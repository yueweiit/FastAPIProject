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
from services.inventory_impairment import (
    ImpairmentRule,
    InventoryLayer,
    PRODUCT_TYPE_NEW,
    PRODUCT_TYPE_STABLE,
    batch_impairments,
    impairment_rate,
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
        self.assertEqual(sheet["H2"].value, 2)
        self.assertEqual(sheet["I2"].value, '=IF(OR(F2="",G2=""),"",F2*G2)')
        self.assertEqual(sheet["J2"].value, '=IF(E2="","",MIN(E2*0.01,0.9))')
        self.assertEqual(sheet["K2"].value, '=IF(OR(F2="",H2="",J2=""),"",F2*H2*J2)')
        self.assertEqual(sheet["L2"].value, '=IF(OR(I2="",K2=""),"",I2-K2)')
        self.assertEqual(
            sheet["M2"].value,
            '=IF(OR(D2="",F2="",L2=""),"",L2-F2*(0-0*MIN(MAX(DATE(2026,7,31)-D2,0)*0.01,0.9)))',
        )
        self.assertIn("J2>=0.6", sheet["N2"].value)
        self.assertEqual(sheet["E3"].value, "=SUM(E2:E2)")
        self.assertEqual(sheet["F3"].value, "=SUM(F2:F2)")
        self.assertEqual(sheet["G3"].value, "=SUM(G2:G2)")
        self.assertEqual(sheet["M3"].value, "=SUM(M2:M2)")
        self.assertEqual(sheet["A1"].fill.fgColor.rgb, "00C00000")

        output.seek(0)
        cached_sheet = load_workbook(output, data_only=True)[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(cached_sheet["E2"].value, 30)
        self.assertEqual(cached_sheet["H2"].value, 2)
        self.assertEqual(cached_sheet["I2"].value, 25)
        self.assertEqual(cached_sheet["J2"].value, 0.3)
        self.assertEqual(cached_sheet["K2"].value, 7.5)
        self.assertEqual(cached_sheet["L2"].value, 17.5)
        self.assertEqual(cached_sheet["M2"].value, 17.5)
        self.assertEqual(cached_sheet["N2"].value, "正常计提")
        self.assertEqual(cached_sheet["H3"].value, 2)

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

        formula = workbook[INVENTORY_IMPAIRMENT_SHEET]["M2"].value
        self.assertIn("L2-F2*(3-3", formula)
        self.assertIn("DATE(2026,7,31)-D2", formula)

        output = _workbook_bytes_with_formula_cache(workbook, formula_cache)
        cached_sheet = load_workbook(output, data_only=True)[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(cached_sheet["K2"].value, 15.25)
        self.assertEqual(cached_sheet["L2"].value, 9.75)
        self.assertEqual(cached_sheet["M2"].value, -16.5)

    def test_previous_month_book_value_prefers_saved_snapshot(self):
        workbook, formula_cache = _build_inventory_impairment_workbook([
            {
                "store_name": "测试店铺",
                "sku": "SKU-002",
                "product_name": "历史库存商品",
                "arrived_at": date(2026, 7, 1),
                "unit_cost": Decimal("12.5"),
                "quantity": 2,
                "previous_month_quantity": 3,
                "previous_book_value": Decimal("7.5"),
                "impairment_amount": Decimal("0"),
                "batch_no": "BATCH-002",
            }
        ], date(2026, 8, 31))

        sheet = workbook[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(sheet["M2"].value, Decimal("17.5"))

        output = _workbook_bytes_with_formula_cache(workbook, formula_cache)
        cached_sheet = load_workbook(output, data_only=True)[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(cached_sheet["M2"].value, 17.5)

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
        self.assertEqual(cached_sheet["M2"].value, -35)

    def test_empty_report_has_numeric_totals_without_circular_formulas(self):
        workbook, _ = _build_inventory_impairment_workbook([], date(2026, 8, 31))
        sheet = workbook[INVENTORY_IMPAIRMENT_SHEET]
        self.assertEqual(sheet["A2"].value, "合计")
        for cell in ("E2", "F2", "G2", "H2", "I2", "J2", "K2", "L2", "M2"):
            self.assertEqual(sheet[cell].value, 0)

    def test_new_rule_allocates_safe_stock_to_newest_stable_batch(self):
        impairments = batch_impairments([
            InventoryLayer(1, 10, 1, date(2026, 8, 1), 4, PRODUCT_TYPE_STABLE, 7),
            InventoryLayer(2, 10, 2, date(2026, 9, 1), 6, PRODUCT_TYPE_STABLE, 7),
        ], date(2026, 9, 30))

        self.assertEqual(impairments[1].provision_quantity, 3)
        self.assertEqual(impairments[2].provision_quantity, 0)
        self.assertEqual(impairments[1].rate, Decimal("0.6"))

    def test_new_product_uses_half_percent_rate_from_september(self):
        self.assertEqual(
            impairment_rate(date(2026, 9, 30), date(2026, 9, 10), PRODUCT_TYPE_NEW),
            Decimal("0.1"),
        )
        self.assertEqual(
            impairment_rate(date(2026, 8, 31), date(2026, 8, 1), PRODUCT_TYPE_NEW),
            Decimal("0.3"),
        )

    def test_new_product_history_is_preserved_when_all_stock_becomes_safe(self):
        impairments = batch_impairments([
            InventoryLayer(
                1, 10, 1, date(2026, 9, 1), 200, PRODUCT_TYPE_STABLE, 200,
                (
                    ImpairmentRule(PRODUCT_TYPE_NEW, 0, date(2026, 9, 1)),
                    ImpairmentRule(PRODUCT_TYPE_STABLE, 200, date(2026, 9, 11)),
                ),
            ),
        ], date(2026, 9, 20))

        self.assertEqual(impairments[1].provision_quantity, 200)
        self.assertEqual(impairments[1].impairment_units, Decimal("9.000"))
        self.assertEqual(impairments[1].rate, Decimal("0.045"))

    def test_new_product_history_continues_for_stock_outside_safe_quantity(self):
        impairments = batch_impairments([
            InventoryLayer(
                1, 10, 1, date(2026, 9, 1), 200, PRODUCT_TYPE_STABLE, 100,
                (
                    ImpairmentRule(PRODUCT_TYPE_NEW, 0, date(2026, 9, 1)),
                    ImpairmentRule(PRODUCT_TYPE_STABLE, 100, date(2026, 9, 11)),
                ),
            ),
        ], date(2026, 9, 20))

        # 200 units keep the 9 days of new-product provision (4.5%), while
        # the 100 units beyond safe stock add 10 days at 1%.
        self.assertEqual(impairments[1].provision_quantity, 200)
        self.assertEqual(impairments[1].impairment_units, Decimal("19.000"))
        self.assertEqual(impairments[1].rate, Decimal("0.095"))

    def test_mixed_history_caps_safe_and_excess_stock_separately(self):
        impairments = batch_impairments([
            InventoryLayer(
                1, 10, 1, date(2026, 9, 1), 200, PRODUCT_TYPE_STABLE, 100,
                (
                    ImpairmentRule(PRODUCT_TYPE_NEW, 0, date(2026, 9, 1)),
                    ImpairmentRule(PRODUCT_TYPE_STABLE, 100, date(2027, 2, 8)),
                ),
            ),
        ], date(2027, 4, 18))

        # The safe 100 units remain at 79.5%; the other 100 units reach 90%.
        self.assertEqual(impairments[1].impairment_units, Decimal("169.500"))


if __name__ == "__main__":
    unittest.main()
