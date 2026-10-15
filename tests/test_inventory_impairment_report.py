"""原静态准备余额及快照历史回归；现行报表使用持有期间回放。"""
import unittest
from datetime import date
from decimal import Decimal

from routers.reports import _previous_month_end
from services.inventory_impairment import (
    ImpairmentRule, InventoryLayer, PRODUCT_TYPE_NEW, PRODUCT_TYPE_STABLE,
    batch_impairments, impairment_rate,
)


class InventoryImpairmentReportTests(unittest.TestCase):
    def test_monthly_provision_preserves_rule_history_and_safe_stock(self):
        layers = [InventoryLayer(
            1, 10, 1, date(2026, 8, 1), 200, PRODUCT_TYPE_STABLE, 100,
            (
                ImpairmentRule(PRODUCT_TYPE_NEW, 0, date(2026, 8, 1)),
                ImpairmentRule(PRODUCT_TYPE_STABLE, 100, date(2026, 9, 11)),
            ),
        )]
        result = batch_impairments(layers, date(2026, 9, 30), period_start=date(2026, 9, 1))[1]
        self.assertEqual(result.impairment_units, Decimal("60"))
        self.assertEqual(result.period_impairment_units, Decimal("30"))


    def test_monthly_provision_obeys_cap_and_backfilled_first_rule(self):
        capped = batch_impairments([
            InventoryLayer(1, 10, 1, date(2026, 7, 1), 100, PRODUCT_TYPE_STABLE, 0),
        ], date(2026, 9, 30), period_start=date(2026, 9, 1))[1]
        self.assertEqual(capped.impairment_units, Decimal("90"))
        self.assertEqual(capped.period_impairment_units, Decimal("29"))

        backfilled = batch_impairments([
            InventoryLayer(
                1, 10, 1, date(2026, 8, 1), 100, PRODUCT_TYPE_NEW, 0,
                (ImpairmentRule(PRODUCT_TYPE_NEW, 0, date(2026, 9, 22)),),
            ),
        ], date(2026, 9, 30), period_start=date(2026, 9, 1))[1]
        self.assertEqual(backfilled.impairment_units, Decimal("30"))
        self.assertEqual(backfilled.period_impairment_units, Decimal("15"))


    def test_monthly_provision_handles_partial_month_across_year_boundary(self):
        result = batch_impairments([
            InventoryLayer(1, 10, 1, date(2025, 12, 1), 100, PRODUCT_TYPE_NEW, 0),
        ], date(2026, 1, 15), period_start=date(2026, 1, 1))[1]
        self.assertEqual(result.impairment_units, Decimal("22.5"))
        self.assertEqual(result.period_impairment_units, Decimal("7.5"))


    def test_previous_month_end_handles_year_boundary(self):
        self.assertEqual(_previous_month_end(date(2026, 1, 15)), date(2025, 12, 31))
        self.assertEqual(_previous_month_end(date(2026, 9, 30)), date(2026, 8, 31))


    def test_new_rule_allocates_safe_stock_to_newest_stable_batch(self):
        impairments = batch_impairments([
            InventoryLayer(1, 10, 1, date(2026, 8, 1), 4, PRODUCT_TYPE_STABLE, 7),
            InventoryLayer(2, 10, 2, date(2026, 9, 1), 6, PRODUCT_TYPE_STABLE, 7),
        ], date(2026, 9, 30))

        self.assertEqual(impairments[1].provision_quantity, 3)
        self.assertEqual(impairments[2].provision_quantity, 0)
        self.assertEqual(impairments[1].rate, Decimal("0.6"))


    def test_new_product_uses_half_percent_rate_from_arrival(self):
        self.assertEqual(
            impairment_rate(date(2026, 9, 30), date(2026, 9, 10), PRODUCT_TYPE_NEW),
            Decimal("0.1"),
        )
        self.assertEqual(
            impairment_rate(date(2026, 8, 31), date(2026, 8, 1), PRODUCT_TYPE_NEW),
            Decimal("0.15"),
        )


    def test_new_product_rate_starts_at_arrival_at_new_product_rate(self):
        self.assertEqual(
            impairment_rate(date(2026, 9, 29), date(2026, 8, 1), PRODUCT_TYPE_NEW),
            Decimal("0.295"),
        )


    def test_same_rule_comparison_keeps_decreasing_inventory_book_value_negative(self):
        current_layer = InventoryLayer(
            1, 10, 1, date(2026, 7, 8), 371, PRODUCT_TYPE_NEW, 0
        )
        previous_layer = InventoryLayer(
            1, 10, 1, date(2026, 7, 8), 394, PRODUCT_TYPE_NEW, 0
        )
        current_impairment = batch_impairments([current_layer], date(2026, 9, 29))[1]
        previous_impairment = batch_impairments([previous_layer], date(2026, 8, 31))[1]
        current_book_value = Decimal("5.689") * current_layer.quantity - (
            Decimal("5.689") * current_impairment.impairment_units
        )
        previous_book_value = Decimal("5.689") * previous_layer.quantity - (
            Decimal("5.689") * previous_impairment.impairment_units
        )

        self.assertLess(current_book_value - previous_book_value, 0)


    def test_first_known_rule_backfills_from_earliest_arrival(self):
        impairments = batch_impairments([
            InventoryLayer(
                1, 10, 1, date(2026, 8, 1), 236, PRODUCT_TYPE_NEW, 0,
                (ImpairmentRule(PRODUCT_TYPE_NEW, 0, date(2026, 9, 22)),),
            ),
        ], date(2026, 9, 29))

        self.assertEqual(impairments[1].provision_quantity, 236)
        self.assertEqual(impairments[1].rate, Decimal("0.295"))


    def test_stable_product_applies_safe_stock_from_arrival(self):
        impairments = batch_impairments([
            InventoryLayer(1, 10, 1, date(2026, 8, 1), 200, PRODUCT_TYPE_STABLE, 100),
        ], date(2026, 9, 29))

        self.assertEqual(impairments[1].provision_quantity, 100)
        self.assertEqual(impairments[1].impairment_units, Decimal("59.000"))
        self.assertEqual(impairments[1].rate, Decimal("0.59"))


    def test_stable_product_newly_arrived_uses_excess_over_safe_stock(self):
        impairments = batch_impairments([
            InventoryLayer(1, 10, 1, date(2026, 9, 10), 200, PRODUCT_TYPE_STABLE, 100),
        ], date(2026, 9, 29))

        self.assertEqual(impairments[1].provision_quantity, 100)
        self.assertEqual(impairments[1].impairment_units, Decimal("19.000"))
        self.assertEqual(impairments[1].rate, Decimal("0.19"))


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
