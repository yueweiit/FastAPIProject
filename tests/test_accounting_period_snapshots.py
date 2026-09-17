import unittest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from services.accounting_periods import create_period_snapshot, ensure_period_snapshot


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self


class _SnapshotDb:
    def __init__(self, batch_rows, deduction_rows, rule_rows=()):
        self.batch_rows = batch_rows
        self.deduction_rows = deduction_rows
        self.rule_rows = rule_rows
        self.execute_count = 0
        self.added = []
        self.flushed = False

    async def execute(self, statement):
        self.execute_count += 1
        if self.execute_count == 1:
            return _Result(self.batch_rows)
        if self.execute_count == 2:
            return _Result(self.rule_rows)
        return _Result(self.deduction_rows)

    def add_all(self, values):
        self.added.extend(values)

    async def flush(self):
        self.flushed = True


class AccountingPeriodSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_rejects_an_incomplete_month(self):
        with self.assertRaisesRegex(ValueError, "只能在该自然月结束后"):
            await ensure_period_snapshot(
                None,
                date(2026, 9, 30),
                now=datetime(2026, 9, 17),
            )

    async def test_snapshot_persists_month_end_quantity_and_book_value_once(self):
        period = SimpleNamespace(
            id=7,
            period_end=date(2026, 8, 31),
            snapshot_version=0,
        )
        batch = SimpleNamespace(
            id=11,
            product_id=3,
            quantity=10,
            unit_cost=Decimal("2.50"),
            arrived_at=datetime(2026, 7, 1),
        )
        deduction = SimpleNamespace(batch_id=11, deducted_quantity=3)
        db = _SnapshotDb([(batch, 4, "stable", 0)], [deduction])

        self.assertTrue(await create_period_snapshot(db, period))
        snapshot = db.added[0]
        self.assertEqual(snapshot.accounting_period_id, 7)
        self.assertEqual(snapshot.quantity, 7)
        self.assertEqual(snapshot.store_id, 4)
        self.assertEqual(snapshot.inventory_amount, Decimal("17.50"))
        self.assertEqual(snapshot.impairment_rate, Decimal("0.61"))
        self.assertEqual(snapshot.book_value, Decimal("6.8250"))
        self.assertEqual(period.snapshot_version, 1)
        self.assertTrue(db.flushed)

        self.assertFalse(await create_period_snapshot(db, period))
        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.execute_count, 3)


if __name__ == "__main__":
    unittest.main()
