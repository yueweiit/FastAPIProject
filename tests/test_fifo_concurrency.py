import asyncio
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from models import Sale, SaleCostDetail
from services.fifo import InsufficientStockError, fifo_sell


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Inventory:
    def __init__(self):
        self.remaining_quantity = 1
        self.lock = asyncio.Lock()
        self.sales = []
        self.details = []
        self.next_sale_id = 1


class _Session:
    def __init__(self, inventory):
        self.inventory = inventory
        self.batch = None
        self.added = []
        self.locked = False

    async def execute(self, statement):
        # Model a MySQL locking read: the second session sees the committed
        # quantity only after the first session finishes its transaction.
        if statement._for_update_arg is not None:
            await self.inventory.lock.acquire()
            self.locked = True
        quantity = self.inventory.remaining_quantity
        self.batch = SimpleNamespace(
            id=1,
            batch_no="BATCH-1",
            remaining_quantity=quantity,
            unit_cost=Decimal("10"),
        )
        await asyncio.sleep(0)
        return _Rows([self.batch] if quantity > 0 else [])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if isinstance(value, Sale) and value.id is None:
                value.id = self.inventory.next_sale_id
                self.inventory.next_sale_id += 1

    async def commit(self):
        self.inventory.remaining_quantity = self.batch.remaining_quantity
        self.inventory.sales.extend(value for value in self.added if isinstance(value, Sale))
        self.inventory.details.extend(
            value for value in self.added if isinstance(value, SaleCostDetail)
        )
        if self.locked:
            self.inventory.lock.release()
            self.locked = False

    async def rollback(self):
        if self.locked:
            self.inventory.lock.release()
            self.locked = False

    async def refresh(self, _value):
        pass


class FifoConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_sales_cannot_both_consume_the_last_unit(self):
        inventory = _Inventory()

        async def sell(order_no):
            db = _Session(inventory)
            try:
                await fifo_sell(
                    db,
                    product_id=1,
                    order_no=order_no,
                    quantity=1,
                    selling_price=Decimal("20"),
                    sold_at=datetime(2026, 9, 30),
                )
                return True
            except InsufficientStockError:
                await db.rollback()
                return False

        outcomes = await asyncio.gather(sell("ORDER-1"), sell("ORDER-2"))

        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(inventory.remaining_quantity, 0)
        self.assertEqual(len(inventory.sales), 1)
        self.assertEqual(len(inventory.details), 1)
        self.assertEqual(inventory.details[0].quantity, 1)


if __name__ == "__main__":
    unittest.main()
