"""批次编辑真实路由契约；隔离内存SQLite，不运行应用启动或连接业务库。"""
import os
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, select, event
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session

from auth import get_current_user
from database import Base, get_db
from models import InventoryBatch, Product, User, BatchChangeLog, InventoryCostAdjustment, InventoryCostAdjustmentLine
from routers import batches


class AsyncFacade:
    def __init__(self, session):
        self.session = session
        self.info = session.info

    async def scalar(self, statement): return self.session.scalar(statement)
    async def get(self, model, key): return self.session.get(model, key)
    async def execute(self, statement): return self.session.execute(statement)
    def add(self, row): self.session.add(row)
    async def flush(self): self.session.flush()
    async def delete(self, row): self.session.delete(row)
    async def commit(self): self.session.commit()
    async def refresh(self, row, **options): self.session.refresh(row, **options)


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
        self.actor = SimpleNamespace(id=7, role='operator', username='batch-editor')
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
                   'arrived_at': '2026-08-30T00:15:37', 'shipping_date': '2026-08-29T23:59:41',
                   'edit_version': getattr(self.session.get(InventoryBatch, 12), 'edit_version', None)}
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
        log = self.session.scalar(select(BatchChangeLog))
        self.assertEqual((log.batch_id, log.actor_id, log.actor_username, log.actor_role),
                         (12, 7, 'batch-editor', 'operator'))
        self.assertEqual(log.before_values['quantity'], 13)
        self.assertEqual(log.after_values['quantity'], 14)
        self.assertEqual(log.before_values['total_amount'], '34.0054')
        self.assertEqual(log.after_values['unit_cost'], '13.9901')
        self.assertEqual(log.before_values['dingtalk_order_no'], 'OLD')
        self.assertIsNone(log.after_values['dingtalk_order_no'])
        self.assertNotIn('password_hash', log.before_values)
        self.assertIsNotNone(log.created_at)

    async def test_operator_can_edit_other_creators_batch_without_changing_owner(self):
        self.actor.id = 99
        self.actor.username = 'another-operator'
        response = await self.save(quantity=20)
        self.assertEqual(response.status_code, 200, response.text)
        batch = self.session.get(InventoryBatch, 12)
        self.assertEqual((batch.quantity, batch.remaining_quantity, batch.user_id), (20, 20, 7))
        log = self.session.scalar(select(BatchChangeLog))
        self.assertEqual((log.actor_id, log.actor_username), (99, 'another-operator'))
        self.assertEqual(log.before_values['user_id'], 7)
        self.assertEqual(log.after_values['user_id'], 7)

    async def test_admin_can_edit_other_creators_unsold_batch(self):
        self.actor.id = 99
        self.actor.role = 'admin'
        response = await self.save(total_amount='35.0001')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.session.get(InventoryBatch, 12).total_amount, Decimal('35.0001'))

    async def test_viewer_cannot_bypass_page_and_submit(self):
        self.actor.role = 'viewer'
        self.assertEqual((await self.save()).status_code, 403)
        self.assertEqual(self.session.scalars(select(BatchChangeLog)).all(), [])

    async def test_sale_after_list_load_is_rechecked_before_edit(self):
        batch = self.session.get(InventoryBatch, 12)
        opened_version = batch.edit_version
        batch.remaining_quantity = 12
        self.session.commit()
        response = await self.save(quantity=20, edit_version=opened_version)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'batch_edit_conflict')
        batch = self.session.get(InventoryBatch, 12)
        self.assertEqual((batch.quantity, batch.remaining_quantity), (13, 12))
        self.assertEqual(batch.total_amount, Decimal('34.0054'))
        self.assertEqual(self.session.scalars(select(BatchChangeLog)).all(), [])

    async def test_invalid_quantities_and_negative_costs_are_rejected(self):
        for change in ({'quantity': 0}, {'quantity': 1.5}, {'quantity': 2_147_483_648},
                       {'total_amount': '-1'}, {'shipping_cost': '-1'}, {'total_amount': '1e50'},
                       {'quantity': 1, 'total_amount': '99999999', 'shipping_cost': '1'}):
            with self.subTest(change=change):
                self.assertEqual((await self.save(**change)).status_code, 422)
        self.assertEqual(self.session.get(InventoryBatch, 12).quantity, 13)

    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
            return await client.request(method, path, **kwargs)

    async def test_log_insert_failure_rolls_back_the_batch_change(self):
        def reject(_mapper, _connection, _target):
            raise RuntimeError('simulated log write failure')
        event.listen(BatchChangeLog, 'before_insert', reject)
        try:
            with self.assertRaisesRegex(RuntimeError, 'simulated log write failure'):
                await self.save(quantity=99)
        finally:
            event.remove(BatchChangeLog, 'before_insert', reject)
        batch = self.session.get(InventoryBatch, 12)
        self.assertEqual((batch.quantity, batch.remaining_quantity), (13, 13))
        self.assertEqual(self.session.scalars(select(BatchChangeLog)).all(), [])

    async def test_logs_paginate_include_identity_and_survive_void(self):
        await self.save(total_amount='35.0001')
        self.actor.username = 'renamed-operator'
        await self.save(total_amount='36.0001')
        self.actor.id = 99
        response = await self.request('GET', '/batches/12/logs?page_size=1')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual((data['total'], data['page']), (2, 1))
        newest = data['items'][0]
        self.assertEqual(newest['actor_username'], 'renamed-operator')
        self.assertEqual(newest['before_values']['total_amount'], '35.0001')
        self.assertEqual(newest['after_values']['total_amount'], '36.0001')
        self.assertTrue(newest['created_at'].endswith('+00:00'))
        older = (await self.request('GET', '/batches/12/logs?page=99&page_size=1')).json()
        self.assertEqual(older['page'], 2)
        self.assertEqual(older['items'][0]['actor_username'], 'batch-editor')
        response = await self.request('POST', '/batches/12/void', json={
            'edit_version': self.session.get(InventoryBatch, 12).edit_version,
            'reason': '重复入库',
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(self.session.get(InventoryBatch, 12).voided_at)
        logs = (await self.request('GET', '/batches/12/logs')).json()
        self.assertEqual(logs['total'], 3)
        self.assertEqual([row['action'] for row in logs['items']], ['void', 'update', 'update'])

    async def test_viewer_cannot_read_logs_or_change_them(self):
        await self.save()
        self.actor.role = 'viewer'
        self.assertEqual((await self.request('GET', '/batches/12/logs')).status_code, 403)
        self.actor.role = 'admin'
        for method in ('PUT', 'DELETE', 'POST'):
            self.assertEqual((await self.request(method, '/batches/12/logs')).status_code, 405)
        self.assertEqual((await self.request('GET', '/batches/999/logs')).json()['items'], [])
        self.assertEqual((await self.request('GET', '/batches/12/logs?page_size=101')).status_code, 422)

    async def test_cost_adjustment_invalidates_an_older_batch_edit_version(self):
        os.environ['FIFO_FINANCIAL_CONTROLS_ENABLED'] = '1'
        opened_version = self.session.get(InventoryBatch, 12).edit_version
        self.session.add(InventoryCostAdjustment(id=1, request_key='batch-test-adjustment', source_ref='cost',
            category='purchase', allocation_basis='quantity', amount=0, amount_cny=0, currency='CNY',
            exchange_rate=1, document_date=datetime(2026, 9, 1).date(), rate_date=datetime(2026, 9, 1).date(),
            is_final=True, created_by=7))
        self.session.flush()
        self.session.add(InventoryCostAdjustmentLine(adjustment_id=1, batch_id=12, quantity=13,
            allocation_weight=13, amount_cny=0))
        self.session.commit()
        self.actor.id = 99
        response = await self.save(quantity=99, edit_version=opened_version)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'batch_edit_conflict')
        self.assertEqual(self.session.get(InventoryBatch, 12).quantity, 13)
        self.assertEqual(self.session.scalars(select(BatchChangeLog)).all(), [])

    async def test_edit_loads_adjustments_with_current_read_before_version_check(self):
        # 已提交的零额来源也必须使旧版本失效；普通快照读会漏掉它并错误接受旧表单。
        os.environ['FIFO_FINANCIAL_CONTROLS_ENABLED'] = '1'
        opened_version = self.session.get(InventoryBatch, 12).edit_version
        self.session.add(InventoryCostAdjustment(id=1, request_key='current-read-edit', source_ref='source',
            category='purchase', allocation_basis='quantity', amount=0, amount_cny=0, currency='CNY',
            exchange_rate=1, document_date=datetime(2026, 9, 1).date(), rate_date=datetime(2026, 9, 1).date(),
            is_final=True, created_by=7))
        self.session.flush()
        self.session.add(InventoryCostAdjustmentLine(adjustment_id=1, batch_id=12, quantity=13,
            allocation_weight=13, amount_cny=0))
        self.session.commit()
        session = self.session
        locked_reads = []
        class SnapshotFacade(AsyncFacade):
            async def execute(self, statement):
                sql = str(statement.compile(dialect=mysql.dialect()))
                if 'inventory_cost_adjustment_lines' in sql:
                    locked_reads.append(sql)
                    if statement._for_update_arg is None:
                        statement = statement.where(False)
                return await super().execute(statement)
        async def db():
            try:
                yield SnapshotFacade(session)
            finally:
                session.rollback()
        self.app.dependency_overrides[get_db] = db
        response = await self.save(quantity=99, edit_version=opened_version)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'batch_edit_conflict')
        self.assertEqual(len(locked_reads), 1)
        self.assertTrue(locked_reads[0].endswith('FOR UPDATE'))
        self.assertEqual(self.session.get(InventoryBatch, 12).quantity, 13)

    async def test_stale_editor_cannot_overwrite_cost_or_append_a_log(self):
        opened = (await self.request('GET', '/batches')).json()[0]
        first = await self.save(shipping_cost='200.0000')
        self.assertEqual(first.status_code, 200, first.text)
        self.actor.id = 99
        self.actor.username = 'second-editor'
        # 第二个编辑框仅改单号，提交的运费还是打开时的旧值。
        stale = await self.save(edit_version=opened['edit_version'], dingtalk_order_no='NEW')
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()['detail']['code'], 'batch_edit_conflict')
        batch = self.session.get(InventoryBatch, 12)
        self.assertEqual(batch.shipping_cost, Decimal('200.0000'))
        self.assertEqual(batch.dingtalk_order_no, 'OLD')
        self.assertEqual(len(self.session.scalars(select(BatchChangeLog)).all()), 1)
        # 重新读取并核对后保存，保留前一人的修改；新日志准确链接前后状态。
        latest = (await self.request('GET', '/batches?page=1')).json()['items'][0]
        self.assertNotEqual(latest['edit_version'], opened['edit_version'])
        self.assertEqual(latest['edit_version'], first.json()['edit_version'])
        saved = await self.save(edit_version=latest['edit_version'], shipping_cost='200.0000',
                                dingtalk_order_no='NEW')
        self.assertEqual(saved.status_code, 200, saved.text)
        logs = self.session.scalars(select(BatchChangeLog).order_by(BatchChangeLog.id)).all()
        self.assertEqual(logs[1].before_values, logs[0].after_values)
        self.assertEqual(logs[1].after_values['shipping_cost'], '200.0000')
        self.assertNotIn('edit_version', logs[1].after_values)

    async def test_missing_or_invalid_version_cannot_bypass_conflict_check(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
            missing = await client.put('/batches/12', json={'product_id': 42, 'quantity': 99})
        self.assertEqual(missing.status_code, 428, missing.text)
        response = await self.save(edit_version=None, quantity=99)
        self.assertEqual(response.status_code, 428, response.text)
        self.assertEqual(response.json()['detail']['code'], 'batch_edit_version_required')
        invalid = await self.save(edit_version='invalid', quantity=99)
        self.assertEqual(invalid.status_code, 422, invalid.text)
        mismatch = await self.save(edit_version='0' * 64, quantity=99)
        self.assertEqual(mismatch.status_code, 409, mismatch.text)
        self.assertEqual(self.session.get(InventoryBatch, 12).quantity, 13)
        self.assertEqual(self.session.scalars(select(BatchChangeLog)).all(), [])

    async def test_version_is_checked_against_locked_row_not_stale_identity_map(self):
        batch = self.session.get(InventoryBatch, 12)
        version = batch.edit_version
        self.session.commit()
        # 另一事务已提交，但本请求会话仍持有旧ORM对象。
        with self.engine.begin() as connection:
            connection.execute(InventoryBatch.__table__.update().where(
                InventoryBatch.id == 12).values(shipping_cost=Decimal('200.0000')))
        self.assertEqual(batch.shipping_cost, Decimal('161.8555'))
        response = await self.save(edit_version=version)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.session.get(InventoryBatch, 12).shipping_cost, Decimal('200.0000'))
        self.assertEqual(self.session.scalars(select(BatchChangeLog)).all(), [])

    async def test_version_uses_persisted_original_fields_even_with_cost_display_wrapper(self):
        from schemas import BatchResponse
        from services.cost_layers import AdjustedBatch
        batch = self.session.get(InventoryBatch, 12)
        original = BatchResponse.model_validate(batch)
        adjusted = BatchResponse.model_validate(AdjustedBatch(batch, [(SimpleNamespace(
            id=55, amount_cny=Decimal('13'), quantity=13), SimpleNamespace(
                id=1, category='purchase', is_final=True, reverses_id=None))]))
        self.assertNotEqual(adjusted.unit_cost, original.unit_cost)
        self.assertNotEqual(adjusted.edit_version, original.edit_version)
        # 相同金额不同小数位与 ORM 重新载入不应造成假冲突。
        batch.last_mile_cost = Decimal('0.000000')
        self.assertEqual(batch.edit_version, original.edit_version)
        self.session.expire_all()
        self.assertEqual(self.session.get(InventoryBatch, 12).edit_version, original.edit_version)
        saved = await self.save(edit_version=original.edit_version)
        self.assertEqual(saved.status_code, 200, saved.text)

    async def test_retired_delete_does_not_open_inventory_transaction(self):
        async def unexpected_db():
            raise AssertionError('retired DELETE must not access inventory')
            yield

        self.app.dependency_overrides[get_db] = unexpected_db
        response = await self.request('DELETE', '/batches/12')
        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(response.json()['detail']['code'], 'batch_delete_retired')
        self.assertEqual(self.session.get(InventoryBatch, 12).quantity, 13)
        self.assertEqual(self.session.scalars(select(BatchChangeLog)).all(), [])

    async def test_current_cost_read_without_adjustments_allows_edit(self):
        os.environ['FIFO_FINANCIAL_CONTROLS_ENABLED'] = '1'
        response = await self.save(shipping_cost='200.0000')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.session.scalars(select(BatchChangeLog)).all()), 1)

    async def test_unchanged_save_refreshes_with_current_read_for_audit_snapshot(self):
        session = self.session
        refresh_options = []

        class RefreshFacade(AsyncFacade):
            async def refresh(self, row, **options):
                refresh_options.append(options)
                await super().refresh(row, **options)
        async def db():
            try:
                yield RefreshFacade(session)
            finally:
                session.rollback()
        self.app.dependency_overrides[get_db] = db
        response = await self.save()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(refresh_options[0], {'with_for_update': True})
        log = self.session.scalar(select(BatchChangeLog))
        self.assertEqual(log.before_values, log.after_values)

    async def test_create_still_accepts_legacy_payload_without_edit_version(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
            response = await client.post('/batches', json={'product_id': 42, 'quantity': 2, 'purchase_price': '3'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertRegex(response.json()['edit_version'], r'^[a-f0-9]{64}$')


if __name__ == '__main__':
    unittest.main()
