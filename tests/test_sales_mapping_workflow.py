"""店铺编码映射、版本、只读预览和原记录确认；隔离SQLite，不写业务库。"""
import unittest
import os
import csv
import io
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, select
from openpyxl import Workbook

from models import BusinessOperationLog, InventoryBatch, Product, Sale, SaleCostDetail, SalesProductMapping, SalesProductMappingComponent, SalesImportBatch, SettlementEntry
from tests import test_sales_import_store as fixtures
from tests.test_sales_name_import import source_file


class SalesMappingWorkflowTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ImportStoreTests.setUp
    tearDown = fixtures.ImportStoreTests.tearDown
    request = fixtures.ImportStoreTests.request

    async def upload(self, rows, *, preview=False, token=None, store=7, headers=()):
        data = {'store_id': str(store)}
        if token:
            data['preview_token'] = token
        return await self.request('POST', '/sales/import-preview' if preview else '/sales/import', data=data,
            files={'file': ('source.csv', source_file(rows, headers), 'text/csv')})

    async def mapping(self, *, value='EXT', store=7, parts=None, start='2026-01-01', expected=None,
                      kind='platform_sku', unique=False, active=True):
        return await self.request('POST', '/sales/product-mappings', json={
            'store_id': store, 'key_kind': kind, 'key_value': value, 'effective_from': start,
            'expected_version_id': expected, 'source_unit': '套', 'confirm_name_unique': unique,
            'is_active': active, 'components': parts or [{'product_id': 1, 'multiplier': 2}]})

    def second_product(self):
        self.session.add(Product(id=2, sku='SECOND', name='商品', specification='紫色'))
        self.session.add(InventoryBatch(id=4, product_id=2, batch_no='SECOND', quantity=10,
            remaining_quantity=10, purchase_price=10, unit_cost=10, arrived_at=datetime(2026, 1, 1)))
        self.session.commit()

    async def test_unknown_code_does_not_guess_by_unique_name_or_old_global_mapping(self):
        result = await self.upload([{'sku': 'PLAT', 'name': '商品', 'quantity': 1}])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((result.json()['sales_created'], result.json()['pending_confirmation']), (0, 1))
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)

    async def test_confirmed_code_bundle_ignores_name_and_deducts_once(self):
        self.second_product()
        saved = await self.mapping(parts=[{'product_id': 1, 'multiplier': 2}, {'product_id': 2, 'multiplier': 1}])
        self.assertEqual(saved.status_code, 201, saved.text)
        rows = [{'sku': 'EXT', 'name': '名称已修改，无需补规格', 'quantity': 3}]
        result = await self.upload(rows)
        self.assertEqual(result.json()['sales_created'], 2)
        sales = self.session.scalars(select(Sale).order_by(Sale.product_id)).all()
        self.assertEqual([(s.product_id, s.quantity) for s in sales], [(1, 6), (2, 3)])
        entry = self.session.scalar(select(SettlementEntry))
        self.assertEqual(entry.raw_data['_product_resolution']['version_id'], saved.json()['id'])
        repeated = await self.upload(rows)
        self.assertEqual(repeated.json()['skipped_duplicates'], 1)
        self.assertEqual(len(self.session.scalars(select(SaleCostDetail)).all()), 2)

    async def test_equivalent_local_expressions_share_event_and_order_identity(self):
        self.second_product()
        expressions = ('SKU*2+SECOND', 'SECOND + SKU + SKU', ' SKU × 2 ＋ SECOND ', 'SECOND*1+SKUx2')
        for index, expression in enumerate(expressions):
            row = {'sku': '', '本地SKU': expression, 'order': 'LOCAL-BUNDLE', 'quantity': 1}
            preview = await self.upload([row], headers=('本地SKU',), preview=True)
            self.assertEqual(preview.status_code, 200, preview.text)
            result = await self.upload([row], headers=('本地SKU',), token=preview.json()['preview_token'])
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['skipped_duplicates'], 0 if index == 0 else 1)
        self.assertEqual([(s.product_id, s.quantity) for s in self.session.scalars(select(Sale).order_by(Sale.product_id))], [(1, 2), (2, 1)])
        self.assertEqual(len(self.session.scalars(select(SettlementEntry)).all()), 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 8)

    async def test_equivalent_local_expressions_in_one_file_are_rejected_without_writes(self):
        rows = [{'sku': '', '本地SKU': code, 'order': 'SAME', 'quantity': 1} for code in ('SKU*2', 'SKU + SKU')]
        result = await self.upload(rows, headers=('本地SKU',))
        self.assertEqual(result.status_code, 400, result.text)
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        self.assertEqual(self.session.scalars(select(SettlementEntry)).all(), [])

    async def test_old_raw_expression_keys_are_recognized_without_rewriting_history(self):
        from routers.sales import _source_key, _order_no
        from services.sales_mapping import order_identifier
        await self.upload([{'sku': '', '本地SKU': 'SKU*2', 'quantity': 1}], headers=('本地SKU',))
        entry = self.session.scalar(select(SettlementEntry))
        sale = self.session.scalar(select(Sale))
        # 模拟修复前已经保存的原文哈希；兼容无组件快照的旧记录。
        entry.raw_data = {k: v for k, v in entry.raw_data.items() if k != '_product_resolution'}
        entry.source_key = _source_key(entry.raw_data, 'order_detail')
        sale.order_no = sale.order_claim = _order_no(entry.order_id, order_identifier(entry.raw_data))
        self.session.commit()
        original = (entry.source_key, sale.order_no, sale.total_cost)
        for extra, status in (({}, 200), ({'total': 170}, 400)):
            result = await self.upload([{'sku': '', '本地SKU': 'SKU + SKU', 'quantity': 1, **extra}], headers=('本地SKU',))
            self.assertEqual(result.status_code, status, result.text)
            if status == 200:
                self.assertEqual(result.json()['skipped_duplicates'], 1)
        self.assertEqual((entry.source_key, sale.order_no, sale.total_cost), original)
        self.assertEqual(len(self.session.scalars(select(Sale)).all()), 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 8)

    async def test_pending_equivalent_expressions_cannot_confirm_twice(self):
        self.session.get(InventoryBatch, 1).remaining_quantity = 0
        self.session.get(InventoryBatch, 2).remaining_quantity = 0
        self.session.get(InventoryBatch, 3).remaining_quantity = 0
        self.session.commit()
        for code, total in (('SKU*2', 180), ('SKU + SKU', 170)):
            result = await self.upload([{'sku': '', '本地SKU': code, 'quantity': 1, 'total': total}], headers=('本地SKU',))
            self.assertEqual(result.json()['pending_confirmation'], 1)
        ids = [e.id for e in self.session.scalars(select(SettlementEntry).order_by(SettlementEntry.id))]
        self.session.get(InventoryBatch, 1).remaining_quantity = 10
        self.session.commit()
        for index, entry_id in enumerate(ids):
            preview = await self.request('POST', '/sales/pending-confirmations/preview', json={'entry_ids': [entry_id]})
            result = await self.request('POST', '/sales/pending-confirmations/retry', json={
                'entry_ids': [entry_id], 'preview_token': preview.json()['preview_token']})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['sales_created'], 1 if index == 0 else 0)
            self.assertEqual(result.json()['pending_rows'], index)
        self.assertEqual(len(self.session.scalars(select(Sale)).all()), 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 8)

    async def test_literal_sku_with_operators_keeps_exact_match_and_new_order_is_allowed(self):
        self.second_product()
        self.session.get(Product, 2).sku = 'SKU*2'
        self.session.commit()
        for code in ('SKU*2', 'SKU + SKU'):
            result = await self.upload([{'sku': '', '本地SKU': code, 'quantity': 1}], headers=('本地SKU',))
            self.assertEqual(result.json()['sales_created'], 1, result.text)
        result = await self.upload([{'sku': '', '本地SKU': 'SKU + SKU', 'order': 'NEW-ORDER', 'quantity': 1}], headers=('本地SKU',))
        self.assertEqual(result.json()['sales_created'], 1, result.text)
        self.assertEqual(sum(s.quantity for s in self.session.scalars(select(Sale))), 5)

    async def test_renamed_local_sku_reuses_saved_component_identity(self):
        await self.upload([{'sku': '', '本地SKU': 'SKU*2', 'quantity': 1}], headers=('本地SKU',))
        self.session.get(Product, 1).sku = 'RENAMED'
        self.session.commit()
        result = await self.upload([{'sku': '', '本地SKU': 'RENAMED + RENAMED', 'quantity': 1}], headers=('本地SKU',))
        self.assertEqual(result.json()['skipped_duplicates'], 1, result.text)
        self.assertEqual(len(self.session.scalars(select(Sale)).all()), 1)

    async def test_mapping_is_store_scoped_and_cannot_be_written_by_viewer_or_other_store(self):
        self.assertEqual((await self.mapping()).status_code, 201)
        result = await self.upload([{'sku': 'EXT', 'name': '商品', 'quantity': 1}], store=8)
        self.assertEqual(result.json()['pending_confirmation'], 1)
        self.actor = SimpleNamespace(id=2, username='operator', role='operator', store_id=7)
        self.assertEqual((await self.mapping(store=8)).status_code, 403)
        self.assertEqual((await self.request('GET', '/sales/product-mappings?store_id=8')).status_code, 403)
        self.actor.role = 'viewer'
        self.assertEqual((await self.mapping(value='NEW')).status_code, 403)

    async def test_versions_use_sale_date_preserve_past_and_reject_stale_editor(self):
        first = (await self.mapping()).json()['id']
        second = await self.mapping(start='2026-10-01', expected=first, parts=[{'product_id': 1, 'multiplier': 3}])
        self.assertEqual(second.status_code, 201, second.text)
        conflict = await self.mapping(start='2026-11-01', expected=first)
        self.assertEqual(conflict.status_code, 409)
        await self.upload([{'sku': 'EXT', 'name': '任意标题', 'quantity': 1},
            {'sku': 'EXT', 'name': '任意标题', 'quantity': 1, 'order': 'OCT', 'ordered_on': '2026-10-02'}])
        self.assertEqual([s.quantity for s in self.session.scalars(select(Sale).order_by(Sale.id))], [2, 3])
        self.assertEqual(len(self.session.scalars(select(SalesProductMapping)).all()), 2)

    async def test_name_only_needs_confirmed_unique_alias_and_separate_identity(self):
        self.assertEqual((await self.mapping(kind='product_name', value='商品')).status_code, 422)
        self.assertEqual((await self.mapping(kind='product_name', value='商品', unique=True)).status_code, 201)
        result = await self.upload([{'sku': '', 'name': '商品', 'quantity': 1},
                                    {'sku': '', 'name': '另一个名称', 'quantity': 1}])
        self.assertEqual((result.json()['sales_created'], result.json()['pending_confirmation']), (1, 1))
        self.assertEqual(len(self.session.scalars(select(SettlementEntry)).all()), 2)

    async def test_local_sku_is_exact_and_explicit_spec_or_mapping_conflict_stops_deduction(self):
        self.second_product()
        await self.mapping()
        result = await self.upload([{'sku': 'UNKNOWN', 'name': '改名', '本地SKU': 'SECOND', 'quantity': 1},
            {'sku': 'EXT', '本地SKU': 'SECOND', 'quantity': 1},
            {'sku': 'EXT', '规格/型号': '错误规格', 'quantity': 1}], headers=('本地SKU', '规格/型号'))
        self.assertEqual((result.json()['sales_created'], result.json()['pending_confirmation']), (1, 2))
        self.assertEqual(self.session.get(InventoryBatch, 4).remaining_quantity, 9)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)

    async def test_import_preview_only_reads_and_token_rejects_inventory_changes(self):
        await self.mapping()
        rows = [{'sku': 'EXT', 'name': '任意标题', 'quantity': 1}]
        statements = []
        def capture(_c, _cur, statement, _p, _ctx, _many):
            statements.append(statement)
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            with patch('routers.sales.get_cny_rates', new=AsyncMock()) as rates:
                preview = await self.upload(rows, preview=True)
                rates.assert_not_called()
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(all(s.lstrip().upper().startswith('SELECT') for s in statements), statements)
        plan = preview.json()
        self.assertEqual(plan['rows'][0]['components'][0]['deduct_quantity'], 2)
        self.assertEqual(plan['rows'][0]['components'][0]['available_quantity'], 30)
        self.session.get(InventoryBatch, 1).remaining_quantity = 9
        self.session.commit()
        changed = await self.upload(rows, token=plan['preview_token'])
        self.assertEqual(changed.status_code, 409, changed.text)
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        fresh = (await self.upload(rows, preview=True)).json()
        self.assertEqual((await self.upload(rows, token=fresh['preview_token'])).json()['sales_created'], 1)

    async def test_pending_preview_save_mapping_and_retry_other_rows_without_reupload(self):
        await self.upload([{'sku': 'EXT', 'name': '同一来源', 'quantity': 1},
                           {'sku': 'EXT', 'name': '同一来源', 'quantity': 2}])
        entries = self.session.scalars(select(SettlementEntry).order_by(SettlementEntry.id)).all()
        keys = [e.source_key for e in entries]
        payload = {'entry_ids': [entries[0].id], 'components': [{'product_id': 1, 'multiplier': 2}],
                   'save_mapping': {'effective_from': '2026-01-01', 'source_unit': '包'}}
        preview = await self.request('POST', '/sales/pending-confirmations/preview', json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(self.session.scalars(select(SalesProductMapping)).all(), [])
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        payload['preview_token'] = preview.json()['preview_token']
        applied = await self.request('POST', '/sales/pending-confirmations/retry', json=payload)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertEqual(applied.json()['confirmed_rows'], 1)
        retried = await self.request('POST', '/sales/pending-confirmations/retry', json={'entry_ids': [e.id for e in entries]})
        self.assertEqual((retried.json()['confirmed_rows'], retried.json()['skipped_rows']), (1, 1))
        self.assertEqual([e.source_key for e in entries], keys)
        self.assertEqual(sum(s.quantity for s in self.session.scalars(select(Sale))), 6)
        self.assertEqual(len(self.session.scalars(select(SalesProductMapping)).all()), 1)

    async def test_refunds_and_adjustments_never_deduct_or_restore_inventory(self):
        await self.mapping()
        response = await self.upload([{'sku': 'EXT', 'name': '退款', 'type': '退款', 'quantity': -1, 'net': -20, 'total': -18}])
        self.assertEqual(response.json()['sales_created'], 0)
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)

    async def test_preview_rejects_changed_file_mapping_user_store_tampering_and_expiry(self):
        first = (await self.mapping()).json()['id']
        rows = [{'sku': 'EXT', 'quantity': 1}]
        token = (await self.upload(rows, preview=True)).json()['preview_token']
        for bad in ('invalid', token[:-1] + '!'):
            self.assertEqual((await self.upload(rows, token=bad)).status_code, 400)
        self.assertEqual((await self.upload([{'sku': 'EXT', 'quantity': 2}], token=token)).status_code, 409)
        self.assertEqual((await self.upload(rows, token=token, store=8)).status_code, 409)
        self.actor = SimpleNamespace(id=2, username='operator', role='operator', store_id=7)
        self.assertEqual((await self.upload(rows, token=token)).status_code, 409)
        self.actor = SimpleNamespace(id=1, username='admin', role='admin', store_id=None)
        with patch('services.sales_preview.time.time', return_value=1):
            expired = (await self.upload(rows, preview=True)).json()['preview_token']
        self.assertEqual((await self.upload(rows, token=expired)).status_code, 409)
        await self.mapping(expected=first, parts=[{'product_id': 1, 'multiplier': 3}])
        self.assertEqual((await self.upload(rows, token=token)).status_code, 409)
        for model in (Sale, SaleCostDetail, SettlementEntry, SalesImportBatch):
            self.assertEqual(self.session.scalars(select(model)).all(), [])

    async def test_bundle_shortage_is_atomic_and_changed_pending_preview_cannot_save_mapping(self):
        self.second_product()
        await self.upload([{'sku': 'EXT', 'quantity': 6}])
        entry = self.session.scalar(select(SettlementEntry))
        body = {'entry_ids': [entry.id], 'components': [{'product_id': 1, 'multiplier': 1}, {'product_id': 2, 'multiplier': 2}],
                'save_mapping': {'effective_from': '2026-01-01', 'source_unit': '包'}}
        preview = (await self.request('POST', '/sales/pending-confirmations/preview', json=body)).json()
        self.assertEqual(preview['rows'][0]['status'], 'pending')
        self.assertEqual([c['deduct_quantity'] for c in preview['rows'][0]['components']], [0, 0])
        self.assertEqual(preview['rows'][0]['resolution']['source_unit'], '包')
        self.assertEqual(self.session.scalars(select(SalesProductMapping)).all(), [])
        self.session.get(InventoryBatch, 4).remaining_quantity = 12
        self.session.commit()
        body['preview_token'] = preview['preview_token']
        rejected = await self.request('POST', '/sales/pending-confirmations/retry', json=body)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(self.session.scalars(select(SalesProductMapping)).all(), [])
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        body.pop('preview_token')
        fresh = (await self.request('POST', '/sales/pending-confirmations/preview', json=body)).json()
        body['preview_token'] = fresh['preview_token']
        applied = await self.request('POST', '/sales/pending-confirmations/retry', json=body)
        self.assertEqual(applied.json()['sales_created'], 2)
        repeated = await self.request('POST', '/sales/pending-confirmations/retry', json=body)
        self.assertEqual(repeated.status_code, 409)  # 旧计划已改变；刷新后跳过已处理来源。
        body.pop('preview_token')
        self.assertEqual((await self.request('POST', '/sales/pending-confirmations/retry', json=body)).json()['skipped_rows'], 1)
        self.assertEqual(len(self.session.scalars(select(SalesProductMapping)).all()), 1)
        self.assertEqual(sum(s.quantity for s in self.session.scalars(select(Sale))), 18)

    async def test_name_rename_cannot_deduct_same_order_twice_and_normalized_alias_versions_share_latest(self):
        first = (await self.mapping(kind='product_name', value='Bag', unique=True)).json()['id']
        second = (await self.mapping(kind='product_name', value='ＢＡＧ', unique=True, expected=first)).json()['id']
        listed = (await self.request('GET', '/sales/product-mappings?store_id=7')).json()
        self.assertEqual({m['latest_version_id'] for m in listed}, {second})
        self.assertEqual((await self.upload([{'sku': '', 'name': 'bag', 'order': 'SAME', 'quantity': 1}])).json()['sales_created'], 1)
        await self.mapping(kind='product_name', value='New Bag', unique=True)
        result = await self.upload([{'sku': '', 'name': 'New Bag', 'order': 'SAME', 'quantity': 1}])
        self.assertFalse(result.json()['success'])
        self.assertEqual(len(self.session.scalars(select(Sale)).all()), 1)

    async def test_future_disabled_and_conflicting_codes_never_fall_back_to_name(self):
        self.second_product()
        first = (await self.mapping(start='2026-10-01')).json()['id']
        result = await self.upload([{'sku': 'EXT', 'quantity': 1}])
        self.assertEqual(result.json()['pending_confirmation'], 1)
        await self.mapping(start='2026-01-01', expected=first, active=False)
        result = await self.upload([{'sku': 'EXT', 'order': 'DISABLED', 'quantity': 1}])
        self.assertEqual(result.json()['pending_confirmation'], 1)
        await self.mapping(kind='merchant_sku', value='0001', parts=[{'product_id': 2, 'multiplier': 1}])
        result = await self.upload([{'sku': '', '商家SKU': '0001', 'order': 'MERCHANT', 'quantity': 1}], headers=('商家SKU',))
        self.assertEqual(result.json()['sales_created'], 1)
        result = await self.upload([{'sku': 'EXT', '商家SKU': '0001', 'order': 'CONFLICT', 'quantity': 1,
                                    'ordered_on': '2026-10-02'}], headers=('商家SKU',))
        self.assertEqual(result.json()['pending_confirmation'], 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)

    async def test_audit_mapping_and_pending_deduction_are_atomic_with_correct_store(self):
        await self.upload([{'sku': 'EXT', 'quantity': 1}])
        entry = self.session.scalar(select(SettlementEntry))
        body = {'entry_ids': [entry.id], 'components': [{'product_id': 1, 'multiplier': 2}],
                'save_mapping': {'effective_from': '2026-01-01'}}
        def deny(_a, _b, sql, *_rest):
            if sql.startswith('INSERT INTO business_operation_logs'):
                raise RuntimeError('fixture audit failure')
        with patch.dict(os.environ, {'FIFO_OPERATION_AUDIT_ENABLED': '1'}):
            event.listen(self.engine, 'before_cursor_execute', deny)
            try:
                failed = await self.request('POST', '/sales/pending-confirmations/retry', json=body)
                self.assertEqual(failed.status_code, 503, failed.text)
            finally:
                event.remove(self.engine, 'before_cursor_execute', deny)
            for model in (Sale, SaleCostDetail, SalesProductMapping, SalesProductMappingComponent, BusinessOperationLog):
                self.assertEqual(self.session.scalars(select(model)).all(), [])
            self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
            self.assertEqual(self.session.get(SettlementEntry, entry.id).mapping_status, 'pending_confirmation')
            success = await self.request('POST', '/sales/pending-confirmations/retry', json=body)
            self.assertEqual(success.status_code, 200, success.text)
        logs = self.session.scalars(select(BusinessOperationLog)).all()
        self.assertEqual({r.store_id for r in logs}, {7})
        self.assertEqual(len({r.group_id for r in logs}), 1)
        self.assertIn('sales_product_mapping_components', {r.entity_type for r in logs})

    async def test_mapping_versions_prevent_product_deletion(self):
        from routers.products import router
        self.app.include_router(router)
        self.second_product()
        await self.mapping(parts=[{'product_id': 2, 'multiplier': 1}])
        response = await self.request('DELETE', '/products/2')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'sales_mapping_product_referenced')
        self.assertIsNotNone(self.session.get(Product, 2))
        from routers.finance_masters import router as master_router
        self.app.include_router(master_router)
        await self.mapping(store=8)
        response = await self.request('DELETE', '/finance-masters/stores/8')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'store_has_sales_mappings')

    async def test_empty_pending_filter_preview_and_confirm_are_valid_noops(self):
        plan = (await self.request('POST', '/sales/pending-confirmations/preview', json={'reason': 'stock_shortage'})).json()
        self.assertEqual(plan['rows'], [])
        result = await self.request('POST', '/sales/pending-confirmations/retry',
            json={'reason': 'stock_shortage', 'preview_token': plan['preview_token']})
        self.assertEqual(result.json()['sales_created'], 0)
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])

    async def test_mapping_commit_reads_components_with_current_mysql_locking_read(self):
        from sqlalchemy.dialects import mysql
        from services.sales_mapping import load_mappings
        statements = []
        original = self.db.execute
        async def capture(statement):
            statements.append(str(statement.compile(dialect=mysql.dialect())))
            return await original(statement)
        with patch.object(self.db, 'execute', new=capture):
            await load_mappings(self.db, [7], locked=True)
        self.assertEqual(len(statements), 1)
        self.assertIn('sales_product_mapping_components', statements[0])
        self.assertTrue(statements[0].endswith('FOR UPDATE'))

    async def test_xlsx_text_ids_preserved_numeric_ids_require_source_correction(self):
        await self.mapping(value='001736250585985025154')
        rows = list(csv.reader(io.StringIO(source_file([
            {'sku': '001736250585985025154', 'order': '586306814641538373', 'quantity': 1},
            {'sku': '001736250585985025154', 'order': 'NUMERIC', 'quantity': 1},
            {'sku': '001736250585985025154', 'order': '586306814641538374', 'quantity': 1},
        ]).decode('utf-8-sig'))))
        rows[2][7] = 1736250585985025154
        rows[3][6] = 586306814641538374
        workbook = Workbook();workbook.active.title = '订单详情'
        for row in rows:
            workbook.active.append(row)
        output = io.BytesIO();workbook.save(output)
        result = await self.request('POST', '/sales/import', data={'store_id': '7'},
            files={'file': ('orders.xlsx', output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        self.assertEqual((result.json()['sales_created'], result.json()['pending_confirmation']), (1, 2))
        entries = self.session.scalars(select(SettlementEntry).order_by(SettlementEntry.id)).all()
        self.assertEqual((entries[0].platform_sku_id, entries[0].order_id), ('001736250585985025154', '586306814641538373'))
        for entry in entries[1:]:
            self.assertIn('精度', entry.mapping_error)
            retried = await self.request('POST', '/sales/pending-confirmations/retry', json={
                'entry_ids': [entry.id], 'components': [{'product_id': 1, 'multiplier': 2}]})
            self.assertEqual(retried.json()['pending_rows'], 1)
        self.assertEqual(sum(s.quantity for s in self.session.scalars(select(Sale))), 2)

    async def test_same_file_in_other_store_is_not_silently_treated_as_already_imported(self):
        rows = [{'sku': 'UNKNOWN', 'quantity': 1}]
        self.assertEqual((await self.upload(rows)).json()['pending_confirmation'], 1)
        other = await self.upload(rows, store=8)
        self.assertEqual(other.status_code, 409, other.text)
        self.assertEqual(len(self.session.scalars(select(SettlementEntry)).all()), 1)
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])

    async def test_adding_code_after_name_only_sale_does_not_create_second_deduction(self):
        await self.mapping(kind='product_name', value='商品', unique=True)
        await self.mapping()
        first = await self.upload([{'sku': '', 'name': '商品', 'order': 'SAME', 'quantity': 1}])
        self.assertEqual(first.json()['sales_created'], 1)
        repeat = await self.upload([{'sku': 'EXT', 'name': '商品', 'order': 'SAME', 'quantity': 1}])
        self.assertEqual(repeat.status_code, 400, repeat.text)
        # 未确认的改编码来源可保存，但后续人工确认也不能绕过整单防重。
        pending = await self.upload([{'sku': 'UNKNOWN', 'name': '商品', 'order': 'SAME', 'quantity': 1}])
        self.assertEqual(pending.json()['pending_confirmation'], 1)
        entry = self.session.scalars(select(SettlementEntry).order_by(SettlementEntry.id.desc())).first()
        result = await self.request('POST', '/sales/pending-confirmations/retry', json={
            'entry_ids': [entry.id], 'components': [{'product_id': 1, 'multiplier': 2}]})
        self.assertEqual(result.json()['pending_rows'], 1)
        self.assertEqual(sum(s.quantity for s in self.session.scalars(select(Sale))), 2)

    async def test_platform_and_merchant_identity_switch_requires_reconciliation(self):
        self.second_product()
        await self.mapping(kind='merchant_sku', value='0001', parts=[{'product_id': 1, 'multiplier': 1}, {'product_id': 2, 'multiplier': 1}])
        await self.mapping(parts=[{'product_id': 1, 'multiplier': 1}, {'product_id': 2, 'multiplier': 1}])
        first = await self.upload([{'sku': '', '商家SKU': '0001', 'order': 'SAME', 'quantity': 1}], headers=('商家SKU',))
        self.assertEqual(first.json()['sales_created'], 2)
        changed = await self.upload([{'sku': 'EXT', '商家SKU': '0001', 'order': 'SAME', 'quantity': 1}], headers=('商家SKU',))
        self.assertEqual(changed.status_code, 400, changed.text)
        self.assertEqual(len(self.session.scalars(select(Sale)).all()), 2)
