"""批次编辑真实路由契约；隔离内存SQLite，不运行应用启动或连接业务库。"""
import os
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from auth import get_current_user
from database import Base, get_db
from models import InventoryBatch, Product, User
from routers import batches


class AsyncFacade:
    def __init__(self, session):
        self.session = session
        self.info = session.info

    async def scalar(self, statement): return self.session.scalar(statement)
    async def commit(self): self.session.commit()
    async def refresh(self, row): self.session.refresh(row)


class BatchEditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flag = patch.dict(os.environ, {'FIFO_FINANCIAL_CONTROLS_ENABLED': '0'})
        self.flag.start()
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.session.add_all([User(id=7, username='batch-editor', role='operator', password_hash='test'),
                              Product(id=42, sku='HISTORY', name='历史批次测试')])
        self.session.flush()
        self.session.add(InventoryBatch(id=12, product_id=42, user_id=7, batch_no='HISTORY-01',
            quantity=13, remaining_quantity=13, purchase_price=Decimal('2.6158'),
            total_amount=Decimal('34.0054'), shipping_cost=Decimal('161.8555'),
            last_mile_cost=0, other_cost=0, unit_cost=Decimal('15.0662'),
            dingtalk_order_no='OLD', shipping_cost_no='HEAD', last_mile_cost_no='LAST', other_cost_no='OTHER',
            shipping_date=datetime(2026, 8, 29, 23, 59, 41), arrived_at=datetime(2026, 8, 30, 0, 15, 37)))
        self.session.commit()
        self.actor = SimpleNamespace(id=7, role='operator')
        self.app = FastAPI()
        self.app.include_router(batches.router)

        async def actor(): return self.actor
        async def db():
            try:
                yield AsyncFacade(self.session)
            finally:
                self.session.rollback()
        self.app.dependency_overrides[get_current_user] = actor
        self.app.dependency_overrides[get_db] = db

    def tearDown(self):
        self.session.close()
        self.engine.dispose()
        self.flag.stop()

    async def save(self, **changes):
        payload = {'product_id': 42, 'quantity': 13, 'total_amount': '34.0054',
                   'shipping_cost': '161.8555', 'last_mile_cost': '0', 'other_cost': '0',
                   'arrived_at': '2026-08-30T00:15:37', 'shipping_date': '2026-08-29T23:59:41'}
        payload.update(changes)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
            return await client.put('/batches/12', json=payload)

    async def test_owner_can_update_quantity_and_exact_costs_and_clear_order_numbers(self):
        response = await self.save(quantity=14, dingtalk_order_no=None, shipping_cost_no=None,
                                   last_mile_cost_no=None, other_cost_no=None)
        self.assertEqual(response.status_code, 200, response.text)
        batch = self.session.get(InventoryBatch, 12)
        self.assertEqual((batch.quantity, batch.remaining_quantity), (14, 14))
        self.assertEqual(batch.total_amount, Decimal('34.0054'))
        self.assertEqual(batch.shipping_cost, Decimal('161.8555'))
        self.assertEqual(batch.unit_cost, Decimal('13.9901'))
        self.assertEqual(batch.arrived_at, datetime(2026, 8, 30, 0, 15, 37))
        self.assertEqual(batch.shipping_date, datetime(2026, 8, 29, 23, 59, 41))
        for field in ('dingtalk_order_no', 'shipping_cost_no', 'last_mile_cost_no', 'other_cost_no'):
            self.assertIsNone(getattr(batch, field))

    async def test_operator_cannot_edit_other_creators_batch(self):
        self.actor.id = 99
        response = await self.save(quantity=20)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.session.get(InventoryBatch, 12).quantity, 13)

    async def test_admin_can_edit_other_creators_unsold_batch(self):
        self.actor.id = 99
        self.actor.role = 'admin'
        response = await self.save(total_amount='35.0001')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.session.get(InventoryBatch, 12).total_amount, Decimal('35.0001'))

    async def test_viewer_cannot_bypass_page_and_submit(self):
        self.actor.role = 'viewer'
        self.assertEqual((await self.save()).status_code, 403)

    async def test_sale_after_list_load_is_rechecked_before_edit(self):
        batch = self.session.get(InventoryBatch, 12)
        batch.remaining_quantity = 12
        self.session.commit()
        response = await self.save(quantity=20)
        self.assertEqual(response.status_code, 400)
        batch = self.session.get(InventoryBatch, 12)
        self.assertEqual((batch.quantity, batch.remaining_quantity), (13, 12))
        self.assertEqual(batch.total_amount, Decimal('34.0054'))

    async def test_invalid_quantities_and_negative_costs_are_rejected(self):
        for change in ({'quantity': 0}, {'quantity': 1.5}, {'total_amount': '-1'}, {'shipping_cost': '-1'}):
            with self.subTest(change=change):
                self.assertEqual((await self.save(**change)).status_code, 422)
        self.assertEqual(self.session.get(InventoryBatch, 12).quantity, 13)


if __name__ == '__main__':
    unittest.main()
