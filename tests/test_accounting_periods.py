import unittest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from services.accounting_periods import (
    auto_confirm_expired_periods,
    create_period_snapshot,
    period_confirmation_deadline,
)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def scalars(self):
        return self


class _SnapshotDb:
    def __init__(self, batch_rows, deduction_rows):
        self.batch_rows = batch_rows
        self.deduction_rows = deduction_rows
        self.execute_count = 0
        self.added = []
        self.flushed = False

    async def execute(self, statement):
        self.execute_count += 1
        return _Result(self.batch_rows if self.execute_count == 1 else self.deduction_rows)

    def add_all(self, values):
        self.added.extend(values)

    async def flush(self):
        self.flushed = True


class _AutoConfirmDb:
    def __init__(self, periods):
        self.periods = periods
        self.committed = False

    async def execute(self, statement):
        return _Result(self.periods)

    async def commit(self):
        self.committed = True


class AccountingPeriodTests(unittest.IsolatedAsyncioTestCase):
    def test_confirmation_deadline_is_the_end_of_the_next_day(self):
        period = SimpleNamespace(period_end=date(2026, 8, 31))
        self.assertEqual(
            period_confirmation_deadline(period),
            datetime(2026, 9, 1, 23, 59, 59, 999999),
        )

    async def test_snapshot_persists_quantity_and_book_value_at_period_end(self):
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
        db = _SnapshotDb([(batch, 4)], [deduction])

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

    async def test_expired_pending_period_is_automatically_confirmed(self):
        period = SimpleNamespace(
            id=9,
            period_end=date(2026, 8, 31),
            timezone="Asia/Shanghai",
            status="pending_confirmation",
            closed_at=None,
            closed_by_user_id=123,
        )
        db = _AutoConfirmDb([period])

        changed = await auto_confirm_expired_periods(
            db, now=datetime(2026, 9, 2, 0, 0)
        )
        self.assertEqual(changed, [9])
        self.assertEqual(period.status, "auto_closed")
        self.assertIsNone(period.closed_by_user_id)
        self.assertEqual(period.closed_at, datetime(2026, 9, 2, 0, 0))
        self.assertTrue(db.committed)


if __name__ == "__main__":
    unittest.main()
