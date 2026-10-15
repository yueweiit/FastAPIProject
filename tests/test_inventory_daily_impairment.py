"""已停用的2026-10-12算法回归，不代表当前报表验收口径。"""

import unittest
from datetime import date, timedelta
from decimal import Decimal

from services.inventory_daily_impairment import daily_accumulated_impairments
from services.inventory_impairment import ImpairmentRule, InventoryLayer, batch_impairments


class InventoryDailyImpairmentTests(unittest.TestCase):
    def test_historical_daily_accumulation_example_and_month_split(self):
        arrival = date(2026, 8, 21)
        layer = InventoryLayer(1, 10, None, arrival, 100, "new", 0)
        result = daily_accumulated_impairments(
            [layer], [(1, arrival + timedelta(days=1), 10), (1, arrival + timedelta(days=2), 10)],
            arrival + timedelta(days=40),
        )[1]
        self.assertEqual(result.quantity, 80)
        self.assertEqual(result.previous_quantity, 80)
        self.assertEqual(result.current.rate, Decimal("0.20"))
        self.assertEqual(Decimal(10) * result.cumulative_units, Decimal("3280.50"))
        self.assertEqual(Decimal(1000) - Decimal(10) * result.cumulative_units, Decimal("-2280.50"))
        self.assertEqual(Decimal(10) * result.monthly_units, Decimal("3060"))
        self.assertEqual(Decimal(10) * result.previous_units, Decimal("220.50"))

    def test_same_day_sales_are_deducted_before_calculating_and_future_sales_ignored(self):
        arrival = date(2026, 9, 1)
        layer = InventoryLayer(1, 10, None, arrival, 100, "new", 0)
        result = daily_accumulated_impairments(
            [layer], [(1, arrival, 10), (1, date(2026, 9, 2), 10),
                      (1, date(2026, 9, 3), 80), (9, arrival, 100)], date(2026, 9, 2),
        )[1]
        self.assertEqual(result.quantity, 80)
        self.assertEqual(result.cumulative_units, Decimal("0.4"))
        self.assertEqual(result.previous_quantity, 0)

    def test_sold_out_stops_new_amounts_but_keeps_history(self):
        layer = InventoryLayer(1, 10, None, date(2026, 7, 1), 50, "new", 0)
        result = daily_accumulated_impairments([layer], [(1, date(2026, 7, 3), 50)], date(2026, 9, 30))[1]
        self.assertEqual(result.quantity, 0)
        self.assertEqual(result.cumulative_units, Decimal("0.25"))
        self.assertEqual(result.monthly_units, 0)
        self.assertEqual(result.current.provision_quantity, 0)

    def test_later_receipts_and_safe_stock_are_evaluated_on_each_day(self):
        layers = [InventoryLayer(1, 10, None, date(2026, 9, 1), 100, "stable", 100),
                  InventoryLayer(2, 10, None, date(2026, 9, 3), 100, "stable", 100)]
        result = daily_accumulated_impairments(layers, [], date(2026, 9, 4))
        # 9月2日旧批全在安全库存；新批到货后保护新批，旧批按当日库龄计算。
        self.assertEqual(result[1].cumulative_units, Decimal("5"))
        self.assertEqual(result[2].cumulative_units, 0)
        self.assertEqual(result[1].current.rate, Decimal("0.03"))

    def test_daily_rate_cap_does_not_cap_accumulated_amount_or_stop_daily_sum(self):
        layer = InventoryLayer(1, 10, None, date(2026, 1, 1), 1, "stable", 0)
        result = daily_accumulated_impairments([layer], [], date(2026, 1, 1) + timedelta(days=92))[1]
        expected = sum((min(Decimal(day) * Decimal("0.01"), Decimal("0.9")) for day in range(1, 93)), Decimal("0"))
        self.assertEqual(result.cumulative_units, expected)
        self.assertEqual(result.cumulative_units, Decimal("42.75"))
        self.assertEqual(result.current.rate, Decimal("0.9"))

    def test_partial_month_across_year_boundary(self):
        layer = InventoryLayer(1, 10, None, date(2025, 12, 30), 10, "new", 0)
        result = daily_accumulated_impairments([layer], [], date(2026, 1, 2))[1]
        self.assertEqual(result.cumulative_units, Decimal("0.30"))
        self.assertEqual(result.previous_units, Decimal("0.05"))
        self.assertEqual(result.monthly_units, Decimal("0.25"))

    def test_rule_history_reuses_the_daily_balance_algorithm(self):
        rules = (ImpairmentRule("new", 0, date(2026, 9, 1)),
                 ImpairmentRule("stable", 50, date(2026, 9, 11)))
        layer = InventoryLayer(1, 10, None, date(2026, 9, 1), 100, "stable", 50, rules)
        result = daily_accumulated_impairments([layer], [], date(2026, 9, 20))[1]
        expected = sum((batch_impairments([layer], date(2026, 9, day))[1].impairment_units for day in range(2, 21)), Decimal("0"))
        self.assertEqual(result.cumulative_units, expected)
        self.assertEqual(result.current.impairment_units, Decimal("9.5"))

    def test_empty_future_only_and_arrival_day(self):
        layer = InventoryLayer(1, 10, None, date(2026, 9, 10), 100, "new", 0)
        self.assertEqual(daily_accumulated_impairments([], [], date(2026, 9, 30)), {})
        self.assertEqual(daily_accumulated_impairments([layer], [], date(2026, 9, 9)), {})
        result = daily_accumulated_impairments([layer], [], date(2026, 9, 10))[1]
        self.assertEqual(result.quantity, 100)
        self.assertEqual(result.cumulative_units, 0)


if __name__ == "__main__":
    unittest.main()
