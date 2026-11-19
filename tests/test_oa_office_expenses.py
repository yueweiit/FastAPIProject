import json
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
    OaExpenseSourceError,
    _china_salary_totals,
    _office_space_total,
    _shared_admin_expense_month,
    _shared_admin_total,
    china_salary_totals_by_store_and_application_date,
    office_space_totals_by_application_date,
    shared_admin_expenses_by_store_and_month,
)
from services.oa_payroll import payroll_supplements_by_store


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
    async def test_payroll_supplements_use_the_filing_month_and_dedicated_department_rows(self):
        december, january = date(2026, 12, 1), date(2027, 1, 1)
        def approval(key, filed, *fields):
            return {"process_instance_id": key, "submitted": filed, "form_component_values": list(fields)}

        tax = {"name": "税费Impuestos", "value": "薪资税/个税Renta"}
        def detail(shop, amount):
            return [{"id": "DepartmentField_1", "value": shop}, {"id": "MoneyField_T", "value": amount}]

        generic = {"name": "明细detalle", "details": [detail("A", "771.90")]}
        legacy = {"name": "薪酬税费总支出(分部门)", "details": [detail("A", "10.23"), detail("A", "-0.02"), detail("B", "120")]}
        fund = {"name": "管理支出Gastos de operación", "value": "社保公积金seguridad social y fondo de vivienda"}
        table = {"id": "TableField_G2ELEALN0S80", "value": [
            {"rowValue": [{"label": "部门", "value": "A"}, {"key": "MoneyField_6TQM4CNAHYO0", "value": "900"},
                          {"key": "MoneyField_X5KBWAODJ1S0", "value": "931.10"},
                          {"key": "MoneyField_IR71DYOSO8O0", "value": "31.10"}]},
            {"rowValue": [{"label": "部门", "value": "B"}, {"label": "金额", "value": "500"}]},
            {"rowValue": [{"label": "部门", "value": "C"}, {"label": "社保+公积金（公司）", "currency": "USD", "value": "4"}]},
            {"rowValue": [{"label": "部门", "value": "B"}, {"label": "社保+公积金（个人）", "value": "18"}]},
            {"values": [{"label": "部门", "value": "D"}, {"label": "社保+公积金（公司）", "value": "1e101"}]},
        ]}
        with patch("services.oa_payroll._fetch_oa_forms", new=AsyncMock(return_value=[
            approval("tax", january, tax, generic, legacy),
            approval("tax-generic", january, tax, {"name": "薪酬税费总支出", "value": [{"rowValue": []}]},
                     {"name": "明细detalle", "details": [detail("A", "1.20")]}),
            approval("fund", december, fund, table, generic),
            approval("wrong-month", january, fund, table),
            approval("missing-date", None, tax, generic),
            approval("corporate-tax", january, {"name": "税费Impuestos", "value": "企业所得税"}, generic),
        ])) as fetch:
            result = await payroll_supplements_by_store(december, january)
        sql, start, end = fetch.await_args.args
        self.assertEqual((start, end), (december, january))
        for clause in ("source.corp_id = $1", "source.process_code = $2", "source.deleted_at IS NULL",
                       "'COMPLETED'", "source.result", "Asia/Shanghai", "interval '1 month'"):
            self.assertIn(clause, sql)
        self.assertEqual(result["individual_income_tax"], {"A": {december: Decimal("11.41")}, "B": {december: Decimal("120")}})
        self.assertEqual(result["employer_contributions"], {"A": {december: Decimal("31.10")}})
        self.assertCountEqual([(w.code, w.month, w.departmentNames) for w in result["expense_review_warnings"]], [
            ("PAYROLL_DETAIL_UNRESOLVED", "2026-12", ["B", "D"]),
            ("EXPENSE_CURRENCY_UNSUPPORTED", "2026-12", ["C"]),
            ("APPLICATION_MONTH_UNRESOLVED", None, ["A"]),
        ])

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
                    "OA_EXPENSE_API_CORP_ID": "corp-a",
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
        self.assertIn("source.corp_id = $1", query)
        self.assertEqual(args, ["corp-a", OA_OPERATION_PROCESS_CODE, OA_SHARED_ADMIN_COMPONENT])

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
                    "OA_EXPENSE_API_CORP_ID": "corp-a",
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
        self.assertIn("source.corp_id = $1", query_args[0])
        self.assertEqual(query_args[1:], ("corp-a", OA_OPERATION_PROCESS_CODE, date(2026, 9, 1),
                                         date(2026, 10, 1), OA_OFFICE_SPACE_EXPENSE))

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
                    "OA_EXPENSE_API_CORP_ID": "corp-a",
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
        self.assertEqual(connection.fetch.await_args.args[5], OA_CHINA_SALARY_EXPENSE)

    async def test_configured_database_requires_a_corp_before_opening_a_connection(self):
        with (
            patch("services.oa_office_expenses._oa_database_config", return_value={"host": "localhost"}),
            patch.dict("os.environ", {"OA_EXPENSE_API_CORP_ID": "  "}),
            patch("services.oa_office_expenses.asyncpg.connect", new_callable=AsyncMock) as connect,
        ):
            with self.assertRaisesRegex(OaExpenseSourceError, "OA_EXPENSE_API_CORP_ID"):
                await payroll_supplements_by_store(date(2026, 10, 1), date(2026, 11, 1))
            connect.assert_not_awaited()

    async def test_approved_payroll_rows_use_one_representation_and_warn_on_uncertain_values(self):
        october, november = date(2026, 10, 1), date(2026, 11, 1)
        identities = [{"id": "1", "name": "店 A"}, {"id": "2", "name": "店 B"}]

        def cells(department, amount, **metadata):
            return [{"id": "DepartmentField_store", "value": department, **metadata},
                    {"id": "MoneyField_tax", "value": amount}]

        def tax(instance, value, *, dedicated=None, **table):
            fields = [
                {"name": "税费Impuestos", "value": "个税"},
                {"name": "明细detalle", "value": json.dumps(value), **table},
            ]
            if dedicated is not None:
                fields.append({"name": "薪酬税费总支出", "value": [{"rowValue": dedicated}]})
            return {"process_instance_id": instance, "submitted": november, "form_component_values": fields}

        rows = [
            tax("single", [{"values": cells(json.dumps(identities[:1]), "100", extendValue=identities[:1])}]),
            tax("ids", [{"rowValue": [], "value": cells("2", "20", extValue=json.dumps(identities[1]))}]),
            tax("ambiguous", [{"rowValue": cells("", "700", extendValue=identities)}]),
            tax("id-only", [{"rowValue": cells("999", "400")}]),
            tax("conflict", [{"rowValue": cells("店 A", "800", extValue=identities[1])}]),
            tax("mirrors", [{"rowValue": cells("店 A", None)}, {"rowValue": cells("店 B", "5")}],
                details=[{"value": cells("店 A", "4")}, {"values": cells("店 B", None)}]),
            tax("twice-in-one-store", {"rows": [{"values": cells("店 A", "1")}, {"value": cells("店 A", "1")}]},
                details=[cells("店 A", "1"), cells("店 A", "1")]),
            tax("fallback", [{"rowValue": cells("店 A", "10")}], dedicated=cells("[]", "0.00")),
            tax("real-zero", [{"rowValue": cells("店 A", "99")}], dedicated=cells("店 B", 0)),
            tax("no-shop", [{"rowValue": cells("店 A", "99")}], dedicated=cells("", "77")),
            tax("precision", [{"rowValue": cells("店 A", "1e101")}, {"rowValue": cells("店 A", "0.50")},
                              {"rowValue": cells("店 B", 9007199254740992)}]),
        ]
        with patch("services.oa_payroll._fetch_oa_forms", new=AsyncMock(return_value=rows)):
            values = await payroll_supplements_by_store(october, november)
        self.assertEqual(values["individual_income_tax"], {
            "店 A": {october: Decimal("116.50")}, "店 B": {october: Decimal("25")},
        })
        self.assertCountEqual([(w.processInstanceId, w.departmentNames, w.code, w.month)
                               for w in values["expense_review_warnings"]], [
            (instance, shops, "PAYROLL_DETAIL_UNRESOLVED", "2026-10")
            for instance, shops in (("ambiguous", []), ("id-only", []), ("conflict", []), ("no-shop", []), ("precision", ["店 A", "店 B"]))
        ])

    async def test_payroll_mirrors_match_unique_rows_and_retain_currency(self):
        october, november = date(2026, 10, 1), date(2026, 11, 1)

        def detail(amount, number=None, **metadata):
            return {"rowNumber": number, "values": [
                {"label": "部门", "value": "店 A"}, {"label": "金额", "value": amount, **metadata},
            ]}

        def approval(instance, archived, current=None):
            return {"process_instance_id": instance, "submitted": november, "form_component_values": [
                {"name": "税费", "value": "个税"},
                {"name": "明细", "value": {"rows": archived}, "details": current},
            ]}

        forms = [
            approval("ordered", [detail("3", 2), detail("8", 1)], [detail("8", 1), detail(None, 2)]),
            approval("unpaired", [detail("10"), detail("20")], [detail("10"), detail(None)]),
            approval("old-only", [detail("2")]),
            approval("currency", [detail("40", currency="USD")], [detail("40")]),
        ]
        with patch("services.oa_payroll._fetch_oa_forms", new=AsyncMock(return_value=forms)):
            result = await payroll_supplements_by_store(october, november)
        self.assertEqual(result["individual_income_tax"], {"店 A": {october: Decimal("23")}})
        self.assertCountEqual([(w.processInstanceId, w.code, w.departmentNames) for w in result["expense_review_warnings"]], [
            ("unpaired", "PAYROLL_DETAIL_UNRESOLVED", ["店 A"]),
            ("currency", "EXPENSE_CURRENCY_UNSUPPORTED", ["店 A"]),
        ])


if __name__ == "__main__":
    unittest.main()
