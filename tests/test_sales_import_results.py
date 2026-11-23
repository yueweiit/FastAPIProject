"""隔离 SQLite/真实路由：结果快照、权限、重试/撤销兼容及只读回看。"""
import ast
import copy
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError

from migrations.add_sales_import_result import DDL
from models import InventoryBatch, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation
from tests import test_sales_import_store as fixtures
from tests import test_sales_name_import as names


class ImportResultTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ImportStoreTests.setUp
    tearDown = fixtures.ImportStoreTests.tearDown
    request = fixtures.ImportStoreTests.request
    upload = names.NameImportTests.upload

    async def import_review(self):
        rows = [{'order': 'SALE', 'name': '商品', 'quantity': 2},
                {'order': 'PENDING-A', 'name': '<未建档商品>', 'quantity': 1},
                {'order': 'PENDING-B', 'name': '另一个未建档商品', 'quantity': 1}]
        rows.extend({'order': f'REFUND-{i}', 'name': '商品', 'quantity': 1, 'net': -20, 'total': -18}
                    for i in range(8))
        response = await self.upload(rows)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def result(self, batch_id):
        response = await self.request('GET', f'/sales/imports/{batch_id}/result')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        return response.json()

    def business_snapshot(self):
        return {model.__tablename__: list(self.session.execute(select(*model.__table__.columns).order_by(model.id)))
                for model in (InventoryBatch, Sale, SaleCostDetail, SettlementEntry, SettlementEntryAllocation)}

    async def test_complete_details_persist_in_same_commit_and_reload_without_writes(self):
        commit = self.db.commit
        snapshots_at_commit = []

        async def checked_commit():
            batch = self.session.scalar(select(SalesImportBatch))
            snapshots_at_commit.append(copy.deepcopy(batch.result_snapshot))
            await commit()

        with patch.object(self.db, 'commit', checked_commit):
            imported = await self.import_review()
        self.assertEqual(snapshots_at_commit, [imported])
        self.assertEqual(len(imported['warnings']), 8)
        self.assertEqual(len(imported['pending_details']), 2)
        self.assertEqual([row['row'] for row in imported['pending_details']], [3, 4])
        self.assertTrue(all(row['message'] for row in imported['pending_details']))
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 8)
        before = self.business_snapshot()
        self.session.expunge_all()
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement.strip().split()[0].upper())
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            saved = await self.result(imported['import_batch_id'])
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(saved['result'], imported)
        self.assertEqual(saved['store_name'], '店铺7')
        self.assertEqual(set(statements), {'SELECT'})
        self.assertEqual(self.business_snapshot(), before)

    async def test_history_list_does_not_load_or_expose_large_snapshot(self):
        await self.import_review()
        self.session.expunge_all()
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            response = await self.request('GET', '/sales/imports')
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(statements)
        self.assertNotIn('result_snapshot', '\n'.join(statements))
        self.assertNotIn('result', response.json()[0])

    async def test_special_characters_survive_import_and_result_read_without_extra_fifo(self):
        missing_name = "未建档$& $` $' $$ {store} {message} <literal>"
        response = await self.upload([
            {'order': 'VALID', 'name': '商品', 'quantity': 2},
            {'order': 'SPECIAL-$&', 'name': missing_name, 'quantity': 1},
        ])
        self.assertEqual(response.status_code, 200, response.text)
        imported = response.json()
        self.assertEqual((imported['sales_created'], imported['pending_confirmation']), (1, 1))
        self.assertEqual(imported['pending_details'][0]['message'], f'未找到本地商品映射: 产品名={missing_name}')
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 8)
        before = self.business_snapshot()
        saved = await self.result(imported['import_batch_id'])
        self.assertEqual(saved['result'], imported)
        self.assertEqual(self.business_snapshot(), before)

    async def test_retry_changes_current_counts_but_not_import_time_result(self):
        imported = (await self.upload([{'name': '商品', 'quantity': 31}])).json()
        entry = self.session.scalar(select(SettlementEntry))
        self.assertEqual(imported['pending_confirmation'], 1)
        self.session.add(InventoryBatch(id=4, product_id=1, batch_no='EXTRA', quantity=10,
            remaining_quantity=10, purchase_price=10, unit_cost=10, arrived_at=datetime(2026, 9, 1)))
        self.session.commit()
        response = await self.request('POST', '/sales/pending-confirmations/retry', json={'ids': [entry.id]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['confirmed_rows'], 1)
        before = self.business_snapshot()
        saved = await self.result(imported['import_batch_id'])
        self.assertEqual((saved['pending_confirmation'], saved['sales_created']), (0, 1))
        self.assertEqual(saved['result'], imported)
        self.assertEqual((saved['result']['pending_confirmation'], saved['result']['sales_created']), (1, 0))
        self.assertEqual(self.session.get(InventoryBatch, 4).remaining_quantity, 9)
        self.assertEqual(self.business_snapshot(), before)

    async def test_rollback_preserves_original_details_and_restores_only_actual_stock(self):
        imported = await self.import_review()
        batch_id = imported['import_batch_id']
        response = await self.request('POST', f'/sales/imports/{batch_id}/rollback')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['restored_quantity'], 2)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
        self.assertEqual(self.session.scalars(select(SettlementEntry)).all(), [])
        before = self.business_snapshot()
        saved = await self.result(batch_id)
        self.assertEqual(saved['status'], 'rolled_back')
        self.assertFalse(saved['can_rollback'])
        self.assertEqual(saved['result'], imported)
        self.assertEqual(self.business_snapshot(), before)

    async def test_result_permissions_match_history_including_unknown_and_unbound(self):
        imported = await self.import_review()
        batch_id = imported['import_batch_id']
        for role in ('operator', 'viewer'):
            self.actor = SimpleNamespace(id=2, username='user', role=role, store_id=7)
            self.assertEqual((await self.result(batch_id))['result'], imported)
            self.actor.store_id = 8
            for key in (batch_id, 99999):
                response = await self.request('GET', f'/sales/imports/{key}/result')
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()['detail']['code'], 'sales_import_result_not_found')
            self.actor.store_id = None
            self.assertEqual((await self.request('GET', f'/sales/imports/{batch_id}/result')).status_code, 403)

    async def test_old_batch_is_explicitly_unavailable_not_an_empty_success_result(self):
        batch = SalesImportBatch(user_id=1, store_id=7, file_name='old.csv', file_type='order_detail',
            settlement_rows=1554, sales_created=1808, pending_confirmation=13)
        self.session.add(batch)
        self.session.commit()
        saved = await self.result(batch.id)
        self.assertIsNone(saved['result'])
        self.assertEqual((saved['settlement_rows'], saved['sales_created'], saved['pending_confirmation']), (1554, 1808, 13))

    async def test_repeated_import_keeps_separate_snapshot_and_never_deducts_twice(self):
        original = await self.import_review()
        before = self.business_snapshot()
        repeated = await self.import_review()
        self.assertEqual((repeated['settlement_rows'], repeated['sales_created'], repeated['skipped_duplicates']), (0, 0, 11))
        self.assertEqual(self.business_snapshot(), before)
        self.assertEqual((await self.result(original['import_batch_id']))['result'], original)
        self.assertEqual((await self.result(repeated['import_batch_id']))['result'], repeated)

    async def test_snapshot_commit_failure_rolls_back_entire_import(self):
        before = self.business_snapshot()
        with patch.object(self.db, 'commit', side_effect=IntegrityError('commit', {}, Exception('test conflict'))):
            response = await self.upload([{'name': '商品', 'quantity': 2}])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.session.scalars(select(SalesImportBatch)).all(), [])
        self.assertEqual(self.business_snapshot(), before)


class ImportResultSchemaTests(unittest.TestCase):
    def test_additive_column_keeps_legacy_data_and_is_registered_for_startup(self):
        engine = create_engine('sqlite://')
        try:
            with engine.begin() as connection:
                connection.execute(text('CREATE TABLE sales_import_batches (id INTEGER PRIMARY KEY, file_name TEXT)'))
                connection.execute(text("INSERT INTO sales_import_batches VALUES (1, 'legacy.csv')"))
                connection.execute(text(DDL))
                self.assertEqual(connection.execute(text('SELECT id, file_name, result_snapshot FROM sales_import_batches')).one(),
                                 (1, 'legacy.csv', None))
        finally:
            engine.dispose()
        source = ast.parse((Path(__file__).resolve().parents[1] / 'migrate.py').read_text(encoding='utf-8'))
        migrations = next(ast.literal_eval(node.value) for node in source.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'MIGRATIONS' for target in node.targets))
        self.assertIn(('sales_import_batches', 'result_snapshot', 'JSON NULL'), migrations)


if __name__ == '__main__':
    unittest.main()
