"""Read monthly OA expense totals for FIFO reports."""

import os
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, NotRequired, TypedDict

import httpx
from pydantic import BaseModel, Field, ValidationError

from services.oa_office_expenses import OaExpenseSourceError


class _ExpenseRow(BaseModel):
    expenseType: Literal["office_space", "china_salary", "shared_admin"]
    month: str = Field(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    departmentName: str | None
    amount: str = Field(pattern=r"^-?[0-9]+(\.[0-9]+)?$")
    currency: Literal["CNY"]
    approvalCount: int = Field(gt=0)


class _ExpenseWarning(BaseModel):
    code: Literal["EXPENSE_MONTH_UNRESOLVED", "APPLICATION_MONTH_UNRESOLVED", "EXPENSE_CURRENCY_UNSUPPORTED"]
    expenseType: Literal["office_space", "china_salary", "shared_admin"]
    processInstanceId: str = Field(min_length=1)
    departmentNames: list[str]
    month: str | None = Field(default=None, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")


class OperatingExpenseValues(TypedDict):
    office: dict[date, Decimal]
    salaries: dict[str, dict[date, Decimal]]
    shared_admin_by_store: dict[str, dict[date, Decimal]]
    shared_admin_unresolved: dict[str, int]
    expense_review_warnings: NotRequired[list[_ExpenseWarning]]


class _ExpenseResponse(BaseModel):
    success: Literal[True]
    corpId: str
    startMonth: str
    endMonth: str
    data: list[_ExpenseRow]
    warnings: list[_ExpenseWarning]


def operating_expense_http_enabled() -> bool:
    return bool(os.getenv("OA_EXPENSE_API_BASE_URL", "").strip())


_EXPENSE_NAMES = {
    "office_space": "办公场地总费用",
    "china_salary": "工资中国",
    "shared_admin": "平摊人事+财务管理费用",
}
_REVIEW_REASONS = {
    "APPLICATION_MONTH_UNRESOLVED": "申请月份待核对",
    "EXPENSE_CURRENCY_UNSUPPORTED": "币种待核对",
}


def operating_expense_review_messages(values: OperatingExpenseValues, store_name: str | None,
                                      active_store_count: int) -> list[str]:
    groups: defaultdict[tuple[str, str, str], set[str]] = defaultdict(set)
    for warning in values.get("expense_review_warnings", []):
        relevant = bool(active_store_count) if warning.expenseType == "office_space" else store_name in warning.departmentNames
        if relevant:
            groups[warning.month or "", warning.expenseType, warning.code].add(warning.processInstanceId)
    return [
        f"{month + ' ' if month else ''}有{len(ids)}张 OA 审批的{_EXPENSE_NAMES[kind]}"
        f"{_REVIEW_REASONS[code]}，相关金额未纳入 OA 汇总。"
        for (month, kind, code), ids in sorted(groups.items())
    ]


async def read_operating_expenses(start: date, end: date) -> OperatingExpenseValues:
    """Map the API's inclusive months to the report's half-open date range."""
    base_url = os.getenv("OA_EXPENSE_API_BASE_URL", "").strip()
    corp_id = os.getenv("OA_EXPENSE_API_CORP_ID", "").strip()
    token = os.getenv("OA_EXPENSE_API_TOKEN", "").strip()
    if not all((base_url, corp_id, token)):
        raise OaExpenseSourceError("OA 运营费用接口配置不完整")
    if start.day != 1 or end.day != 1 or start >= end:
        raise OaExpenseSourceError("OA 运营费用接口需要完整月份区间")

    start_month = start.isoformat()[:7]
    end_month = (end - timedelta(days=1)).isoformat()[:7]
    try:
        base = httpx.URL(base_url)
        if base.scheme not in {"http", "https"} or not base.host or base.userinfo or base.query or base.fragment:
            raise ValueError("Invalid OA expense API base URL")
        url = base_url.rstrip("/") + "/api/v1/reporting/operating-expenses"
        async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = await client.get(
                url,
                params={"corpId": corp_id, "startMonth": start_month, "endMonth": end_month},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            payload = _ExpenseResponse.model_validate_json(response.content, strict=True)
        if (payload.corpId, payload.startMonth, payload.endMonth) != (corp_id, start_month, end_month):
            raise ValueError("OA expense response does not match request")

        values: OperatingExpenseValues = {
            "office": {}, "salaries": {}, "shared_admin_by_store": {}, "shared_admin_unresolved": {},
        }
        seen = set()
        for row in payload.data:
            key = (row.expenseType, row.month, row.departmentName)
            if key in seen or not start_month <= row.month <= end_month:
                raise ValueError("Duplicate or out-of-range OA expense row")
            seen.add(key)
            month = date.fromisoformat(row.month + "-01")
            amount = Decimal(row.amount)
            if row.expenseType == "office_space":
                if row.departmentName is not None:
                    raise ValueError("Unexpected office expense department")
                values["office"][month] = amount
            else:
                department = row.departmentName
                if not department or department != department.strip():
                    raise ValueError("Invalid OA expense department")
                bucket = values["salaries"] if row.expenseType == "china_salary" else values["shared_admin_by_store"]
                bucket.setdefault(department, {})[month] = amount

        unresolved: defaultdict[str, set[str]] = defaultdict(set)
        review_warnings = []
        for warning in payload.warnings:
            if (warning.expenseType == "office_space") != (not warning.departmentNames):
                raise ValueError("Invalid OA expense warning scope")
            for department in warning.departmentNames:
                if not department or department != department.strip():
                    raise ValueError("Invalid OA expense warning department")
            if warning.code == "EXPENSE_MONTH_UNRESOLVED":
                if warning.expenseType != "shared_admin" or warning.month is not None:
                    raise ValueError("Invalid OA expense month warning")
                for department in warning.departmentNames:
                    unresolved[department].add(warning.processInstanceId)
            elif warning.code == "APPLICATION_MONTH_UNRESOLVED":
                if warning.expenseType == "shared_admin" or warning.month is not None:
                    raise ValueError("Invalid OA application month warning")
                review_warnings.append(warning)
            elif warning.month is None or not start_month <= warning.month <= end_month:
                raise ValueError("Invalid OA expense currency warning month")
            else:
                review_warnings.append(warning)
        values["shared_admin_unresolved"] = {name: len(ids) for name, ids in unresolved.items()}
        if review_warnings:
            values["expense_review_warnings"] = review_warnings
        return values
    except (httpx.HTTPError, httpx.InvalidURL, ValidationError, ValueError):
        # Avoid exposing the response body or credentials through validation errors.
        raise OaExpenseSourceError("无法读取 OA 运营费用接口") from None
