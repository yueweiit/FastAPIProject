import unittest
from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import load_workbook

from routers.reports import (
    INVENTORY_IMPAIRMENT_HEADERS,
    INVENTORY_IMPAIRMENT_SHEET,
    _build_inventory_impairment_workbook,
    _previous_month_end,
    _workbook_bytes_with_formula_cache,
)


class InventoryImpairmentReportTests(unittest.TestCase):
    def test_previous_month_end_handles_year_boundary(self):
        self.assertEqual(_previous_month_end(date(2026, 1, 15)), date(2025, 12, 31))
        self.assertEqual(_previous_month_end(date(2026, 9, 30)), date(2026, 8, 31))

    def test_workbook_matches_first_template_sheet_and_formulas(self):
        workbook, formula_cache = _build_inventory_impairment_workbook([
            {
                "store_name": "测试店铺",
                "sku": "SKU-001",
                "product_name": "测试商品",
                "arrived_at": date(2026, 8, 1),
                "unit_cost": Decimal("12.5"),
                "quantity": 2,
                "previous_month_quantity": 0,
                "batch_no": "BATCH-001",
            }
        ], date(2026, 8, 31))

        output = _workbook_bytes_with_formula_cache(workbook, formula_cache)
        saved = load_workbook(output, data_only=False)
        sheet = saved[INVENTORY_IMPAIRMENT_SHEET]

        self.assertEqual(saved.sheetnames, [INVENTORY_IMPAIRMENT_SHEET])
        self.assertEqual(sheet.freeze_panes, "A2")
        self.assertEqual([cell.value for cell in sheet[1]], INVENTORY_IMPAIRMENT_HEADERS)
        self.assertEqual(sheet["E2"].value, '=IF(D2="","",DATE(2026,8,31)-D2)')
        self.assertEqual(sheet["H2"].value, '=IF(OR(F2="",G2=""),"",F2*G2)')
        self.assertEqual(sheet["I2"].value, '=IF(E2="","",MIN(E2*0.01,0.9))')
        self.assertEqual(sheet["J2"].value, '=IF(OR(H2="",I2=""),"",H2*I2)')
        self.assertEqual(sheet["K2"].value, '=IF(OR(H2="",J2=""),"",H2-J2)')
        self.assertEqual(
            sheet["L2"].value,
            '=IF(OR(D2="",F2="",K2=""),"",K2-F2*0*(1-MIN(MAX(DATE(2026,7,31)-D2,0)*0.01,0.9)))',
        )
        self.assertIn("I2>=0.6", sheet["M2"].value)
        self.assertEqual(sheet["E3"].value, "=SUM(E2:E2)")
        self.assertEqual(sheet["F3"].value, "=SUM(F2:F2)")
        self.assertEqual(sheet["G3"].value, "=SUM(G2:G2)")
        self.assertEqual(sheet["L3"].value, "=SUM(L2:L2)")
        self.assertEqual(sheet["A1"].fill.fgColor.rgb, "00C00000")

        output.seek(0)
        cached_sheet = load_workbook(output, data_only=True)[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(cached_sheet["E2"].value, 30)
        self.assertEqual(cached_sheet["H2"].value, 25)
        self.assertEqual(cached_sheet["I2"].value, 0.3)
        self.assertEqual(cached_sheet["J2"].value, 7.5)
        self.assertEqual(cached_sheet["K2"].value, 17.5)
        self.assertEqual(cached_sheet["L2"].value, 17.5)
        self.assertEqual(cached_sheet["M2"].value, "正常计提")
        self.assertEqual(cached_sheet["H3"].value, 25)

    def test_previous_month_book_value_uses_actual_previous_inventory_quantity(self):
        workbook, formula_cache = _build_inventory_impairment_workbook([
            {
                "store_name": "测试店铺",
                "sku": "SKU-002",
                "product_name": "历史库存商品",
                "arrived_at": date(2026, 7, 1),
                "unit_cost": Decimal("12.5"),
                "quantity": 2,
                "previous_month_quantity": 3,
                "batch_no": "BATCH-002",
            }
        ], date(2026, 8, 31))

        formula = workbook[INVENTORY_IMPAIRMENT_SHEET]["L2"].value
        self.assertIn("K2-F2*3", formula)
        self.assertIn("DATE(2026,7,31)-D2", formula)

        output = _workbook_bytes_with_formula_cache(workbook, formula_cache)
        cached_sheet = load_workbook(output, data_only=True)[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(cached_sheet["K2"].value, 9.75)
        self.assertEqual(cached_sheet["L2"].value, -16.5)

    def test_fully_sold_current_batch_keeps_previous_month_reduction(self):
        workbook, formula_cache = _build_inventory_impairment_workbook([
            {
                "store_name": "测试店铺",
                "sku": "SKU-003",
                "product_name": "本月售完商品",
                "arrived_at": date(2026, 7, 1),
                "unit_cost": Decimal("10"),
                "quantity": 0,
                "previous_month_quantity": 5,
                "batch_no": "BATCH-003",
            }
        ], date(2026, 8, 31))

        output = _workbook_bytes_with_formula_cache(workbook, formula_cache)
        cached_sheet = load_workbook(output, data_only=True)[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(cached_sheet["G2"].value, 0)
        self.assertEqual(cached_sheet["K2"].value, 0)
        self.assertEqual(cached_sheet["L2"].value, -35)

    def test_empty_report_has_numeric_totals_without_circular_formulas(self):
        workbook, _ = _build_inventory_impairment_workbook([], date(2026, 8, 31))
        sheet = workbook[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(sheet["A2"].value, "合计")
        for cell in ("E2", "F2", "G2", "H2", "I2", "J2", "K2", "L2"):
            self.assertEqual(sheet[cell].value, 0)


if __name__ == "__main__":
    unittest.main()
