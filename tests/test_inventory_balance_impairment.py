"""早期静态余额历史回归；现行回放见test_inventory_provision_movements。"""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from services.inventory_balance_impairment import inventory_balance_impairments
from services.inventory_impairment import ImpairmentRule, InventoryLayer, batch_impairments


class InventoryBalanceImpairmentTests(unittest.TestCase):
    def test_confirmed_100_units_40_sold_40_days_example(self):
        arrival = date(2026, 8, 21)
        layer = InventoryLayer(1, 10, None, arrival, 100, "new", 0)
        result = inventory_balance_impairments(
            [layer], [(1, arrival + timedelta(days=1), 20),
                      (1, arrival + timedelta(days=2), 20)], date(2026, 9, 30),
        )[1]
        cost = Decimal("10")
        self.assertEqual(result.quantity, 60)
        self.assertEqual(result.current.rate, Decimal("0.20"))
        self.assertEqual(cost * result.current.impairment_units, Decimal("120"))
        self.assertEqual(cost * (result.quantity - result.current.impairment_units), Decimal("480"))
        self.assertEqual(result.previous_quantity, 60)
        self.assertEqual(cost * result.previous.impairment_units, Decimal("30"))
        self.assertEqual(cost * result.current.period_impairment_units, Decimal("90"))

    def test_sales_this_month_do_not_turn_book_difference_into_monthly_provision(self):
        layer = InventoryLayer(1, 10, None, date(2026, 8, 21), 100, "new", 0)
        result = inventory_balance_impairments([layer], [(1, date(2026, 9, 5), 40)], date(2026, 9, 30))[1]
        self.assertEqual(result.quantity, 60)
        self.assertEqual(result.previous_quantity, 100)
        self.assertEqual(result.current.impairment_units, Decimal("12"))
        self.assertEqual(result.previous.impairment_units, Decimal("5"))
        self.assertEqual(result.current.period_impairment_units, Decimal("9"))
        # 本期账面480，上期950，差额-470；现存库存本月计提90。
        difference = Decimal("10") * (
            result.quantity - result.current.impairment_units
            - result.previous_quantity + result.previous.impairment_units
        )
        self.assertEqual(difference, Decimal("-470"))

    def test_sales_on_cutoff_are_included_future_and_unknown_sales_ignored(self):
        arrival = date(2026, 9, 1)
        layer = InventoryLayer(1, 10, None, arrival, 100, "new", 0)
        result = inventory_balance_impairments(
            [layer], [(1, arrival, 10), (1, date(2026, 9, 2), 10),
                      (1, date(2026, 9, 3), 80), (9, arrival, 100)], date(2026, 9, 2),
        )[1]
        self.assertEqual(result.quantity, 80)
        self.assertEqual(result.current.impairment_units, Decimal("0.4"))
        self.assertEqual(result.previous_quantity, 0)
        self.assertEqual(result.previous.impairment_units, 0)

    def test_sold_out_batch_has_zero_closing_balance_and_keeps_previous_balance(self):
        layer = InventoryLayer(1, 10, None, date(2026, 8, 21), 100, "new", 0)
        result = inventory_balance_impairments([layer], [(1, date(2026, 9, 3), 100)], date(2026, 9, 30))[1]
        self.assertEqual(result.quantity, 0)
        self.assertEqual(result.current.impairment_units, 0)
        self.assertEqual(result.current.period_impairment_units, 0)
        self.assertEqual(result.current.provision_quantity, 0)
        self.assertEqual(result.previous_quantity, 100)
        self.assertEqual(result.previous.impairment_units, Decimal("5"))

    def test_later_receipts_have_independent_age_and_previous_zero(self):
        layers = [InventoryLayer(1, 10, None, date(2026, 8, 21), 100, "new", 0),
                  InventoryLayer(2, 10, None, date(2026, 9, 20), 100, "new", 0)]
        result = inventory_balance_impairments(layers, [(1, date(2026, 8, 22), 40)], date(2026, 9, 30))
        self.assertEqual(result[1].current.rate, Decimal("0.20"))
        self.assertEqual(result[2].current.rate, Decimal("0.05"))
        self.assertEqual(result[2].previous_quantity, 0)
        self.assertEqual(result[2].previous.impairment_units, 0)
        # 原批单价10、新批单价15：期末准备195、账面1905。
        provision = Decimal("10") * result[1].current.impairment_units + Decimal("15") * result[2].current.impairment_units
        remaining_cost = Decimal("10") * result[1].quantity + Decimal("15") * result[2].quantity
        self.assertEqual(provision, Decimal("195"))
        self.assertEqual(remaining_cost - provision, Decimal("1905"))

    def test_safe_stock_is_allocated_separately_for_each_cutoff(self):
        layers = [InventoryLayer(1, 10, None, date(2026, 8, 1), 100, "stable", 100),
                  InventoryLayer(2, 10, None, date(2026, 9, 20), 100, "stable", 100)]
        result = inventory_balance_impairments(layers, [], date(2026, 9, 30))
        self.assertEqual(result[1].current.impairment_units, Decimal("60"))
        self.assertEqual(result[1].previous.impairment_units, 0)
        self.assertEqual(result[2].current.impairment_units, 0)
        # 本月现存增提30，与准备余额变化60含义不同。
        self.assertEqual(result[1].current.period_impairment_units, Decimal("30"))

    def test_rate_cap_bounds_closing_provision_and_stops_further_monthly_accrual(self):
        layer = InventoryLayer(1, 10, None, date(2026, 1, 1), 60, "stable", 0)
        result = inventory_balance_impairments([layer], [], date(2026, 9, 30))[1]
        self.assertEqual(result.current.rate, Decimal("0.9"))
        self.assertEqual(result.current.impairment_units, Decimal("54"))
        self.assertEqual(result.current.period_impairment_units, 0)
        self.assertEqual(Decimal("10") * (result.quantity - result.current.impairment_units), Decimal("60"))

    def test_partial_month_across_year_boundary(self):
        layer = InventoryLayer(1, 10, None, date(2025, 12, 30), 10, "new", 0)
        result = inventory_balance_impairments([layer], [], date(2026, 1, 2))[1]
        self.assertEqual(result.current.impairment_units, Decimal("0.15"))
        self.assertEqual(result.previous.impairment_units, Decimal("0.05"))
        self.assertEqual(result.current.period_impairment_units, Decimal("0.10"))

    def test_rule_history_reuses_existing_balance_algorithm_without_summing_balances(self):
        rules = (ImpairmentRule("new", 0, date(2026, 8, 21)),
                 ImpairmentRule("stable", 50, date(2026, 9, 11)))
        layer = InventoryLayer(1, 10, None, date(2026, 8, 21), 100, "stable", 50, rules)
        result = inventory_balance_impairments([layer], [(1, date(2026, 9, 5), 20)], date(2026, 9, 30))[1]
        remaining = InventoryLayer(1, 10, None, layer.arrived_at, 80, "stable", 50, rules)
        expected = batch_impairments([remaining], date(2026, 9, 30), period_start=date(2026, 9, 1))[1]
        self.assertEqual(result.current, expected)
        self.assertEqual(result.current.impairment_units, Decimal("14"))
        self.assertEqual(result.current.period_impairment_units, Decimal("10"))
        self.assertEqual(result.previous.impairment_units, Decimal("5"))

    def test_empty_future_only_arrival_day_and_overdrawn_history(self):
        layer = InventoryLayer(1, 10, None, date(2026, 9, 10), 100, "new", 0)
        self.assertEqual(inventory_balance_impairments([], [], date(2026, 9, 30)), {})
        self.assertEqual(inventory_balance_impairments([layer], [], date(2026, 9, 9)), {})
        result = inventory_balance_impairments([layer], [], date(2026, 9, 10))[1]
        self.assertEqual(result.quantity, 100)
        self.assertEqual(result.current.impairment_units, 0)
        result = inventory_balance_impairments([layer], [(1, date(2026, 9, 11), 101)], date(2026, 9, 30))[1]
        self.assertEqual(result.quantity, 0)
        self.assertEqual(result.current.impairment_units, 0)


if __name__ == "__main__":
    unittest.main()
