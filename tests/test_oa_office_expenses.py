import unittest
from decimal import Decimal

from services.oa_office_expenses import _office_space_total


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


if __name__ == "__main__":
    unittest.main()
