"""Read approved expense data from the OA database."""

import json
import logging
import os
import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg


logger = logging.getLogger(__name__)

OA_OPERATION_PROCESS_CODE = "PROC-E7BC3316-E618-4812-BDCC-7A655A7C694B"
OA_OFFICE_SPACE_EXPENSE = "办公场地总费用Gastos de local de oficinas"
OA_CHINA_SALARY_EXPENSE = "工资中国Salario en China"
OA_SHARED_ADMIN_COMPONENT = "平摊人事+财务管理费用"
OA_EXPENSE_MONTH_COMPONENT = "费用所属月份"
OA_MATTER_DESCRIPTION_FIELDS = {"事项说明", "事项说明Explicación de asuntos"}
OA_OFFICE_SPACE_TABLE_ID = "TableField_9KUR3Y1BQYW0"
LATIN_GO_DEPARTMENT_ID = "1089990115"
OFFICE_DETAIL_VALUES = {"租金Alquiler", "电费Electricidad"}

_CHINESE_YEAR_MONTH = re.compile(r"(?<![0-9])([0-9]{4})\s*年\s*([0-9]{1,2})\s*月(?:份)?")
_APPROVED_OPERATION_FILTER = """
    source.deleted_at IS NULL
    AND source.corp_id = $1 AND source.process_code = $2
    AND UPPER(COALESCE(source.status, '')) = 'COMPLETED'
    AND LOWER(COALESCE(source.result, '')) IN ('agree', 'approved', '同意', '通过')
"""


class OaExpenseSourceError(RuntimeError):
    """The configured OA source cannot be read."""


def _oa_database_config() -> dict[str, str] | None:
    required = {
        "host": os.getenv("OA_DB_HOST", "").strip(),
        "database": os.getenv("OA_DB_DATABASE", "").strip(),
        "user": os.getenv("OA_DB_USER", "").strip(),
        "password": os.getenv("OA_DB_PASSWORD", ""),
    }
    if not all(required.values()):
        return None
    required["port"] = os.getenv("OA_DB_PORT", "5432").strip() or "5432"
    return required


def _decode(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith(("[", "{", '"')):
        try:
            return json.loads(text)
        except ValueError:
            pass  # Keep malformed input distinguishable from an empty field.
    return text


def _json_list(value: Any) -> list[dict[str, Any]]:
    parsed = _decode(value)
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _department_id(cell: dict[str, Any] | None) -> str:
    if not isinstance(cell, dict):
        return ""
    identity = cell.get("extendValue")
    if isinstance(identity, list) and identity and isinstance(identity[0], dict):
        return str(identity[0].get("id") or identity[0].get("itemId") or "").strip()
    return ""


def _cell(cells: list[Any], *keywords: str) -> dict[str, Any] | None:
    normalized_keywords = tuple(keyword.casefold() for keyword in keywords)
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        label = str(cell.get("label") or cell.get("name") or "").casefold()
        if any(keyword in label for keyword in normalized_keywords):
            return cell
    return None


def _department_name(cell: dict[str, Any] | None) -> str | None:
    """None denotes an empty selector, an empty string a filled but unresolved one."""
    if not isinstance(cell, dict):
        return None
    names, ids, display_values = set(), set(), set()
    for source in ("value", "extendValue", "extValue"):
        entry = _decode(cell.get(source))
        if isinstance(entry, list):
            if len(entry) > 1:
                return ""
            entry = entry[0] if entry else None
        if entry is None or entry == "":
            continue
        if isinstance(entry, str):
            text = entry.strip()
            if text.startswith(("[", "{", '"')):
                return ""
            if text:
                display_values.add(text)
        elif isinstance(entry, (int, float)) and not isinstance(entry, bool):
            ids.add(str(entry))
        elif isinstance(entry, dict):
            if not entry:
                continue
            raw_id = next((entry[k] for k in ("id", "itemId", "deptId", "dept_id") if entry.get(k) is not None), "")
            raw_name = entry.get("name") or entry.get("label") or ""
            if not isinstance(raw_name, str) or not isinstance(raw_id, (str, int, float)) or isinstance(raw_id, bool):
                return ""
            ident, label = str(raw_id).strip(), raw_name.strip()
            if not ident and not label:
                return ""
            if ident:
                ids.add(ident)
            if label:
                names.add(label)
        else:
            return ""
    for display in display_values - ids:
        if display not in names and re.fullmatch(r"[0-9]+", display):
            ids.add(display)
        else:
            names.add(display)
    if not names and not ids:
        return None
    return next(iter(names)) if len(names) == 1 and len(ids) <= 1 else ""


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return None


def _office_space_total(form_component_values: Any) -> Decimal:
    total = Decimal("0")
    for table in _json_list(form_component_values):
        if table.get("id") != OA_OFFICE_SPACE_TABLE_ID:
            continue
        for row in _json_list(table.get("value")):
            cells = row.get("rowValue")
            if not isinstance(cells, list):
                continue
            department = next(
                (cell for cell in cells if isinstance(cell, dict) and cell.get("label") == "部门"),
                None,
            )
            detail = next(
                (cell for cell in cells if isinstance(cell, dict) and cell.get("label") == "费用明细Detalle de gastos"),
                None,
            )
            amount = next(
                (cell for cell in cells if isinstance(cell, dict) and cell.get("label") == "金额（元）Monto (yuan)"),
                None,
            )
            if (
                _department_id(department) != LATIN_GO_DEPARTMENT_ID
                or not isinstance(detail, dict)
                or detail.get("value") not in OFFICE_DETAIL_VALUES
                or not isinstance(amount, dict)
            ):
                continue
            value = _decimal(amount.get("value"))
            if value is not None:
                total += value
    return total


def _department_details(form_component_values: Any) -> Iterator[tuple[str, list[Any]]]:
    for table in _json_list(form_component_values):
        table_label = str(table.get("label") or table.get("name") or "").casefold()
        if "明细" not in table_label and "detalle" not in table_label:
            continue
        for row in _json_list(table.get("value")):
            cells = row.get("rowValue")
            if not isinstance(cells, list):
                continue
            department = _cell(cells, "部门名称", "部门", "departamento")
            department_name = _department_name(department)
            if department_name:
                yield department_name, cells


def _china_salary_totals(form_component_values: Any) -> dict[str, Decimal]:
    totals: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for department_name, cells in _department_details(form_component_values):
        amount = _cell(cells, "金额", "monto", "importe")
        value = _decimal(amount.get("value")) if amount else None
        if value is not None:
            totals[department_name] += value
    return dict(totals)


def _component_name(component: dict[str, Any]) -> str:
    return str(component.get("name") or component.get("label") or "").strip()


def _shared_admin_total(form_component_values: Any) -> Decimal:
    total = Decimal("0")
    for component in _json_list(form_component_values):
        if _component_name(component) != OA_SHARED_ADMIN_COMPONENT:
            continue
        raw_amount = component.get("value")
        if isinstance(raw_amount, str) and re.fullmatch(
            r"[+-]?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?", raw_amount.strip()
        ):
            raw_amount = raw_amount.replace(",", "")
        value = _decimal(raw_amount)
        if value is not None and value.is_finite():
            total += value
    return total


def _parse_expense_month(value: str) -> date | None:
    chinese_month = _CHINESE_YEAR_MONTH.fullmatch(value)
    if chinese_month:
        year, month = map(int, chinese_month.groups())
        value = f"{year:04d}-{month:02d}"
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        value += "-01"
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return None
    try:
        return date.fromisoformat(value).replace(day=1)
    except ValueError:
        return None


def _shared_admin_expense_month(form_component_values: Any) -> date | None:
    explicit_months = []
    description_months = []
    for component in _json_list(form_component_values):
        name = _component_name(component)
        value = component.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        if name == OA_EXPENSE_MONTH_COMPONENT:
            explicit_months.append(value.strip())
        elif name in OA_MATTER_DESCRIPTION_FIELDS:
            description_months.extend(match.group() for match in _CHINESE_YEAR_MONTH.finditer(value))

    months = {_parse_expense_month(value) for value in (explicit_months or description_months)}
    return months.pop() if len(months) == 1 else None


async def _fetch_oa_forms(query: str, *args: Any) -> list[Any]:
    config = _oa_database_config()
    if config is None:
        logger.warning("OA database is not configured; expense data is unavailable")
        return []
    corp_id = os.getenv("OA_EXPENSE_API_CORP_ID", "").strip()
    if not corp_id:
        raise OaExpenseSourceError("OA 数据库直连缺少企业标识 OA_EXPENSE_API_CORP_ID")
    try:
        connection = await asyncpg.connect(
            host=config["host"],
            port=int(config["port"]),
            database=config["database"],
            user=config["user"],
            password=config["password"],
            command_timeout=10,
        )
        try:
            return list(await connection.fetch(query, corp_id, OA_OPERATION_PROCESS_CODE, *args))
        finally:
            await connection.close()
    except Exception as exc:
        raise OaExpenseSourceError("无法读取 OA 费用数据") from exc


async def _approved_forms_by_expense(
    start_date: date, end_date: date, expense_name: str
) -> list[Any]:
    query = f"""
        SELECT
            request_field.value ->> 'value' AS request_date,
            source.form_component_values
        FROM ding_approval_instance AS source
        JOIN LATERAL jsonb_array_elements(
            COALESCE(source.form_component_values, '[]'::jsonb)
        ) AS request_field(value)
          ON request_field.value ->> 'name' = '申请日期Fecha de solicitud'
        WHERE {_APPROVED_OPERATION_FILTER}
          AND (request_field.value ->> 'value')::date >= $3::date
          AND (request_field.value ->> 'value')::date < $4::date
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(
                  COALESCE(source.form_component_values, '[]'::jsonb)
              ) AS management_field(value)
              WHERE management_field.value ->> 'name' = '管理支出Gastos de operación'
                AND management_field.value ->> 'value' = $5
          )
    """
    return await _fetch_oa_forms(query, start_date, end_date, expense_name)


async def _approved_shared_admin_forms() -> list[Any]:
    # An approval can be submitted after the month (or year) it belongs to.
    query = f"""
        SELECT source.form_component_values
        FROM ding_approval_instance AS source
        WHERE {_APPROVED_OPERATION_FILTER}
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(
                  CASE WHEN jsonb_typeof(source.form_component_values) = 'array'
                       THEN source.form_component_values ELSE '[]'::jsonb END
              ) AS component(value)
              WHERE btrim(COALESCE(NULLIF(component.value ->> 'name', ''),
                                   component.value ->> 'label')) = $3
          )
    """
    return await _fetch_oa_forms(query, OA_SHARED_ADMIN_COMPONENT)


async def office_space_totals_by_application_date(
    start_date: date, end_date: date
) -> dict[date, Decimal]:
    """Return completed, agreed office-space totals keyed by application date.

    An unconfigured source is expected in local development and returns no data.
    A configured source that cannot be read raises instead of silently reporting zero.
    """
    totals: defaultdict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    for row in await _approved_forms_by_expense(
        start_date, end_date, OA_OFFICE_SPACE_EXPENSE
    ):
        try:
            request_date = date.fromisoformat(str(row["request_date"]))
        except (TypeError, ValueError):
            continue
        total = _office_space_total(row["form_component_values"])
        if total:
            totals[request_date] += total
    return dict(totals)


async def china_salary_totals_by_store_and_application_date(
    start_date: date, end_date: date
) -> dict[str, dict[date, Decimal]]:
    """Return China salary totals keyed by exact OA department/store name and date."""
    totals: defaultdict[str, defaultdict[date, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    for row in await _approved_forms_by_expense(
        start_date, end_date, OA_CHINA_SALARY_EXPENSE
    ):
        try:
            request_date = date.fromisoformat(str(row["request_date"]))
        except (TypeError, ValueError):
            continue
        for department_name, amount in _china_salary_totals(
            row["form_component_values"]
        ).items():
            totals[department_name.strip()][request_date] += amount
    return {
        store_name: dict(date_totals)
        for store_name, date_totals in totals.items()
    }


async def shared_admin_expenses_by_store_and_month(
    start_date: date, end_date: date
) -> tuple[dict[str, dict[date, Decimal]], dict[str, int]]:
    """Return dated totals in the requested period and unresolved form counts per store."""
    totals: defaultdict[str, defaultdict[date, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: Decimal("0"))
    )
    unresolved: defaultdict[str, int] = defaultdict(int)
    for row in await _approved_shared_admin_forms():
        form = row["form_component_values"]
        amount = _shared_admin_total(form)
        if not amount:
            continue
        expense_month = _shared_admin_expense_month(form)
        if expense_month is not None and not (start_date <= expense_month < end_date):
            continue
        for department_name in {name for name, _cells in _department_details(form)}:
            if expense_month is None:
                unresolved[department_name] += 1
            else:
                totals[department_name][expense_month] += amount
    return {name: dict(month_totals) for name, month_totals in totals.items()}, dict(unresolved)
