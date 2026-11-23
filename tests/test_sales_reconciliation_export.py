"""真实导入/导出路由 + 隔离 SQLite，核对原始行、组件关系和只读边界。"""
import io
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import load_workbook
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql

from models import InventoryBatch, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation
from tests import test_sales_name_import as names
from tests import test_sales_import_store as fixtures


class ReconciliationExportTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ImportStoreTests.setUp
    tearDown = fixtures.ImportStoreTests.tearDown
    request = fixtures.ImportStoreTests.request
    upload = names.NameImportTests.upload
    add_bundle_products = names.NameImportTests.add_bundle_products

    async def import_bundle(self):
        self.add_bundle_products()
        response = await self.upload([
            {'order': '00042', 'sku': '00123456789012345678', 'name': '吸盘逗猫棒（黑色）*2+吸盘逗猫棒（白色）', 'quantity': 2},
            {'order': 'PENDING', 'name': '未建档商品', 'quantity': 1, 'net': 50, 'total': 40},
            {'order': '00042', 'sku': '00123456789012345678', 'type': '退款', 'name': '吸盘逗猫棒（黑色）', 'quantity': -1, 'net': -20, 'total': -18},
        ])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()['settlement_rows'], response.json()['sales_created'], response.json()['pending_confirmation']), (3, 2, 1))
        return response.json()['import_batch_id']

    async def workbook(self, url='/sales/export', **params):
        response = await self.request('GET', url, params=params)
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        return load_workbook(io.BytesIO(response.content), data_only=True)

    def records(self, book, name='原始结算对账'):
        rows = list(book[name].values)
        return [dict(zip(rows[0], row)) for row in rows[1:-1]]

    def snapshot(self):
        return {model.__tablename__: list(self.session.execute(select(*model.__table__.columns).order_by(model.id)))
                for model in (InventoryBatch, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation)}

    def add_shared_source(self, original, *, store_id=7, same_batch=False):
        batch_id = original.import_batch_id
        if not same_batch:
            batch = SalesImportBatch(store_id=store_id, user_id=1, file_name='other-source.csv', file_type='order_detail')
            self.session.add(batch)
            self.session.flush()
            batch_id = batch.id
        entry = SettlementEntry(source_key='shared-export-source', source_row_number=3, import_batch_id=batch_id,
            source_file='other-source.csv', source_sheet='OTHER-SHEET', order_id='OTHER-SOURCE-ORDER',
            platform_sku_id='OTHER-SOURCE-SKU', product_name='Otro título', store_id=store_id,
            sale_id=original.sale_id, quantity=1, net_product_sales=10, settlement_total=9,
            currency='USD', exchange_rate_to_cny=2, exchange_rate_date=original.exchange_rate_date,
            exchange_rate_source='OTHER-FX', order_created_at=original.order_created_at,
            settlement_date=original.settlement_date,
            raw_data={'产品名': '另一来源名称', '订单ID/调整单ID': 'OTHER-DISPLAY-ORDER'})
        self.session.add(entry)
        self.session.commit()
        return entry

    def allocate_source(self, entry, sale_id):
        sale = self.session.get(Sale, sale_id)
        entry.sale_id = None
        self.session.add(SettlementEntryAllocation(settlement_entry_id=entry.id, sale_id=sale_id,
            product_id=sale.product_id, component_name='商品', quantity=sale.quantity))
        self.session.commit()

    async def test_batch_amounts_and_metadata_only_use_selected_sources_for_both_link_types(self):
        await self.upload([{'name': '商品', 'quantity': 1}])
        original = self.session.scalar(select(SettlementEntry))
        other = self.add_shared_source(original)
        for link_type in ('direct', 'allocation'):
            with self.subTest(link_type=link_type):
                if link_type == 'allocation':
                    self.allocate_source(other, original.sale_id)
                before = self.snapshot()
                for entry, net, total, revenue, fee, currency, rate, order_no in (
                    (original, 200, 180, 200, 20, 'CNY', 1, 'ORDER-1'),
                    (other, 10, 9, 20, 2, 'USD', 2, 'OTHER-DISPLAY-ORDER'),
                ):
                    book = await self.workbook(f'/sales/imports/{entry.import_batch_id}/export')
                    detail, = self.records(book, '销售拆分明细')
                    self.assertEqual(detail['来源净商品销售额（原币，整行勿合计）'], net)
                    self.assertEqual(detail['分摊净商品销售额（原币）'], net)
                    self.assertEqual(detail['分摊结算总金额（原币）'], total)
                    self.assertEqual((detail['店铺收入（元）'], detail['其他费用（元）']), (revenue, fee))
                    self.assertEqual((detail['原币'], detail['汇率'], detail['订单号']), (currency, rate, order_no))
                    self.assertEqual(detail['关联编号'], f'E{entry.id}')
                    self.assertEqual(detail['来源 SKU ID'], entry.platform_sku_id)
                    self.assertEqual(detail['来源产品名'], entry.raw_data['产品名'])
                    self.assertEqual(book['订单汇总']['B2'].value, order_no)
                self.assertEqual(self.snapshot(), before)

    async def test_other_store_sources_cannot_change_normal_or_batch_export(self):
        imported = (await self.upload([{'name': '商品', 'quantity': 1}])).json()
        original = self.session.scalar(select(SettlementEntry))
        other = self.add_shared_source(original, store_id=8)
        self.actor = SimpleNamespace(id=2, username='viewer', role='viewer', store_id=7)
        for link_type in ('direct', 'allocation'):
            with self.subTest(link_type=link_type):
                if link_type == 'allocation':
                    self.allocate_source(other, original.sale_id)
                before = self.snapshot()
                for url in ('/sales/export', f"/sales/imports/{imported['import_batch_id']}/export"):
                    book = await self.workbook(url)
                    detail, = self.records(book, '销售拆分明细')
                    self.assertEqual(detail['分摊净商品销售额（原币）'], 200)
                    self.assertEqual(detail['分摊结算总金额（原币）'], 180)
                    self.assertEqual(detail['店铺收入（元）'], 200)
                    self.assertEqual(detail['关联编号'], f'E{original.id}')
                    exported_text = '\n'.join(str(value) for sheet in book for row in sheet.values for value in row)
                    for hidden in ('other-source.csv', 'OTHER-SHEET', 'OTHER-SOURCE-ORDER', 'OTHER-DISPLAY-ORDER',
                                   'OTHER-SOURCE-SKU', 'Otro título', '另一来源名称', 'OTHER-FX'):
                        self.assertNotIn(hidden, exported_text)
                self.assertEqual(self.snapshot(), before)

    async def test_empty_authorized_source_set_does_not_restore_unscoped_context(self):
        await self.upload([{'name': '商品', 'quantity': 1}])
        entry = self.session.scalar(select(SettlementEntry))
        entry.store_id = 8
        entry.platform_sku_id = 'HIDDEN-SOURCE-SKU'
        entry.product_name = 'Hidden product'
        entry.raw_data = {'产品名': 'Hidden expression', '订单ID/调整单ID': 'HIDDEN-ORDER'}
        entry.net_product_sales, entry.settlement_total = 999, 888
        self.session.commit()
        self.actor = SimpleNamespace(id=2, username='viewer', role='viewer', store_id=7)
        before = self.snapshot()
        book = await self.workbook()
        source, = self.records(book)
        detail, = self.records(book, '销售拆分明细')
        self.assertEqual(source['处理状态'], '无原始结算记录')
        self.assertEqual(detail['店铺收入（元）'], 200)  # 仅保留本店已保存的销售金额。
        for field in ('来源 SKU ID', '来源产品名', '分摊净商品销售额（原币）', '分摊结算总金额（原币）'):
            self.assertIsNone(detail[field])
        self.assertEqual(detail['订单号'], self.session.get(Sale, entry.sale_id).order_no)
        self.assertEqual(self.snapshot(), before)

    async def test_shared_sale_quantities_are_unknown_per_source_and_not_double_totalled(self):
        imported = (await self.upload([{'name': '商品', 'quantity': 1}])).json()
        original = self.session.scalar(select(SettlementEntry))
        other = self.add_shared_source(original, same_batch=True)
        other.currency, other.exchange_rate_to_cny = 'CNY', 1
        self.session.commit()
        before = self.snapshot()
        for url in ('/sales/export', f"/sales/imports/{imported['import_batch_id']}/export"):
            book = await self.workbook(url)
            originals, details = self.records(book), self.records(book, '销售拆分明细')
            self.assertEqual(len(originals), 2)
            self.assertEqual(len(details), 1)
            self.assertEqual(details[0]['实际出库数量'], 1)
            self.assertEqual(details[0]['分摊净商品销售额（原币）'], 210)
            self.assertEqual(details[0]['关联编号'].splitlines(), [row['关联编号'] for row in originals])
            totals = dict(zip([cell.value for cell in book.active[1]], list(book.active.values)[-1]))
            for row in [*originals, totals]:
                for field in ('出库明细数', '实际出库件数', '销售成本（元）', '毛利（元）'):
                    self.assertIsNone(row[field], (field, row))
            for row in originals:
                self.assertIn('多个来源行', row['待核对原因'])
                self.assertIn('数量', row['待核对原因'])
                self.assertEqual(row['销售明细编号'], details[0]['销售明细编号'])
        self.assertEqual(self.snapshot(), before)

    async def test_detail_source_name_preserves_original_fallback_and_explicit_blanks(self):
        imported = (await self.upload([{'name': '商品', 'quantity': 1}])).json()
        entry = self.session.scalar(select(SettlementEntry))
        entry.product_name = 'Juguete para Perro'
        for raw, expected in (({'商品名称': 'Juguete para Perro'}, 'Juguete para Perro'),
                              ({'产品名': '来源表达式'}, '来源表达式'), ({'产品名': ''}, None), ({'产品名': None}, None)):
            with self.subTest(raw=raw):
                entry.raw_data = raw
                self.session.commit()
                listed, = (await self.request('GET', '/sales')).json()
                self.assertEqual(listed['display_name'], raw.get('产品名') or '商品')
                before = self.snapshot()
                for url in ('/sales/export', f"/sales/imports/{imported['import_batch_id']}/export"):
                    book = await self.workbook(url)
                    source, = self.records(book)
                    detail, = self.records(book, '销售拆分明细')
                    self.assertEqual(source['产品名'], expected)
                    self.assertEqual(detail['来源产品名'], expected)
                    self.assertEqual(detail['本地商品名称'], '商品')
                self.assertEqual(self.snapshot(), before)

    async def test_multiple_source_names_follow_reference_order_without_local_fallback(self):
        await self.upload([{'name': '商品', 'quantity': 1}])
        original = self.session.scalar(select(SettlementEntry))
        original.raw_data = {'商品名称': 'Original title'}
        original.product_name = 'Original title'
        other = self.add_shared_source(original, same_batch=True)
        other.currency, other.exchange_rate_to_cny = 'CNY', 1
        self.session.commit()
        book = await self.workbook()
        originals, details = self.records(book), self.records(book, '销售拆分明细')
        self.assertEqual(details[0]['来源产品名'].splitlines(), [row['产品名'] for row in originals])
        self.assertEqual(details[0]['来源 SKU ID'].splitlines(), [row['SKU ID'] for row in originals])

    async def test_original_rows_include_pending_and_preserve_links_without_writes(self):
        batch_id = await self.import_bundle()
        before = self.snapshot()
        statements = []
        def capture(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement.strip().split()[0].upper())
        event.listen(self.engine, 'before_cursor_execute', capture)
        book = await self.workbook(f'/sales/imports/{batch_id}/export')
        event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertTrue(set(statements) <= {'SELECT'}, statements)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(book.sheetnames[:2], ['原始结算对账', '销售拆分明细'])
        self.assertEqual(book.active.title, '原始结算对账')
        originals, details = self.records(book), self.records(book, '销售拆分明细')
        self.assertEqual(len(originals), 3)
        self.assertEqual([row['原文件行号'] for row in originals], [2, 3, 4])
        self.assertEqual([row['结算总金额（原币）'] for row in originals], [180, 40, -18])
        self.assertEqual(len(details), 3)
        bundle = originals[0]
        self.assertEqual((bundle['来源数量'], bundle['实际出库件数'], bundle['出库明细数']), (2, 6, 2))
        self.assertEqual(bundle['销售成本（元）'], 140)
        self.assertEqual(bundle['毛利（元）'], 40)
        children = [row for row in details if row['关联编号'] == bundle['关联编号']]
        self.assertEqual({row['本地商品 SKU'] for row in children}, {'BLACK', 'WHITE'})
        self.assertEqual({row['本地商品名称'] for row in children}, {'吸盘逗猫棒（黑色）', '吸盘逗猫棒（白色）'})
        self.assertEqual({row['来源 SKU ID'] for row in children}, {'00123456789012345678'})
        self.assertEqual(set(bundle['销售明细编号'].splitlines()), {row['销售明细编号'] for row in children})
        self.assertEqual(sum(Decimal(str(row['分摊结算总金额（原币）'])) for row in children).quantize(Decimal('.0001')), 180)
        self.assertIsNone(originals[1]['销售成本（元）'])
        self.assertIsNone(originals[1]['毛利（元）'])
        self.assertEqual(originals[1]['处理状态'], '待确认')
        self.assertEqual(originals[2]['处理状态'], '仅结算')
        self.assertEqual(originals[2]['销售成本（元）'], 0)
        total = dict(zip([cell.value for cell in book.active[1]], list(book.active.values)[-1]))
        self.assertEqual(total['结算总金额（原币）'], 202)
        self.assertIsNone(total['销售成本（元）'])
        self.assertIsNotNone(book.active['A2'].hyperlink)
        self.assertEqual(book['销售拆分明细']['AI2'].hyperlink.target, "#'原始结算对账'!A2")

    async def test_product_filter_expands_whole_source_and_batch_ignores_month_filters(self):
        batch_id = await self.import_bundle()
        book = await self.workbook(product_id=3, month='2026-09')
        self.assertEqual(len(self.records(book)), 1)
        details = self.records(book, '销售拆分明细')
        self.assertEqual({row['本地商品 SKU'] for row in details}, {'BLACK', 'WHITE'})
        self.assertEqual(self.records(book)[0]['销售成本（元）'], 140)
        book = await self.workbook(f'/sales/imports/{batch_id}/export', month='1900-01', keyword='not-found', product_id=999)
        self.assertEqual(len(self.records(book)), 3)
        self.assertEqual(len(self.records(await self.workbook(month='2026-09'))), 3)

    async def test_pending_only_export_and_limits_cover_sources_and_expanded_components(self):
        response = await self.upload([{'name': '未建档商品', 'quantity': 1}])
        batch_id = response.json()['import_batch_id']
        book = await self.workbook(month='2026-09')
        self.assertEqual(len(self.records(book)), 1)
        self.assertEqual(self.records(book, '销售拆分明细'), [])
        self.assertEqual(self.records(await self.workbook(f'/sales/imports/{batch_id}/export'))[0]['处理状态'], '待确认')
        await self.import_bundle()
        with patch('routers.sales.MAX_EXPORT_ROWS', 1):
            response = await self.request('GET', '/sales/export', params={'product_id': 3})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()['detail']['code'], 'sales_export_too_large')
            response = await self.request('GET', '/sales/export', params={'keyword': '未建档商品'})
            self.assertEqual(response.status_code, 422)

    async def test_batch_permissions_and_rolled_back_are_enforced(self):
        batch_id = await self.import_bundle()
        for role in ('operator', 'viewer'):
            self.actor = SimpleNamespace(id=2, username='user', role=role, store_id=7)
            self.assertEqual(len(self.records(await self.workbook(f'/sales/imports/{batch_id}/export'))), 3)
            self.actor.store_id = 8
            self.assertEqual((await self.request('GET', f'/sales/imports/{batch_id}/export')).status_code, 404)
            self.actor.store_id = None
            self.assertEqual((await self.request('GET', f'/sales/imports/{batch_id}/export')).status_code, 403)
        self.actor = SimpleNamespace(id=1, username='admin', role='admin', store_id=None)
        self.session.get(SalesImportBatch, batch_id).status = 'rolled_back'
        self.session.commit()
        response = await self.request('GET', f'/sales/imports/{batch_id}/export')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'sales_export_batch_rolled_back')
        self.assertEqual((await self.request('GET', '/sales/imports/99999/export')).status_code, 404)

    async def test_unknown_cost_and_rate_stay_blank_in_originals(self):
        batch_id = await self.import_bundle()
        self.session.get(InventoryBatch, 4).voided_at = datetime(2026, 10, 1)
        entry = self.session.scalar(select(SettlementEntry).order_by(SettlementEntry.id))
        entry.currency, entry.exchange_rate_to_cny = 'USD', None
        self.session.commit()
        book = await self.workbook(f'/sales/imports/{batch_id}/export')
        row = self.records(book)[0]
        self.assertEqual(row['结算总金额（原币）'], 180)
        for key in ('来源收入（折人民币）', '销售成本（元）', '毛利（元）', '汇率'):
            self.assertIsNone(row[key])
        self.assertIn('汇率缺失', row['待核对原因'])
        totals = dict(zip([cell.value for cell in book.active[1]], list(book.active.values)[-1]))
        self.assertIsNone(totals['结算总金额（原币）'])

    async def test_source_ids_and_formula_text_preserve_separate_events(self):
        batch_id = await self.import_bundle()
        entries = self.session.scalars(select(SettlementEntry).order_by(SettlementEntry.id)).all()
        entries[0].source_file = '=HYPERLINK("https://unit.invalid","x")'
        entries[0].raw_data = {**entries[0].raw_data, '订单ID/调整单ID': '=1+2'}
        self.session.commit()
        book = await self.workbook(f'/sales/imports/{batch_id}/export')
        self.assertEqual(book.active['C2'].data_type, 's')
        self.assertEqual(book.active['F2'].data_type, 's')
        self.assertEqual(book.active['G2'].value, '00123456789012345678')
        self.assertEqual(len({row['关联编号'] for row in self.records(book)}), 3)

    async def test_legacy_direct_link_and_missing_source_remain_read_only(self):
        response = await self.upload([{'name': '商品', 'quantity': 1}])
        entry = self.session.scalar(select(SettlementEntry))
        allocations = self.session.scalars(select(SettlementEntryAllocation)).all()
        for allocation in allocations:
            self.session.delete(allocation)
        self.session.commit()
        before = self.snapshot()
        book = await self.workbook()
        self.assertEqual(self.records(book)[0]['出库明细数'], 1)
        self.assertEqual(self.snapshot(), before)
        self.session.delete(entry)
        self.session.commit()
        book = await self.workbook()
        row = self.records(book)[0]
        self.assertEqual(row['处理状态'], '无原始结算记录')
        self.assertIsNone(row['来源数量'])
        self.assertIsNone(row['结算总金额（原币）'])
        self.assertEqual(row['销售明细编号'], self.records(book, '销售拆分明细')[0]['销售明细编号'])

    async def test_all_duplicate_batch_exports_empty_scope_with_skip_count(self):
        rows = [{'name': '商品', 'quantity': 1}]
        await self.upload(rows)
        repeated = (await self.upload(rows)).json()
        self.assertEqual(repeated['skipped_duplicates'], 1)
        book = await self.workbook(f"/sales/imports/{repeated['import_batch_id']}/export")
        self.assertEqual(self.records(book), [])
        self.assertEqual(self.records(book, '销售拆分明细'), [])
        notes = dict(book['说明'].values)
        self.assertEqual(notes['本次重复跳过行数'], 1)
        self.assertEqual(notes['文件有效数据行数'], 1)

    async def test_shared_sale_in_another_batch_does_not_duplicate_primary_cost(self):
        imported = (await self.upload([{'name': '商品', 'quantity': 1}])).json()
        original = self.session.scalar(select(SettlementEntry))
        batch = SalesImportBatch(store_id=7, user_id=1, file_name='second.csv', file_type='order_detail')
        self.session.add(batch)
        self.session.flush()
        self.session.add(SettlementEntry(source_key='another-event', source_row_number=2, import_batch_id=batch.id,
            source_file='second.csv', order_id='another', platform_sku_id='SOURCE', store_id=7, sale_id=original.sale_id,
            quantity=1, net_product_sales=10, settlement_total=9, currency='CNY', raw_data={}))
        self.session.commit()
        book = await self.workbook(f"/sales/imports/{imported['import_batch_id']}/export")
        row = self.records(book)[0]
        self.assertEqual(len(self.records(book)), 1)
        self.assertIsNone(row['销售成本（元）'])
        self.assertIsNone(row['毛利（元）'])
        self.assertIn('多个来源行', row['待核对原因'])
        self.assertIsNone(row['出库明细数'])
        self.assertIsNone(row['实际出库件数'])

    async def test_component_expansion_cannot_cross_store_scope(self):
        batch_id = await self.import_bundle()
        sale = self.session.scalar(select(Sale).where(Sale.product_id == 3))
        sale.store_id = 8
        self.session.commit()
        self.actor = SimpleNamespace(id=2, username='viewer', role='viewer', store_id=7)
        book = await self.workbook(f'/sales/imports/{batch_id}/export')
        self.assertNotIn('WHITE', {row['本地商品 SKU'] for row in self.records(book, '销售拆分明细')})
        original = self.records(book)[0]
        self.assertIsNone(original['销售成本（元）'])
        self.assertIsNone(original['出库明细数'])
        self.assertIsNone(original['实际出库件数'])
        self.assertIn('不完整', original['待核对原因'])
        self.assertNotIn(f'S{sale.id}', original['销售明细编号'].splitlines())

    async def test_manual_settlement_and_legacy_csv_do_not_invent_original_amounts(self):
        response = await self.request('POST', '/sales/manual-settlements', json={
            'store_id': 7, 'request_key': 'reconciliation-manual-1', 'occurred_on': '2026-09-02',
            'order_no': 'MANUAL', 'sku_id': '00017', 'product_name': '人工补录', 'quantity': 2,
            'net_product_sales': '100', 'settlement_total': '90'})
        self.assertEqual(response.status_code, 200, response.text)
        book = await self.workbook()
        row = self.records(book)[0]
        self.assertEqual((row['来源数量'], row['实际出库件数'], row['结算总金额（原币）']), (2, 0, 90))
        self.assertEqual(row['处理状态'], '仅结算')
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        response = await self.request('POST', '/sales/import', data={'store_id': '7'},
            files={'file': ('legacy.csv', b'SKU,2026-09-01,1\n', 'text/csv')})
        book = await self.workbook(f"/sales/imports/{response.json()['import_batch_id']}/export")
        row = self.records(book)[0]
        self.assertEqual(row['来源数量'], 1)
        self.assertIsNone(row['净商品销售额（原币）'])
        self.assertIsNone(row['结算总金额（原币）'])

    async def test_source_queries_compile_for_mysql_and_release_read_transaction(self):
        batch_id = await self.import_bundle()
        execute = self.db.execute
        compiled = []
        async def checked(statement):
            compiled.append(str(statement.compile(dialect=mysql.dialect())))
            return await execute(statement)
        from services.sales_export import build_sales_workbook
        def build(*args, **kwargs):
            self.assertFalse(self.session.in_transaction())
            return build_sales_workbook(*args, **kwargs)
        with patch.object(self.db, 'execute', checked), patch('routers.sales.build_sales_workbook', side_effect=build):
            await self.workbook(month='2026-09', keyword='吸盘', product_id=3)
            await self.workbook(f'/sales/imports/{batch_id}/export')
        self.assertTrue(any('UNION' in sql for sql in compiled))
        self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql in compiled))


if __name__ == '__main__':
    unittest.main()
