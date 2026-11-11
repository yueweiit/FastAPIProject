import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from routers.reports import _load_profit_loss_oa_values, _store_profit_loss_auto_values
from services.oa_office_expenses import OaExpenseSourceError
from services.oa_operating_expenses import operating_expense_review_messages, read_operating_expenses


class OaOperatingExpensesHttpTests(unittest.IsolatedAsyncioTestCase):
    env = {
        "OA_EXPENSE_API_BASE_URL": "https://oa.example.invalid/oa/",
        "OA_EXPENSE_API_CORP_ID": "corp-1",
        "OA_EXPENSE_API_TOKEN": "fixture-token",
    }

    async def test_report_fetches_three_categories_once_for_january_and_previous_december(self):
        def handler(request):
            self.assertEqual(request.url.path, "/oa/api/v1/reporting/operating-expenses")
            self.assertEqual(dict(request.url.params), {
                "corpId": "corp-1", "startMonth": "2026-12", "endMonth": "2027-01",
            })
            self.assertEqual(request.headers["Authorization"], "Bearer fixture-token")
            return httpx.Response(200, json={
                "success": True, "corpId": "corp-1", "startMonth": "2026-12", "endMonth": "2027-01",
                "data": [
                    {"expenseType": "office_space", "month": "2026-12", "departmentName": None,
                     "amount": "4207.24", "currency": "CNY", "approvalCount": 1},
                    {"expenseType": "china_salary", "month": "2027-01", "departmentName": "店铺 A",
                     "amount": "1300.00", "currency": "CNY", "approvalCount": 1},
                    {"expenseType": "shared_admin", "month": "2026-12", "departmentName": "店铺 A",
                     "amount": "1217.33", "currency": "CNY", "approvalCount": 1},
                ],
                "warnings": [
                    {"code": "EXPENSE_MONTH_UNRESOLVED", "expenseType": "shared_admin",
                     "processInstanceId": "unresolved-1", "departmentNames": ["店铺 A", "店铺 B"]},
                ],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with (
            patch.dict("os.environ", self.env),
            patch("services.oa_operating_expenses.httpx.AsyncClient", return_value=client) as factory,
            patch("routers.reports.office_space_totals_by_application_date", new_callable=AsyncMock) as old_source,
        ):
            values = await _load_profit_loss_oa_values(date(2027, 1, 1), 2, True)
        factory.assert_called_once()
        old_source.assert_not_awaited()
        self.assertEqual(values, {
            "office": {date(2026, 12, 1): Decimal("4207.24")},
            "salaries": {"店铺 A": {date(2027, 1, 1): Decimal("1300.00")}},
            "shared_admin_by_store": {"店铺 A": {date(2026, 12, 1): Decimal("1217.33")}},
            "shared_admin_unresolved": {"店铺 A": 1, "店铺 B": 1},
        })

    async def test_http_failure_does_not_fall_back_to_old_database(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(503, json={"success": False})
        ))
        with (
            patch.dict("os.environ", self.env),
            patch("services.oa_operating_expenses.httpx.AsyncClient", return_value=client),
            patch("routers.reports.office_space_totals_by_application_date", new_callable=AsyncMock) as old_source,
            self.assertRaises(OaExpenseSourceError),
        ):
            await _load_profit_loss_oa_values(date(2026, 11, 1), 1, True)
        old_source.assert_not_awaited()

    async def test_report_applies_http_shared_expenses_to_the_matched_store_without_dividing(self):
        month = date(2026, 11, 1)
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [])
        )))
        with (
            patch("routers.reports.unlinked_settlement_events", new=AsyncMock(return_value=[])),
            patch("routers.reports.load_cost_layers", new=AsyncMock(return_value={})),
            patch("routers.reports._store_provision_financial_values", new=AsyncMock(return_value={})),
            patch("services.report_exchange_rates.monthly_report_rates", new=AsyncMock(return_value={})),
        ):
            values = await _store_profit_loss_auto_values(db, 1, month, "店铺 A", active_store_count=2, oa_values={
                "office": {month: Decimal("300")},
                "salaries": {"店铺 A": {month: Decimal("100")}},
                "shared_admin_by_store": {"店铺 A": {month: Decimal("80")}, "店铺 B": {month: Decimal("50")}},
                "shared_admin_unresolved": {"店铺 A": 1},
            })
        self.assertEqual(values["rent_utilities"]["current"], Decimal("150"))
        self.assertEqual(values["shared_admin"]["current"], Decimal("80"))
        self.assertEqual(values["salary"]["current"], Decimal("100"))
        self.assertTrue(any("所属年月待核对" in message for message in values["_warnings"]))

    async def test_rejects_response_for_another_enterprise(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={
                "success": True, "corpId": "other", "startMonth": "2026-11", "endMonth": "2026-11",
                "data": [], "warnings": [],
            })
        ))
        with (
            patch.dict("os.environ", self.env),
            patch("services.oa_operating_expenses.httpx.AsyncClient", return_value=client),
            self.assertRaises(OaExpenseSourceError),
        ):
            await read_operating_expenses(date(2026, 11, 1), date(2026, 12, 1))

    async def test_expense_review_warnings_apply_to_the_matching_store(self):
        office_warning = {
            "code": "APPLICATION_MONTH_UNRESOLVED", "expenseType": "office_space",
            "processInstanceId": "office-1", "departmentNames": [],
        }
        salary_warning = {
            "code": "EXPENSE_CURRENCY_UNSUPPORTED", "expenseType": "china_salary",
            "processInstanceId": "salary-1", "departmentNames": ["店铺 A"], "month": "2026-11",
        }
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={
                "success": True, "corpId": "corp-1", "startMonth": "2026-11", "endMonth": "2026-11",
                "data": [
                    {"expenseType": "china_salary", "month": "2026-11", "departmentName": "店铺 A",
                     "amount": "20.10", "currency": "CNY", "approvalCount": 1},
                ],
                "warnings": [office_warning, salary_warning, salary_warning],
            })
        ))
        with (
            patch.dict("os.environ", self.env),
            patch("services.oa_operating_expenses.httpx.AsyncClient", return_value=client),
        ):
            values = await read_operating_expenses(date(2026, 11, 1), date(2026, 12, 1))
        self.assertEqual(values["salaries"], {"店铺 A": {date(2026, 11, 1): Decimal("20.10")}})
        office_message = "有1张 OA 审批的办公场地总费用申请月份待核对，相关金额未纳入 OA 汇总。"
        salary_message = "2026-11 有1张 OA 审批的工资中国币种待核对，相关金额未纳入 OA 汇总。"
        self.assertEqual(operating_expense_review_messages(values, "店铺 A", 2), [office_message, salary_message])
        self.assertEqual(operating_expense_review_messages(values, "店铺 B", 2), [office_message])
        self.assertEqual(operating_expense_review_messages(values, "店铺 B", 0), [])
