import asyncio
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from models import Sale
from routers.sales import _allocate_available_stock, create_sale
from schemas import SaleCreate
from services.fifo import InsufficientStockError, fifo_sell
from tests.test_fifo_concurrency import _Inventory, _Session
from tests.test_sales_rules import _FakeDb


def batch(batch_id, arrived_at, quantity, product_id=1):
    return SimpleNamespace(id=batch_id, batch_no=f"B-{batch_id}", arrived_at=arrived_at,
                           remaining_quantity=quantity, product_id=product_id, unit_cost=Decimal(10))


class SalesIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_future_receipts_do_not_fulfil_historical_sales(self):
        old = batch(1, datetime(2026,9,1), 2)
        future = batch(2, datetime(2026,10,10), 100)
        db = _FakeDb([old, future])
        with self.assertRaises(InsufficientStockError):
            await fifo_sell(db, 1, "HISTORY", 3, Decimal(20), sold_at=datetime(2026,9,30), commit=False)
        self.assertEqual((old.remaining_quantity, future.remaining_quantity), (2,100))
        self.assertEqual(db.added, [])

    async def test_same_day_receipt_is_eligible_and_order_head_is_claimed(self):
        same_day = batch(1, datetime(2026,9,30,23,59), 2)
        db = _FakeDb([same_day])
        sale, _ = await fifo_sell(db,1,"SAME-DAY",1,Decimal(20),sold_at=datetime(2026,9,30),commit=False)
        self.assertEqual(sale.order_claim, "SAME-DAY")
        component, _ = await fifo_sell(db,1,"SAME-DAY",1,Decimal(20),sold_at=datetime(2026,9,30),commit=False,claim_order=False)
        self.assertIsNone(component.order_claim)

    def test_import_precheck_uses_sale_dates_and_keeps_whole_combo_pending(self):
        first, second = SimpleNamespace(id=1,sku="A"), SimpleNamespace(id=2,sku="B")
        candidates = [
            {"is_inventory_sale":True,"sold_at":datetime(2026,9,30),"quantity":2,
             "components":[{"product":first,"multiplier":1},{"product":second,"multiplier":1}],"mapping_error":None},
            {"is_inventory_sale":True,"sold_at":datetime(2026,9,30),"quantity":2,
             "components":[{"product":first,"multiplier":1}],"mapping_error":None},
            {"is_inventory_sale":True,"sold_at":datetime(2026,10,11),"quantity":1,
             "components":[{"product":second,"multiplier":1}],"mapping_error":None},
        ]
        _allocate_available_stock(candidates,{1:2,2:100},batches=[batch(1,datetime(2026,9,1),2),batch(2,datetime(2026,10,10),100,2)])
        self.assertFalse(candidates[0]["is_inventory_sale"])
        self.assertIn("销售日", candidates[0]["mapping_error"])
        self.assertTrue(candidates[1]["is_inventory_sale"])
        self.assertTrue(candidates[2]["is_inventory_sale"])

    def test_repeated_product_components_are_checked_as_one_demand(self):
        product = SimpleNamespace(id=1,sku="A")
        candidate = {"is_inventory_sale":True,"quantity":1,"mapping_error":None,
                     "components":[{"product":product,"multiplier":2},{"product":product,"multiplier":2}]}
        _allocate_available_stock([candidate],{1:3})
        self.assertFalse(candidate["is_inventory_sale"])

    def test_real_orm_unique_constraint_allows_combo_details_but_rejects_second_head(self):
        engine = create_engine("sqlite://")
        try:
            Sale.__table__.create(engine)
            values = dict(product_id=1,order_no="BUNDLE",quantity=1,selling_price=20,total_cost=10,
                          platform_fee=0,profit=10,sold_at=datetime(2026,9,30))
            with engine.begin() as connection:
                connection.execute(Sale.__table__.insert().values(**values,order_claim="BUNDLE"))
                connection.execute(Sale.__table__.insert(),[dict(values,order_claim=None),dict(values,order_claim=None)])
            with self.assertRaises(IntegrityError), engine.begin() as connection:
                connection.execute(Sale.__table__.insert().values(**values,order_claim="BUNDLE"))
        finally:
            engine.dispose()

    async def test_concurrent_duplicate_rejects_second_request_without_double_deduction(self):
        inventory = _Inventory()
        inventory.remaining_quantity = 100
        ready = asyncio.Event()
        checks = 0

        class GuardedSession(_Session):
            async def scalar(self, _statement):
                nonlocal checks
                seen = next((sale.id for sale in inventory.sales if sale.order_no == "DUPLICATE"), None)
                checks += 1
                if checks == 2:
                    ready.set()
                await ready.wait()
                return seen

            async def get(self, _model, key):
                return SimpleNamespace(id=key,is_active=True)

            async def flush(self):
                for row in self.added:
                    if isinstance(row, Sale) and row.order_claim and any(
                        old.order_claim == row.order_claim for old in inventory.sales
                    ):
                        raise IntegrityError("test INSERT", {}, Exception("uq_sales_order_claim"))
                await super().flush()

        request = SaleCreate(product_id=1,store_id=7,order_no="DUPLICATE",quantity=1,selling_price=20)
        user = SimpleNamespace(id=9,role="operator",store_id=7)

        async def submit():
            try:
                await create_sale(request,GuardedSession(inventory),user)
                return 200
            except HTTPException as error:
                return error.status_code

        with patch("routers.sales._build_sale_response",side_effect=lambda sale, details: sale):
            outcomes = await asyncio.gather(submit(),submit())
        self.assertEqual(sorted(outcomes), [200,409])
        self.assertEqual(len(inventory.sales),1)
        self.assertEqual(inventory.remaining_quantity,99)
