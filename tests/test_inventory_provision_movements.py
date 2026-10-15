"""持有期间新增准备、销售转销、期初期末勾稽回归。"""

import unittest
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from services.inventory_impairment import ImpairmentRule, InventoryLayer
from services.inventory_provision_movements import (
    ProvisionAccrual, ProvisionReplay, ProvisionSale, ProvisionWriteoff,
    replay_inventory_provisions,
)


class ProvisionMovementsTests(unittest.TestCase):
    arrival = date(2026, 8, 31)

    def layer(self, batch_id=1, quantity=100, arrival=None, kind="new", safe=0, rules=None):
        arrival = arrival or self.arrival
        return InventoryLayer(batch_id, 10, 7, arrival, quantity, kind, safe,
                              tuple(rules or [ImpairmentRule(kind, safe, arrival)]))

    def sale(self, day, quantity, detail_id=1, batch_id=1, store_id=7):
        return ProvisionSale(detail_id, detail_id, batch_id,
                             datetime.combine(self.arrival + timedelta(days=day), time.min),
                             quantity, store_id)

    def test_screenshot_125_35_90_510_and_receipt_day_zero(self):
        ledger = replay_inventory_provisions(
            [self.layer()], [self.sale(10, 10), self.sale(20, 30, 2)], date(2026, 9, 30),
            balance_dates=[self.arrival],
        )
        current = ledger.balances[date(2026, 9, 30)][1]
        self.assertEqual(ledger.balances[self.arrival][1].impairment_units, 0)
        self.assertEqual(current.quantity, 60)
        self.assertEqual(current.rate, Decimal("0.15"))
        self.assertEqual(current.impairment_units * 10, Decimal("90"))
        self.assertEqual(current.period_impairment_units * 10, Decimal("90"))
        self.assertEqual(ledger.accrued_units(1, date(2026, 9, 1), date(2026, 10, 1)) * 10, Decimal("125"))
        self.assertEqual(ledger.written_off_units(1, date(2026, 9, 1), date(2026, 10, 1)) * 10, Decimal("35"))
        self.assertEqual((current.quantity - current.impairment_units) * 10, Decimal("510"))

    def test_calendar_september_1_to_30_is_29_days(self):
        arrival = date(2026, 9, 1)
        sales = [ProvisionSale(1, 1, 1, datetime(2026, 9, 11), 10, 7),
                 ProvisionSale(2, 2, 1, datetime(2026, 9, 21), 30, 7)]
        ledger = replay_inventory_provisions([self.layer(arrival=arrival)], sales, date(2026, 9, 30))
        self.assertEqual(ledger.balances[date(2026, 9, 30)][1].impairment_units * 10, Decimal("87"))
        self.assertEqual(ledger.accrued_units(1, arrival, date(2026, 10, 1)) * 10, Decimal("122"))

    def test_cross_month_transfer_includes_previous_provision_not_just_current_accrual(self):
        arrival = date(2026, 8, 11)
        sale = ProvisionSale(1, 1, 1, datetime(2026, 9, 5), 40, 7)
        ledger = replay_inventory_provisions([self.layer(arrival=arrival)], [sale], date(2026, 9, 10),
                                             balance_dates=[date(2026, 8, 31)])
        opening = ledger.balances[date(2026, 8, 31)][1].impairment_units
        current = ledger.balances[date(2026, 9, 10)][1]
        total = ledger.accrued_units(1, date(2026, 9, 1), date(2026, 9, 11))
        transferred = ledger.written_off_units(1, date(2026, 9, 1), date(2026, 9, 11))
        self.assertEqual((opening * 10, total * 10, transferred * 10), (Decimal(100), Decimal(40), Decimal(50)))
        self.assertEqual(current.period_impairment_units * 10, Decimal(30))
        self.assertEqual(opening + total - transferred, current.impairment_units)

    def test_fully_sold_can_have_monthly_provision_and_writeoff(self):
        ledger = replay_inventory_provisions([self.layer()], [self.sale(10, 100)], date(2026, 9, 30))
        current = ledger.balances[date(2026, 9, 30)][1]
        self.assertEqual((current.quantity, current.impairment_units, current.period_impairment_units), (0, 0, 0))
        self.assertEqual(ledger.accrued_units(1, date(2026, 9, 1), date(2026, 10, 1)) * 10, Decimal(50))
        self.assertEqual(ledger.writeoffs[0].units * 10, Decimal(50))

    def test_cap_stops_new_accrual_but_existing_provision_is_transferred(self):
        arrival = date(2026, 1, 1)
        sale = ProvisionSale(1, 1, 1, datetime(2026, 7, 5), 30, 7)
        ledger = replay_inventory_provisions([self.layer(arrival=arrival)], [sale], date(2026, 7, 10))
        self.assertEqual(ledger.accrued_units(1, date(2026, 7, 1), date(2026, 8, 1)), 0)
        self.assertEqual(ledger.writeoffs[0].units * 10, Decimal(270))
        self.assertEqual(ledger.balances[date(2026, 7, 10)][1].impairment_units * 10, Decimal(630))

    def test_later_batch_has_own_age_and_saved_fifo_assignment_is_used(self):
        layers = [self.layer(quantity=100), self.layer(2, 100, date(2026, 9, 20))]
        sales = [self.sale(25, 100), self.sale(25, 40, 2, 2)]
        ledger = replay_inventory_provisions(layers, sales, date(2026, 9, 30))
        current = ledger.balances[date(2026, 9, 30)]
        self.assertEqual(current[1].impairment_units, 0)
        self.assertEqual(current[2].impairment_units * 12, Decimal(36))
        self.assertEqual((current[2].quantity - current[2].impairment_units) * 12, Decimal(684))

    def test_safe_stock_new_receipt_does_not_retroactively_accrue_older_safe_days(self):
        layers = [self.layer(kind="stable", safe=100),
                  self.layer(2, 100, date(2026, 9, 20), "stable", 100)]
        ledger = replay_inventory_provisions(layers, [], date(2026, 9, 30))
        current = ledger.balances[date(2026, 9, 30)]
        self.assertEqual(current[1].impairment_units, Decimal("11"))
        self.assertEqual(current[2].impairment_units, 0)
        self.assertEqual(ledger.accrued_units(1, date(2026, 9, 1), date(2026, 10, 1)), Decimal("11"))

    def test_batch_internal_fifo_transfers_old_excess_integer_layers(self):
        layer = self.layer(kind="stable", safe=50)
        ledger = replay_inventory_provisions([layer], [self.sale(10, 40)], date(2026, 9, 20))
        current = ledger.balances[date(2026, 9, 20)][1]
        self.assertEqual(ledger.writeoffs[0].units, Decimal("4"))
        self.assertEqual(current.provision_quantity, 10)
        self.assertEqual(current.impairment_units, Decimal("2"))
        self.assertEqual(ledger.accrued_units(1, date(2026, 9, 1), date(2026, 9, 21)), Decimal("6"))

    def test_new_to_stable_retains_existing_provision_in_protected_layers(self):
        rules = [ImpairmentRule("new", 0, self.arrival), ImpairmentRule("stable", 50, date(2026, 9, 11))]
        layer = self.layer(kind="stable", safe=50, rules=rules)
        ledger = replay_inventory_provisions([layer], [self.sale(15, 30)], date(2026, 9, 20))
        # 9月10日前所有100件各5%；转稳健后较早50件继续计提，售出先转出这些层。
        self.assertEqual(ledger.writeoffs[0].units, Decimal("3"))
        self.assertEqual(ledger.balances[date(2026, 9, 20)][1].impairment_units, Decimal("5.5"))

    def test_sales_before_arrival_are_explicitly_unknown_and_other_products_are_preserved(self):
        invalid = self.layer(arrival=date(2026, 9, 10))
        other = InventoryLayer(2, 20, 7, self.arrival, 100, "new", 0,
                               (ImpairmentRule("new", 0, self.arrival),))
        sale = ProvisionSale(1, 1, 1, datetime(2026, 9, 5), 10, 7)
        ledger = replay_inventory_provisions([invalid, other], [sale], date(2026, 9, 30))
        self.assertIn("销售日期早于", ledger.notes[1])
        self.assertIsNone(ledger.balances[date(2026, 9, 30)][1].impairment_units)
        self.assertIsNone(ledger.writeoffs[0].units)
        self.assertEqual(ledger.balances[date(2026, 9, 30)][2].impairment_units, Decimal(15))

    def test_overdraw_is_flagged_and_does_not_fabricate_negative_inventory(self):
        ledger = replay_inventory_provisions([self.layer()], [self.sale(10, 101)], date(2026, 9, 30))
        self.assertIn("数量异常", ledger.notes[1])
        self.assertIsNone(ledger.writeoffs[0].units)
        self.assertEqual(ledger.balances[date(2026, 9, 30)][1].quantity, 0)

    def test_bad_new_batch_does_not_poison_an_independent_new_batch_of_same_sku(self):
        layers = [self.layer(arrival=date(2026, 9, 10)), self.layer(2, 100, date(2026,9,20))]
        sale = ProvisionSale(1, 1, 1, datetime(2026,9,5), 10, 7)
        ledger = replay_inventory_provisions(layers, [sale], date(2026,9,30))
        self.assertIsNone(ledger.balances[date(2026,9,30)][1].impairment_units)
        self.assertEqual(ledger.balances[date(2026,9,30)][2].impairment_units, Decimal(5))
        self.assertNotIn(2, ledger.notes)

    def test_later_safe_stock_rule_does_not_change_earlier_healthy_balances(self):
        rules = [ImpairmentRule("new", 0, date(2026, 9, 1)),
                 ImpairmentRule("stable", 50, date(2026, 10, 1))]
        layers = [self.layer(arrival=date(2026, 9, 10), kind="stable", safe=50, rules=rules),
                  self.layer(2, arrival=date(2026, 9, 1), kind="stable", safe=50, rules=rules)]
        sales = [ProvisionSale(1, 1, 1, datetime(2026, 9, 5), 10, 7)]
        september = replay_inventory_provisions(layers, sales, date(2026, 9, 30))
        october = replay_inventory_provisions(layers, sales, date(2026, 10, 31),
                                              balance_dates=[date(2026, 9, 30)])
        expected = Decimal("14.5")
        for ledger in (september, october):
            self.assertEqual(ledger.balances[date(2026, 9, 30)][2].impairment_units, expected)
            self.assertEqual(ledger.accrued_units(2, date(2026, 9, 1), date(2026, 10, 1)), expected)
        self.assertIsNone(october.balances[date(2026, 10, 31)][2].impairment_units)

    def test_sold_out_bad_batch_does_not_taint_later_receipt_when_safe_rule_starts(self):
        rules = [ImpairmentRule("new", 0, date(2026, 9, 1)),
                 ImpairmentRule("stable", 50, date(2026, 10, 1))]
        layers = [self.layer(arrival=date(2026, 9, 10), kind="stable", safe=50, rules=rules),
                  self.layer(2, arrival=date(2026, 10, 5), kind="stable", safe=50, rules=rules)]
        sales = [ProvisionSale(1, 1, 1, datetime(2026, 9, 5), 100, 7)]
        ledger = replay_inventory_provisions(layers, sales, date(2026, 10, 31))
        current = ledger.balances[date(2026, 10, 31)][2]
        self.assertEqual(current.impairment_units, Decimal("13"))
        self.assertEqual(current.provision_quantity, 50)
        self.assertNotIn(2, ledger.notes)

    def test_shared_safe_stock_taint_is_not_a_new_quantity_error_source(self):
        rules = [ImpairmentRule("stable", 50, self.arrival)]
        layers = [self.layer(arrival=date(2026, 9, 10), kind="stable", safe=50, rules=rules),
                  self.layer(2, kind="stable", safe=50, rules=rules),
                  self.layer(3, arrival=date(2026, 9, 25), kind="stable", safe=50, rules=rules)]
        sales = [ProvisionSale(1, 1, 1, datetime(2026, 9, 5), 10, 7),
                 ProvisionSale(2, 2, 1, datetime(2026, 9, 20), 90, 7)]
        ledger = replay_inventory_provisions(layers, sales, date(2026, 9, 30),
                                             balance_dates=[date(2026, 9, 4), date(2026, 9, 5)])
        self.assertEqual(ledger.balances[date(2026, 9, 4)][2].impairment_units, Decimal("2"))
        self.assertIsNone(ledger.balances[date(2026, 9, 5)][2].impairment_units)
        self.assertIsNone(ledger.balances[date(2026, 9, 30)][2].impairment_units)
        self.assertEqual(ledger.balances[date(2026, 9, 30)][3].impairment_units, Decimal("2.5"))
        self.assertNotIn(3, ledger.notes)

    def test_period_queries_preserve_same_day_unknowns_and_half_open_boundaries(self):
        days = [date(2026, 9, number) for number in range(1, 7)]
        accruals = [ProvisionAccrual(1, days[2], Decimal(4)),
                    ProvisionAccrual(1, days[0], Decimal(1)),
                    ProvisionAccrual(1, days[1], Decimal(9)),
                    ProvisionAccrual(2, days[1], Decimal(5)),
                    ProvisionAccrual(1, days[0], Decimal(2)),
                    ProvisionAccrual(1, days[1], None)]
        writeoffs = [ProvisionWriteoff(ProvisionSale(1, 1, 1, datetime(2026, 9, 4), 1), None),
                     ProvisionWriteoff(ProvisionSale(2, 2, 1, datetime(2026, 9, 5), 1), Decimal(1))]
        ledger = ProvisionReplay({}, accruals, writeoffs, {})
        self.assertEqual(ledger.accrued_units(1, days[0], days[1]), 3)
        self.assertIsNone(ledger.accrued_units(1, days[1], days[2]))
        self.assertEqual(ledger.accrued_units(1, days[2], days[3]), 4)
        self.assertIsNone(ledger.accrued_units(1, days[3], days[4]))
        self.assertEqual(ledger.accrued_units(1, days[4], days[5]), 0)
        self.assertEqual(ledger.accrued_units(2, days[0], days[5]), 5)
        self.assertEqual(ledger.accrued_units(99, days[0], days[5]), 0)
        self.assertEqual(ledger.accrued_units(1, days[0], days[0]), 0)
        self.assertEqual(ledger.accrued_units(1, days[5], days[0]), 0)
        self.assertIsNone(ledger.written_off_units(1, days[3], days[4]))
        self.assertEqual(ledger.written_off_units(1, days[4], days[5]), 1)

    def test_new_invalid_sale_of_a_sold_out_batch_still_affects_shared_safety_that_day(self):
        rules = [ImpairmentRule("stable", 50, self.arrival)]
        layers = [self.layer(kind="stable", safe=50, rules=rules),
                  self.layer(2, arrival=date(2026, 9, 15), kind="stable", safe=50, rules=rules),
                  self.layer(3, arrival=date(2026, 9, 25), kind="stable", safe=50, rules=rules)]
        sales = [ProvisionSale(1, 1, 1, datetime(2026, 9, 10), 100, 7),
                 ProvisionSale(2, 2, 1, datetime(2026, 9, 20), 1, 7)]
        ledger = replay_inventory_provisions(layers, sales, date(2026, 9, 30),
                                             balance_dates=[date(2026, 9, 19), date(2026, 9, 20)])
        self.assertEqual(ledger.balances[date(2026, 9, 19)][2].impairment_units, Decimal(2))
        self.assertIsNone(ledger.balances[date(2026, 9, 20)][2].impairment_units)
        self.assertIsNone(ledger.writeoffs[-1].units)
        self.assertEqual(ledger.balances[date(2026, 9, 30)][3].impairment_units, Decimal("2.5"))

    def test_many_batch_period_queries_do_not_rescan_global_events(self):
        class CountedEvents(list):
            visits = 0

            def __iter__(self):
                for event in super().__iter__():
                    self.visits += 1
                    yield event

        count = 300
        accruals = CountedEvents(ProvisionAccrual(batch_id, date(2026, 9, day), Decimal(1))
                                for day in range(1, 31) for batch_id in range(count))
        writeoffs = CountedEvents(ProvisionWriteoff(
            ProvisionSale(batch_id, batch_id, batch_id, datetime(2026, 9, 20), 1), Decimal(3))
            for batch_id in range(count))
        ledger = ProvisionReplay({}, accruals, writeoffs, {})
        for batch_id in range(count):
            self.assertEqual(ledger.accrued_units(batch_id, date(2026, 9, 1), date(2026, 10, 1)), 30)
            self.assertEqual(ledger.written_off_units(batch_id, date(2026, 9, 1), date(2026, 10, 1)), 3)
        self.assertLessEqual(accruals.visits, 2 * len(accruals))
        self.assertLessEqual(writeoffs.visits, 2 * len(writeoffs))

    def test_invalid_fully_sold_before_arrival_does_not_report_zero_monthly_accrual(self):
        layer = self.layer(arrival=date(2026, 9, 10))
        sale = ProvisionSale(1, 1, 1, datetime(2026, 9, 5), 100, 7)
        ledger = replay_inventory_provisions([layer], [sale], date(2026, 9, 30))
        self.assertEqual(ledger.balances[date(2026, 9, 30)][1].impairment_units, 0)
        self.assertIsNone(ledger.accrued_units(1, date(2026, 9, 1), date(2026, 10, 1)))

    def test_year_boundary_and_multiple_integer_layers_reconcile(self):
        arrival = date(2025, 12, 1)
        layers = [self.layer(arrival=arrival, kind="stable", safe=40),
                  self.layer(2, 50, date(2026, 1, 5), "stable", 40)]
        sales = [ProvisionSale(1, 1, 1, datetime(2025, 12, 20), 20, 7),
                 ProvisionSale(2, 2, 1, datetime(2026, 1, 10), 30, 7),
                 ProvisionSale(3, 3, 2, datetime(2026, 1, 20), 15, 7)]
        ledger = replay_inventory_provisions(layers, sales, date(2026, 1, 31),
                                             balance_dates=[date(2025, 12, 31)])
        for batch_id in (1, 2):
            opening = ledger.balances[date(2025, 12, 31)][batch_id].impairment_units
            current = ledger.balances[date(2026, 1, 31)][batch_id]
            accrued = ledger.accrued_units(batch_id, date(2026, 1, 1), date(2026, 2, 1))
            writeoff = ledger.written_off_units(batch_id, date(2026, 1, 1), date(2026, 2, 1))
            self.assertEqual(opening + accrued - writeoff, current.impairment_units)
            self.assertIsInstance(current.provision_quantity, int)
            self.assertLessEqual(current.impairment_units, current.quantity * Decimal("0.9"))

    def test_future_sales_ignored_empty_and_arrival_day_sale(self):
        self.assertEqual(replay_inventory_provisions([], [], self.arrival).balances[self.arrival], {})
        ledger = replay_inventory_provisions([self.layer()], [self.sale(31, 100)], date(2026, 9, 30))
        self.assertEqual(len(ledger.writeoffs), 0)
        ledger = replay_inventory_provisions([self.layer()], [self.sale(0, 100)], self.arrival)
        self.assertEqual(ledger.writeoffs[0].units, 0)
        self.assertEqual(ledger.balances[self.arrival][1].quantity, 0)


if __name__ == "__main__":
    unittest.main()
