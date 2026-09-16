import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from services.oa_office_expenses import (
    OA_OFFICE_SPACE_EXPENSE,
    OA_OPERATION_PROCESS_CODE,
    _office_space_total,
    office_space_totals_by_application_date,
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


class OaOfficeExpenseDatabaseTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
