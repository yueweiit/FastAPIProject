"""隔离 SQLite/真实路由：历史状态筛选、旧客户端、店铺权限及只读边界。"""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import event, select

from models import InventoryBatch, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation
from tests import test_sales_import_store as fixtures


class ImportHistoryTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ImportStoreTests.setUp
    tearDown = fixtures.ImportStoreTests.tearDown
    request = fixtures.ImportStoreTests.request
    upload = fixtures.ImportStoreTests.upload

    def seed(self):
        # 成功旧记录在最近 30 条已撤销记录之外，含旧空归属及待确认记录。
        base = datetime(2026, 9, 1)
        self.session.add_all([
            SalesImportBatch(id=1, user_id=None, store_id=None, file_name='legacy.csv',
                file_type='csv', status='completed', imported_at=base),
            SalesImportBatch(id=2, user_id=2, store_id=7, file_name='review.csv',
                file_type='order_detail', status='completed', pending_confirmation=4,
                imported_at=base),
            SalesImportBatch(id=3, user_id=1, store_id=8, file_name='other-store.csv',
                file_type='order_detail', status='completed', imported_at=base),
        ])
        self.session.add_all(SalesImportBatch(id=i, user_id=1, store_id=7 if i % 2 else 8,
            file_name=f'rollback-{i}.csv', file_type='order_detail', status='rolled_back',
            imported_at=base + timedelta(days=i)) for i in range(4, 36))
        self.session.commit()

    async def history(self, **params):
        response = await self.request('GET', '/sales/imports', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_completed_filter_applies_before_limit_and_keeps_pending_and_legacy(self):
        self.seed()
        rows = await self.history(status='completed', limit=30)
        self.assertEqual([row['id'] for row in rows], [3, 2, 1])
        self.assertEqual(rows[1]['pending_confirmation'], 4)
        self.assertIsNone(rows[2]['store_name'])
        self.assertTrue(all(row['status'] == 'completed' for row in rows))
        self.assertEqual([r['id'] for r in await self.history(status='completed', limit=2)], [3, 2])

    async def test_omitted_filter_preserves_all_statuses_and_existing_limit(self):
        self.seed()
        self.assertEqual(len(await self.history()), 20)
        rows = await self.history(limit=100)
        self.assertEqual([row['id'] for row in rows], list(range(35, 0, -1)))
        self.assertEqual({row['status'] for row in rows}, {'completed', 'rolled_back'})
        rows = await self.history(status='rolled_back', limit=30)
        self.assertEqual([row['id'] for row in rows], list(range(35, 5, -1)))
        self.assertTrue(all(row['status'] == 'rolled_back' and not row['can_rollback'] for row in rows))

    async def test_each_filter_keeps_store_permissions_for_operator_and_viewer(self):
        self.seed()
        for role in ('operator', 'viewer'):
            for store in (7, 8):
                self.actor = SimpleNamespace(id=2, role=role, store_id=store)
                for status in (None, 'completed', 'rolled_back'):
                    with self.subTest(role=role, store=store, status=status):
                        rows = await self.history(limit=100, **({'status': status} if status else {}))
                        self.assertTrue(rows)
                        self.assertEqual({row['store_name'] for row in rows}, {f'店铺{store}'})
                        self.assertTrue(all(self.session.get(SalesImportBatch, row['id']).store_id == store for row in rows))
                        if status:
                            self.assertEqual({row['status'] for row in rows}, {status})
                        if role == 'viewer':
                            self.assertFalse(any(row['can_rollback'] for row in rows))
            self.actor.store_id = None
            for params in ({}, {'status': 'completed'}, {'status': 'rolled_back'}):
                self.assertEqual((await self.request('GET', '/sales/imports', params=params)).status_code, 403)

    async def test_invalid_status_or_limit_rejected_and_empty_result_stays_array(self):
        for params in ({'status': 'success'}, {'status': ''}, {'status': 'unknown'},
                       {'status': "completed' OR 1=1"}, {'status': '成功'},
                       {'status': 'completed', 'limit': 0}, {'status': 'rolled_back', 'limit': 101}):
            with self.subTest(params=params):
                response = await self.request('GET', '/sales/imports', params=params)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(await self.history(status='completed'), [])
        self.assertEqual(await self.history(status='rolled_back'), [])

    async def test_filtering_after_import_is_read_only_and_rollback_still_restores_fifo(self):
        response = await self.upload(7, detail=True)
        self.assertEqual(response.status_code, 200, response.text)
        batch_id = response.json()['import_batch_id']
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 9)
        models = (InventoryBatch, Sale, SaleCostDetail, SettlementEntry, SettlementEntryAllocation, SalesImportBatch)
        def snapshot():
            return {model.__tablename__: list(self.session.execute(select(*model.__table__.columns).order_by(model.id)))
                    for model in models}
        before = snapshot()
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            self.assertEqual([row['id'] for row in await self.history(status='completed')], [batch_id])
            self.assertEqual(await self.history(status='rolled_back'), [])
            self.assertEqual(len(await self.history()), 1)
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual({s.strip().split()[0].upper() for s in statements}, {'SELECT'})
        self.assertNotIn('result_snapshot', '\n'.join(statements))
        self.assertEqual(snapshot(), before)
        response = await self.request('POST', f'/sales/imports/{batch_id}/rollback')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        self.assertEqual(self.session.scalars(select(SaleCostDetail)).all(), [])
        self.assertEqual(await self.history(status='completed'), [])
        self.assertEqual([r['id'] for r in await self.history(status='rolled_back')], [batch_id])
        self.assertEqual([r['id'] for r in await self.history()], [batch_id])
        result = await self.request('GET', f'/sales/imports/{batch_id}/result')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['status'], 'rolled_back')
        self.assertEqual(result.json()['result']['sales_created'], 1)


if __name__ == '__main__':
    unittest.main()
