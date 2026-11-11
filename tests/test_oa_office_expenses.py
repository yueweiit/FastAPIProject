import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from services.oa_office_expenses import (
    OA_CHINA_SALARY_EXPENSE,
    OA_EXPENSE_MONTH_COMPONENT,
    OA_OFFICE_SPACE_EXPENSE,
    OA_OPERATION_PROCESS_CODE,
    OA_SHARED_ADMIN_COMPONENT,
    _china_salary_totals,
    _office_space_total,
    _shared_admin_expense_month,
    _shared_admin_total,
    china_salary_totals_by_store_and_application_date,
    office_space_totals_by_application_date,
    shared_admin_expenses_by_store_and_month,
)


def _row(department_id: str, detail: str, amount: str) -> dict:
    return {
        "rowValue": [
            {
                "label": "部门",
                "extendValue": [{"id": department_id}],
                "value": "部门名称",
            },
            {"label": "费用明细Detalle de gastos", "value": detail},
            {"label": "金额（元）Monto (yuan)", "value": amount},
        ]
    }


class OaOfficeExpenseTests(unittest.TestCase):
    def test_shared_admin_total_uses_only_non_zero_exact_name_components(self):
        self.assertEqual(_shared_admin_total([
            {"name": OA_SHARED_ADMIN_COMPONENT, "value": "1,217.33"},
            {"label": OA_SHARED_ADMIN_COMPONENT, "value": "0"},
            {"name": f"其他{OA_SHARED_ADMIN_COMPONENT}", "value": "999"},
            {"name": OA_SHARED_ADMIN_COMPONENT, "value": "1,21.33"},
            {"name": OA_SHARED_ADMIN_COMPONENT, "value": "NaN"},
        ]), Decimal("1217.33"))

    def test_shared_admin_month_requires_a_clear_year_and_month(self):
        description_name = "事项说明Explicación de asuntos"
        for explicit, description, expected in (
            ("2026-10", "2026年11月付款", date(2026, 10, 1)),
            ("2026-10-31", "", date(2026, 10, 1)),
            ("", "1.2026年10月薪资。2.10月份补缴差额。", date(2026, 10, 1)),
            ("", "2026年10月薪资，2026年10月平摊费用。", date(2026, 10, 1)),
            ("", "2026年10月和2026年11月的费用", None),
            ("", "10月薪资", None),
            ("2026-02-30", "2026年2月薪资", None),
        ):
            with self.subTest(explicit=explicit, description=description):
                self.assertEqual(_shared_admin_expense_month([
                    {"name": OA_EXPENSE_MONTH_COMPONENT, "value": explicit},
                    {"name": description_name, "value": description},
                    {"name": "申请日期Fecha de solicitud", "value": "2026-11-11"},
                ]), expected)

    def test_only_latin_go_rent_and_electricity_rows_are_counted(self):
        form_component_values = [
            {
                "id": "TableField_9KUR3Y1BQYW0",
                "value": [
                    _row("1089990115", "租金Alquiler", "3470.77"),
                    _row("1089990115", "电费Electricidad", "736.47"),
                    _row("1089990115", "物业管理费", "500"),
                    _row("1089928990", "租金Alquiler", "2313.85"),
                ],
            },
            {
                "id": "TableField_other",
                "value": [_row("1089990115", "租金Alquiler", "999")],
            },
        ]

        self.assertEqual(
            _office_space_total(form_component_values),
            Decimal("4207.24"),
        )

    def test_china_salary_rows_are_grouped_by_department_name(self):
        form_component_values = [{
            "label": "明细Detalle",
            "value": [
                _row("1", "工资", "1200.50"),
                _row("2", "工资", "300"),
                _row("1", "奖金", "99.50"),
            ],
        }, {
            "label": "其他表格",
            "value": [_row("1", "其他", "9999")],
        }]
        form_component_values[0]["value"][0]["rowValue"][0]["value"] = "店铺A"
        form_component_values[0]["value"][1]["rowValue"][0]["value"] = "店铺B"
        form_component_values[0]["value"][2]["rowValue"][0]["value"] = "店铺A"

        self.assertEqual(_china_salary_totals(form_component_values), {
            "店铺A": Decimal("1300.00"),
            "店铺B": Decimal("300"),
        })


class OaOfficeExpenseDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_admin_totals_use_expense_month_even_when_submitted_later(self):
        departments = ("测试店铺 A", "测试店铺 B", "测试店铺 C", "测试店铺 C")
        details = {
            "label": "明细detalle",
            "value": [
                {"rowValue": [
                    {"label": "部门Departamento", "value": name},
                    {"label": "备注Nota", "value": "2人" if name == "测试店铺 C" else "1人"},
                ]}
                for name in departments
            ],
        }
        def approved_form(amount, submitted_on, description, include_departments=True):
            components = [
                {"name": OA_SHARED_ADMIN_COMPONENT, "value": amount},
                {"name": "申请日期Fecha de solicitud", "value": submitted_on},
                {"name": "事项说明Explicación de asuntos", "value": description},
            ]
            if include_departments:
                components.append(details)
            return {"form_component_values": components}

        connection = AsyncMock()
        connection.fetch.return_value = [
            approved_form("1,217.33", "2026-11-11", "1.2026年10月测试店铺薪资。2.10月份补缴差额。"),
            approved_form("1500", "2026-12-12", "2026年11月薪资"),
            approved_form("200", "2027-01-12", "2026年12月薪资"),
            approved_form("100", "2026-10-11", "2026年9月薪资"),
            approved_form("250", "2026-11-13", "费用月份待核对"),
            approved_form("0", "2026-11-11", ""),
            approved_form("600", "2026-11-11", "2026年10月", include_departments=False),
        ]

        with (
            patch.dict(
                "os.environ",
                {
                    "OA_DB_HOST": "oa.example.test",
                    "OA_DB_DATABASE": "dingtalk_oa",
                    "OA_DB_USER": "readonly",
                    "OA_DB_PASSWORD": "test-password",
                },
                clear=False,
            ),
            patch(
                "services.oa_office_expenses.asyncpg.connect",
                new=AsyncMock(return_value=connection),
            ),
        ):
            october, unresolved = await shared_admin_expenses_by_store_and_month(
                date(2026, 10, 1), date(2026, 11, 1)
            )
            later, _ = await shared_admin_expenses_by_store_and_month(
                date(2026, 11, 1), date(2027, 1, 1)
            )

        self.assertEqual(october, {
            name: {date(2026, 10, 1): Decimal("1217.33")}
            for name in set(departments)
        })
        self.assertEqual(later, {
            name: {date(2026, 11, 1): Decimal("1500"), date(2026, 12, 1): Decimal("200")}
            for name in set(departments)
        })
        self.assertEqual(unresolved, dict.fromkeys(departments, 1))
        query, *args = connection.fetch.await_args.args
        self.assertNotIn("申请日期", query)
        self.assertEqual(args, [OA_OPERATION_PROCESS_CODE, OA_SHARED_ADMIN_COMPONENT])

    async def test_query_binds_dates_as_date_objects(self):
        connection = AsyncMock()
        connection.fetch.return_value = []

        with (
            patch.dict(
                "os.environ",
                {
                    "OA_DB_HOST": "oa.example.test",
                    "OA_DB_DATABASE": "dingtalk_oa",
                    "OA_DB_USER": "readonly",
                    "OA_DB_PASSWORD": "test-password",
                },
                clear=False,
            ),
            patch("services.oa_office_expenses.asyncpg.connect", new=AsyncMock(return_value=connection)),
        ):
            result = await office_space_totals_by_application_date(
                date(2026, 9, 1), date(2026, 10, 1)
            )

        self.assertEqual(result, {})
        query_args = connection.fetch.await_args.args
        self.assertEqual(query_args[1], OA_OPERATION_PROCESS_CODE)
        self.assertEqual(query_args[2], date(2026, 9, 1))
        self.assertEqual(query_args[3], date(2026, 10, 1))
        self.assertEqual(query_args[4], OA_OFFICE_SPACE_EXPENSE)

    async def test_salary_totals_keep_store_name_and_application_date(self):
        connection = AsyncMock()
        connection.fetch.return_value = [{
            "request_date": "2026-09-10",
            "form_component_values": [{
                "label": "明细Detalle",
                "value": [_row("1", "工资", "888.88")],
            }],
        }]
        connection.fetch.return_value[0]["form_component_values"][0]["value"][0][
            "rowValue"
        ][0]["value"] = "TikTok墨西哥店"

        with (
            patch.dict(
                "os.environ",
                {
                    "OA_DB_HOST": "oa.example.test",
                    "OA_DB_DATABASE": "dingtalk_oa",
                    "OA_DB_USER": "readonly",
                    "OA_DB_PASSWORD": "test-password",
                },
                clear=False,
            ),
            patch(
                "services.oa_office_expenses.asyncpg.connect",
                new=AsyncMock(return_value=connection),
            ),
        ):
            result = await china_salary_totals_by_store_and_application_date(
                date(2026, 9, 1), date(2026, 10, 1)
            )

        self.assertEqual(result, {
            "TikTok墨西哥店": {date(2026, 9, 10): Decimal("888.88")},
        })
        self.assertEqual(connection.fetch.await_args.args[4], OA_CHINA_SALARY_EXPENSE)


if __name__ == "__main__":
    unittest.main()
