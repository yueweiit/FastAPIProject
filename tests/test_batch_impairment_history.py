"""按日归属的准备结转、期间勾稽、精度和未知值；纯回放，不连接数据库。"""
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from services.batch_impairment_history import StoreRule
from services.batch_store_allocations import (AssignedBatch, allocated_batch_amount,
    allocated_provision_balance, allocated_provision_period, allocated_provision_transfers)
from services.inventory_impairment import ImpairmentRule, InventoryLayer
from services.inventory_provision_movements import (ProvisionAccrual, ProvisionReplay,
    replay_inventory_provisions, ProvisionSale)


class TemporalAllocationTests(unittest.TestCase):
    def setup_replay(self, cost, groups):
        arrival, cutoff = date(2026, 12, 1), date(2027, 2, 10)
        rules = [StoreRule(day, [{"id": owner, "name": str(owner)} for owner in owners])
                 for day, owners in groups]
        batch = AssignedBatch(SimpleNamespace(id=1, unit_cost=Decimal(cost)), rules[-1].stores, rules)
        layer = InventoryLayer(1, 1, 1, arrival, 19, "stable", 0,
                               (ImpairmentRule("stable", 0, arrival),))
        sales = [ProvisionSale(index, index, 1, datetime.combine(day, datetime.min.time()), quantity, 99)
                 for index, (day, quantity) in enumerate([(date(2026, 12, 19), 3),
                     (date(2027, 1, 1), 4), (date(2027, 1, 15), 5), (cutoff, 2)], 1)]
        snapshots = [date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 31)]
        replay = replay_inventory_provisions([layer], sales, cutoff, balance_dates=snapshots,
            batch_balance_dates={1: [rule.effective_date-timedelta(days=1) for rule in rules[1:]]})
        return batch, replay, cutoff

    def test_multiple_changes_cross_year_and_partial_sales_conserve_each_period(self):
        groups = [(date(2026, 12, 1), [1, 2, 3]), (date(2027, 1, 1), [2, 3, 4, 5]),
                  (date(2027, 1, 15), [1, 4]), (date(2027, 2, 1), [3, 4, 5])]
        periods = [(date(2026, 12, 1), date(2027, 1, 1)),
                   (date(2027, 1, 1), date(2027, 2, 1)),
                   (date(2027, 2, 1), date(2027, 2, 11))]
        for cost in ("0.0001", "1", "7.1234"):
            batch, replay, cutoff = self.setup_replay(cost, groups)
            for start, end in periods:
                opening_day, closing_day = start-timedelta(days=1), end-timedelta(days=1)
                opening_units = replay.balances[opening_day][1].impairment_units
                closing = replay.balances[closing_day][1]
                sums = [Decimal(0)] * 4
                for owner in range(1, 6):
                    with self.subTest(cost=cost, period=start, owner=owner):
                        opening = allocated_provision_balance(replay, batch, owner, opening_day, opening_units)
                        balance = allocated_provision_balance(replay, batch, owner, closing_day, closing.impairment_units)
                        accrued = allocated_provision_period(replay, batch, owner, start, end)
                        written = allocated_provision_period(replay, batch, owner, start, end, writeoff=True)
                        incoming, outgoing = allocated_provision_transfers(replay, batch, owner, start, end)
                        self.assertEqual(opening + accrued - written + incoming - outgoing, balance)
                        self.assertGreaterEqual(balance, 0)
                        self.assertGreaterEqual(accrued, 0)
                        self.assertGreaterEqual(written, 0)
                        from services.batch_impairment_history import rule_at
                        inventory = allocated_batch_amount(batch.unit_cost * closing.quantity, batch, owner,
                                                           stores=rule_at(batch, closing_day).stores)
                        self.assertLessEqual(balance, inventory)
                        sums = [a+b for a, b in zip(sums, (balance, accrued, incoming, outgoing))]
                self.assertEqual(sums[0], closing.impairment_units * batch.unit_cost)
                self.assertEqual(sums[1], replay.accrued_units(1, start, end)*batch.unit_cost)
                self.assertEqual(sums[2], sums[3])
                self.assertEqual(allocated_provision_transfers(replay, batch, None, start, end), tuple(sums[2:]))
            for owner in range(1, 6):
                jan = allocated_provision_period(replay, batch, owner, periods[1][0], periods[1][1])
                feb = allocated_provision_period(replay, batch, owner, periods[2][0], periods[2][1])
                ytd = allocated_provision_period(replay, batch, owner, periods[1][0], periods[2][1])
                self.assertEqual(jan+feb, ytd)

    def test_missing_owner_or_unknown_reserve_is_never_zero_transfer(self):
        for owners in ([], [{"id": 1, "name": "A"}]):
            rules = [StoreRule(date(2026, 9, 1), owners),
                     StoreRule(date(2026, 9, 15), [{"id": 2, "name": "B"}])]
            batch = AssignedBatch(SimpleNamespace(id=1, unit_cost=Decimal(1)), rules[-1].stores, rules)
            unknown = None if owners else Decimal(1)
            replay = ProvisionReplay({}, [ProvisionAccrual(1, date(2026, 9, 2), unknown)], [], {})
            self.assertEqual(allocated_provision_transfers(replay, batch, 2, date(2026, 9, 1), date(2026, 10, 1)),
                             (None, None))
            self.assertEqual(allocated_provision_transfers(replay, batch, 99, date(2026, 9, 1), date(2026, 10, 1)),
                             (0, 0))

    def test_partial_snapshots_only_capture_requested_batch(self):
        day = date(2026, 9, 15)
        layers = [InventoryLayer(i, i, 1, date(2026, 9, 1), 10, "stable", 0) for i in (1, 2)]
        replay = replay_inventory_provisions(layers, [], day+timedelta(days=1), batch_balance_dates={1: [day]})
        self.assertEqual(set(replay.balances[day]), {1})
        self.assertEqual(set(replay.balances[day+timedelta(days=1)]), {1, 2})


if __name__ == "__main__":
    unittest.main()
