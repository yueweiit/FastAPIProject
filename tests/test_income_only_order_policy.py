"""赠品、代发与创建日归月：隔离 SQLite/ASGI，不连接业务库或启动应用。"""
import io
import os
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openpyxl import load_workbook
from sqlalchemy import select

from models import (BatchImpairmentStore, InventoryBatch, Product, Sale, SaleCostDetail, SalesImportBatch,
                    SettlementEntry, SettlementEntryAllocation, Store, User)
from routers.reports import (_product_line_profit_data, _store_profit_loss_auto_values,
                             _sale_provision_adjustments)
from services.financial_facts import SETTLEMENT_ONLY_KEY, unlinked_settlement_events
from services.report_exchange_rates import load_fulfillment_orders
from services.sales_classification import INCOME_ONLY_GIFT, FINANCIAL_CLASSIFICATION_KEY, is_income_only_order
from tests import test_sales_import_store as fixtures, test_sales_name_import as names


GIFTS = (
    ('586324448319472895', '1736358625241302146', '2026-09-29', '181', '132.68'),
    ('586256419587458783', '1736250585985025154', '2026-09-25', '36.67', '22.22'),
    ('586224293927421563', '1736250585985025154', '2026-09-23', '36.67', '22.22'),
    ('586140185307415783', '1736358625241236610', '2026-09-18', '96.02', '67.89'),
    ('586107114055763249', '1736250585985090690', '2026-09-16', '162', '101.90'),
    ('586079637817296388', '1735577326775534722', '2026-09-14', '120.28', '86.55'),
    ('586072973964903905', '1736358625241236610', '2026-09-14', '93.99', '61.46'),
)


class IncomeOnlyOrderTests(unittest.IsolatedAsyncioTestCase):
    request = fixtures.ImportStoreTests.request
    tearDown = fixtures.ImportStoreTests.tearDown

    def setUp(self):
        flags = patch.dict(os.environ, {'FIFO_OPERATION_AUDIT_ENABLED': '0', 'FIFO_FINANCIAL_CONTROLS_ENABLED': '0'})
        flags.start(); self.addCleanup(flags.stop)
        fixtures.ImportStoreTests.setUp(self)
        store = self.session.get(Store, 7)
        store.platform, store.platform_store_id, store.name = 'tiktok_shop', 'manual-1789368327798', 'Citrijoy Mascotas'
        self.session.commit()

    def source_rows(self, *, mapped=True):
        return [dict(order=order, sku=sku, ordered_on=day, settled_on='2026-10-10', quantity=1,
                     name='赠品原始名称', net=net, total=total, **({'本地SKU': 'SKU'} if mapped else {}))
                for order, sku, day, net, total in GIFTS]

    async def upload(self, rows, *, store=7, preview=False, token=None, headers=()):
        data = {'store_id': str(store)}
        if token:
            data['preview_token'] = token
        headers = tuple(dict.fromkeys(('本地SKU', *headers)))
        return await self.request('POST', '/sales/import-preview' if preview else '/sales/import', data=data,
            files={'file': ('gift.csv', names.source_file(rows, headers), 'text/csv')})

    def stock(self):
        return [(b.id, b.quantity, b.remaining_quantity, b.unit_cost) for b in self.session.scalars(select(InventoryBatch))]

    async def orders(self, store=7, start=date(2026, 9, 1), end=date(2026, 10, 1)):
        return await load_fulfillment_orders(self.db, {store}, start, end)

    async def assert_gift_ledger(self, fulfilled=7):
        response = await self.request('GET', '/sales?month=2026-09')
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual(len(rows), 7)
        self.assertEqual(sum(D(row['sales_revenue_cny']) for row in rows), D('726.63'))
        self.assertEqual(sum(D(row['platform_fee_cny']) for row in rows), D('231.71'))
        for row in rows:
            self.assertTrue(row['income_only'])
            self.assertEqual((row['quantity'], row['source_quantity'], row['record_type']),
                             (1, 1, 'sale') if row['id'] > 0 else (0, 1, 'settlement_adjustment'))
            for key in ('total_cost', 'order_total_cost_cny', 'purchase_cost_cny', 'head_shipping_cost_cny', 'impairment_cny'):
                self.assertEqual(D(row[key]), 0, key)
            for key in ('unit_purchase_cost_cny', 'unit_total_cost_cny', 'selling_price_cny'):
                self.assertIsNone(row[key], key)
            self.assertEqual(sum(item['quantity'] for item in row['cost_details']), row['quantity'])
            self.assertTrue(all(D(item['unit_cost']) == 0 for item in row['cost_details']))
        self.assertEqual((await self.request('GET', '/sales?month=2026-10')).json(), [])
        summary = (await self.request('GET', '/sales/monthly-summary?month=2026-09')).json()[0]
        self.assertEqual((summary['sales_count'], summary['sold_quantity'], summary['settlement_event_count']),
                         (fulfilled, fulfilled, 7 - fulfilled))
        self.assertEqual(D(summary['sales_cost_cny']), 0)
        self.assertEqual(D(summary['sales_revenue_cny']), D('726.63'))
        self.assertEqual(len((await self.orders())[date(2026, 9, 1)]), 7)

    async def assert_profit_reports(self, fulfilled=7):
        zero = {key: {period: D(0) for period in ('current', 'previous', 'ytd')}
                for key in ('inventory_impairment', 'inventory_writeoff')}
        writeoffs = {key: (D(0), '') for key in self.session.scalars(select(SaleCostDetail.id))}
        # 本用例仅核对商品成本/销售跌价；存货准备的持有期间回放沿用原规则。
        with patch('routers.reports._store_provision_financial_values', new=AsyncMock(return_value=zero)), \
             patch('routers.reports._sale_provision_adjustments', new=AsyncMock(return_value=writeoffs)), \
             patch('routers.reports._load_inventory_provision_data', new=AsyncMock(return_value=([], [], None))):
            auto = await _store_profit_loss_auto_values(self.db, 7, date(2026, 9, 1),
                oa_values={'office': {}, 'shared_admin_by_store': {}, 'shared_admin_unresolved': {}}, active_store_count=1)
            self.assertEqual(auto['_fulfillment_counts']['current'], 7)
            self.assertEqual(auto['fulfillment_cost']['current'], D(28))
            self.assertEqual(auto['product_sales_revenue']['current'], D('726.63'))
            self.assertEqual(auto['product_purchase_cost']['current'], 0)
            self.assertEqual(auto['head_logistics_cost']['current'], 0)
            rows = await _product_line_profit_data(self.db, {7}, date(2026, 9, 1), date(2026, 9, 30))
            self.assertEqual(sum(row['current']['revenue'] for row in rows), D('726.63'))
            self.assertEqual(sum(row['current']['cost'] for row in rows), D('231.71') + D(28))
            self.assertEqual(sum(row['current_quantity'] for row in rows), fulfilled)

    async def profit_reports(self):
        auto = await _store_profit_loss_auto_values(self.db, 7, date(2026, 9, 1),
            oa_values={'office': {}}, active_store_count=1)
        products = await _product_line_profit_data(self.db, {7}, date(2026, 9, 1), date(2026, 9, 30))
        return auto, products

    async def test_gift_keeps_native_inventory_writeoff_for_legacy_and_explicit_ownership(self):
        response = await self.upload(self.source_rows()[:1])
        self.assertEqual(response.status_code, 200, response.text)
        before = self.stock()
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                if explicit:
                    self.session.add(BatchImpairmentStore(batch_id=1, store_id=7))
                    self.session.commit()
                    self.db.info.clear()
                auto, products = await self.profit_reports()
                self.assertEqual(auto['inventory_writeoff']['current'], D('2.8'))
                expected = sum(auto[key]['current'] for key in ('product_purchase_cost', 'head_logistics_cost',
                    'inventory_other_cost', 'fulfillment_cost', 'other_direct_cost')) - auto['inventory_writeoff']['current']
                self.assertEqual(expected, D('49.52'))
                self.assertEqual(sum(row['current']['cost'] for row in products), expected)
                self.assertEqual(sum(row['ytd']['cost'] for row in products), expected)
                self.assertEqual(sum(row['current_quantity'] for row in products), 1)
                self.assertEqual(self.stock(), before)

    async def test_gift_does_not_hide_unknown_inventory_writeoff(self):
        await self.upload(self.source_rows()[:1])
        self.session.get(User, 2).store_id = 8  # 历史批次归属与销售店铺冲突。
        self.session.commit()
        auto, products = await self.profit_reports()
        adjustments = await _sale_provision_adjustments(self.db, date(2026, 9, 30))
        self.assertIsNone(next(iter(adjustments.values()))[0])
        self.assertIsNone(auto['inventory_writeoff']['current'])
        self.assertIsNone(products[0]['current']['cost'])
        self.assertIsNone(products[0]['ytd']['cost'])
        self.assertIn('待核对', products[0]['note'])

    def add_gift_reference(self, sale, *, store_id, allocated):
        header = SalesImportBatch(store_id=store_id, user_id=1, file_name='gift-reference.csv', file_type='order_detail')
        self.session.add(header)
        self.session.flush()
        order, sku, day, net, total = GIFTS[0]
        entry = SettlementEntry(source_key=f'gift-reference-{allocated}', source_row_number=1,
            import_batch_id=header.id, store_id=store_id, product_id=sale.product_id,
            sale_id=None if allocated else sale.id, order_id=order, platform_sku_id=sku,
            transaction_type='订单', quantity=1, net_product_sales=D(net), settlement_total=D(total),
            currency='CNY', exchange_rate_to_cny=D(1), order_created_at=datetime.fromisoformat(day), raw_data={})
        self.session.add(entry)
        self.session.flush()
        if allocated:
            self.session.add(SettlementEntryAllocation(settlement_entry_id=entry.id, sale_id=sale.id,
                product_id=sale.product_id, quantity=1, component_name='商品'))
        self.session.commit()
        return entry

    async def test_foreign_gift_reference_cannot_exempt_authorized_export_or_order_summary(self):
        response = await self.upload([{'order': 'NORMAL', 'name': '商品', 'quantity': 1, '本地SKU': 'SKU'}], store=8)
        self.assertEqual(response.status_code, 200, response.text)
        original = self.session.scalar(select(SettlementEntry))
        sale = self.session.get(Sale, original.sale_id)
        self.actor = SimpleNamespace(id=1, username='viewer', role='viewer', store_id=8)
        before = self.stock()
        for allocated in (False, True):
            with self.subTest(allocated=allocated):
                if not allocated:
                    gift = self.add_gift_reference(sale, store_id=7, allocated=False)
                else:
                    gift.sale_id = None
                    self.session.add(SettlementEntryAllocation(settlement_entry_id=gift.id, sale_id=sale.id,
                        product_id=sale.product_id, quantity=1, component_name='商品'))
                    self.session.commit()
                for url in ('/sales/export', f'/sales/imports/{original.import_batch_id}/export'):
                    result = await self.request('GET', url)
                    self.assertEqual(result.status_code, 200)
                    book = load_workbook(io.BytesIO(result.content), data_only=True)
                    values = list(book['销售拆分明细'].values)
                    row = dict(zip(values[0], values[1]))
                    self.assertEqual(row['关联编号'], f'E{original.id}')
                    self.assertEqual(row['订单总成本（元，本行）'], 10)
                    self.assertNotEqual(row['成本状态'], '赠品免成本')
                    self.assertEqual(book['订单汇总']['D2'].value, 10)
                detail = (await self.request('GET', f'/sales/{sale.id}')).json()
                self.assertFalse(detail['income_only'])
                self.assertEqual(detail['cost_status'], 'needs_review')
                self.assertEqual(D(detail['original_total_cost']), 10)
                self.assertIsNone(detail['profit_cny'])
                self.assertEqual(self.stock(), before)

    async def test_single_gift_source_requires_resolved_matching_sale_owner(self):
        await self.upload(self.source_rows()[:1])
        sale = self.session.scalar(select(Sale))
        entry = self.session.scalar(select(SettlementEntry))
        # 历史空直接归属沿用创建人绑定；同一来源的直接/分摊双关联只算一次。
        sale.store_id, sale.user_id = None, 2
        self.session.commit()
        self.assertTrue((await self.request('GET', f'/sales/{sale.id}')).json()['income_only'])
        for store_id, user_id in ((None, 1), (8, 2)):
            with self.subTest(store_id=store_id, user_id=user_id):
                sale.store_id, sale.user_id = store_id, user_id
                self.session.commit()
                row = (await self.request('GET', f'/sales/{sale.id}')).json()
                self.assertFalse(row['income_only'])
                self.assertEqual(row['cost_status'], 'needs_review')
                self.assertIsNone(row['profit_cny'])
        # 仅本店销售可见、来源全部不可见时，不能恢复全局赠品来源豁免。
        self.actor = SimpleNamespace(id=1, username='viewer', role='viewer', store_id=8)
        result = await self.request('GET', '/sales/export')
        self.assertEqual(result.status_code, 200)
        book = load_workbook(io.BytesIO(result.content), data_only=True)
        values = list(book['销售拆分明细'].values)
        detail = dict(zip(values[0], values[1]))
        self.assertEqual(detail['订单总成本（元，本行）'], 10)
        self.assertNotEqual(detail['关联编号'], f'E{entry.id}')
        self.assertEqual(book['订单汇总']['D2'].value, 10)

    async def test_mixed_gift_sources_are_reviewed_in_ledger_and_both_profit_reports(self):
        await self.upload([{'order': 'NORMAL', 'name': '商品', 'quantity': 1, '本地SKU': 'SKU'}])
        sale = self.session.scalar(select(Sale))
        gift = self.add_gift_reference(sale, store_id=7, allocated=True)
        row = (await self.request('GET', f'/sales/{sale.id}')).json()
        self.assertFalse(row['income_only'])
        self.assertEqual(row['cost_status'], 'needs_review')
        self.assertIsNone(row['profit_cny'])
        auto, products = await self.profit_reports()
        self.assertIsNone(auto['product_purchase_cost']['current'])
        self.assertTrue(any('赠品' in note and '待核对' in note for note in auto['_warnings']))
        self.assertIsNone(products[0]['current']['cost'])
        self.assertIn('待核对', products[0]['note'])
        result = await self.request('GET', f'/sales/imports/{gift.import_batch_id}/export')
        self.assertEqual(result.status_code, 200)
        book = load_workbook(io.BytesIO(result.content), data_only=True)
        values = list(book['销售拆分明细'].values)
        detail = dict(zip(values[0], values[1]))
        self.assertEqual(detail['成本状态'], '待核对')
        self.assertIsNone(detail['毛利（元）'])

    async def test_import_deducts_all_seven_once_but_financial_cost_and_impairment_are_zero(self):
        before = self.stock()
        preview = await self.upload(self.source_rows(), preview=True)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual({row['status'] for row in preview.json()['rows']}, {'deduct'})
        self.assertEqual(sum(part['deduct_quantity'] for row in preview.json()['rows'] for part in row['components']), 7)
        self.assertEqual(self.stock(), before)
        self.assertEqual(self.session.scalars(select(SettlementEntry)).all(), [])
        response = await self.upload(self.source_rows(), token=preview.json()['preview_token'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()['sales_created'], response.json()['pending_confirmation']), (7, 0))
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 3)
        details = self.session.scalars(select(SaleCostDetail)).all()
        self.assertEqual((len(details), sum(item.quantity for item in details)), (7, 7))
        self.assertTrue(all(item.unit_cost == D(10) for item in details))
        self.assertEqual({e.raw_data[FINANCIAL_CLASSIFICATION_KEY] for e in self.session.scalars(select(SettlementEntry))}, {INCOME_ONLY_GIFT})
        self.assertTrue(all(SETTLEMENT_ONLY_KEY not in e.raw_data for e in self.session.scalars(select(SettlementEntry))))
        await self.assert_gift_ledger()
        await self.assert_profit_reports()
        again = await self.upload(self.source_rows())
        self.assertEqual(again.json()['skipped_duplicates'], 7)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 3)
        self.assertEqual(len(self.session.scalars(select(Sale)).all()), 7)
        self.assertEqual(len((await self.orders())[date(2026, 9, 1)]), 7)

    async def test_exact_store_order_sku_and_date_only_no_name_or_zero_cost_inference(self):
        source = self.source_rows()[0]
        store = self.session.get(Store, 7)
        self.assertTrue(is_income_only_order(store, source['order'], source['sku'], datetime.fromisoformat(source['ordered_on'])))
        for field, value in (('order', 'OTHER'), ('sku', 'OTHER'), ('ordered_on', '2026-09-28')):
            row = {**source, field: value, '本地SKU': 'SKU', SETTLEMENT_ONLY_KEY: INCOME_ONLY_GIFT}
            result = await self.upload([row], headers=('本地SKU', SETTLEMENT_ONLY_KEY))
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['sales_created'], 1)
        self.assertFalse(is_income_only_order(self.session.get(Store, 8), source['order'], source['sku'],
                                              datetime.fromisoformat(source['ordered_on'])))
        self.assertEqual(len(self.session.scalars(select(Sale)).all()), 3)

    async def test_other_store_with_same_order_and_sku_is_not_exempt(self):
        source = {**self.source_rows()[0], '本地SKU': 'SKU'}
        other = await self.upload([source], store=8, headers=('本地SKU',))
        self.assertEqual(other.status_code, 200, other.text)
        self.assertEqual(other.json()['sales_created'], 1)
        row = (await self.request('GET', '/sales?month=2026-09')).json()[0]
        self.assertFalse(row['income_only'])
        self.assertEqual(D(row['total_cost']), D(10))

    async def test_historical_four_linked_three_pending_read_as_gifts_without_rewriting_fifo(self):
        self.session.add(SalesImportBatch(id=1, user_id=1, store_id=7, file_type='order_detail', file_name='history.csv',
                                         sales_created=4, pending_confirmation=3))
        self.session.flush()
        for index, (order, sku, day, net, total) in enumerate(GIFTS, 1):
            entry = SettlementEntry(id=index, source_key=f'history-{index}', source_row_number=index,
                store_id=7, import_batch_id=1, order_id=order, platform_sku_id=sku, transaction_type='订单', quantity=1,
                currency='CNY', exchange_rate_to_cny=1, net_product_sales=D(net), settlement_total=D(total),
                order_created_at=datetime.fromisoformat(day), settlement_date=datetime(2026, 10, 10),
                mapping_status='confirmed' if index <= 4 else 'pending_confirmation',
                mapping_error=None if index <= 4 else '没有商品映射', raw_data={'订单ID/调整单ID': order, 'SKU ID': sku, '产品名': '赠品'})
            self.session.add(entry)
            if index <= 4:
                sale = Sale(id=index, store_id=7, user_id=1, product_id=1, order_no=f'{order}-{sku}',
                    quantity=1, selling_price=D(net), total_cost=10, platform_fee=D(net)-D(total),
                    profit=D(total)-10, sold_at=datetime.fromisoformat(day))
                self.session.add(sale); self.session.flush()
                entry.sale_id, entry.product_id = index, 1
                self.session.add(SaleCostDetail(sale_id=index, batch_id=1, quantity=1, unit_cost=10))
                self.session.add(SettlementEntryAllocation(settlement_entry_id=index, sale_id=index, product_id=1,
                    component_name='商品', quantity=1))
        self.session.get(InventoryBatch, 1).remaining_quantity = 6
        self.session.commit()
        before = self.stock()
        await self.assert_gift_ledger(fulfilled=4)
        await self.assert_profit_reports(fulfilled=4)
        detail = await self.request('GET', '/sales/1/cost-breakdown')
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(sum(batch['quantity'] for batch in detail.json()['cost_batches']), 1)
        self.assertEqual(detail.json()['impairment_batches'], [])
        exported = await self.request('GET', '/sales/export?month=2026-09')
        self.assertEqual(exported.status_code, 200, exported.text[:100])
        book = load_workbook(io.BytesIO(exported.content), data_only=True)
        records = list(book['原始结算对账'].values)
        self.assertEqual(len(records), 9)  # 7 条来源及表头/合计
        columns = {name: index for index, name in enumerate(records[0])}
        self.assertEqual(sum(row[columns['实际出库件数']] for row in records[1:-1]), 4)
        self.assertEqual(sum(row[columns['销售成本（元）']] for row in records[1:-1]), 0)
        self.assertEqual([row[columns['处理状态']] for row in records[1:-1]].count('已出库'), 4)
        self.assertEqual([row[columns['处理状态']] for row in records[1:-1]].count('待确认'), 3)
        self.assertEqual(self.stock(), before)
        self.assertEqual([s.total_cost for s in self.session.scalars(select(Sale))], [D(10)] * 4)
        self.assertEqual([d.unit_cost for d in self.session.scalars(select(SaleCostDetail))], [D(10)] * 4)
        # 未出库的三条经明确确认商品后各扣一次；历史四条绝不重扣。
        for entry_id in (5, 6, 7):
            payload = {'entry_ids': [entry_id], 'components': [{'product_id': 1, 'multiplier': 1}]}
            preview = await self.request('POST', '/sales/pending-confirmations/preview', json=payload)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()['rows'][0]['status'], 'deduct')
            result = await self.request('POST', '/sales/pending-confirmations/retry', json={
                **payload, 'preview_token': preview.json()['preview_token']})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['sales_created'], 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 3)
        self.assertEqual({self.session.get(SettlementEntry, i).mapping_status for i in (5, 6, 7)}, {'confirmed'})
        await self.assert_gift_ledger()
        repeated = await self.request('POST', '/sales/pending-confirmations/retry', json={'entry_ids': list(range(1, 8))})
        self.assertEqual((repeated.json()['sales_created'], repeated.json()['skipped_rows']), (0, 7))
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 3)

    async def test_order_type_dedup_includes_refund_fee_pending_and_gift_but_excludes_compensation(self):
        rows = [self.source_rows()[0],
            {'order': 'REFUND', 'net': -30, 'total': -25, 'quantity': -1},
            {'order': 'REFUND', 'sku': 'SECOND-SKU', 'net': -10, 'total': -8, 'quantity': 1},
            {'order': 'FEE', 'net': 0, 'total': 20, 'quantity': 0},
            {'order': 'PENDING', 'name': '没有映射商品', 'net': 60, 'total': 50, 'quantity': 1},
            {'order': 'PAY', 'type': '物流赔付', 'net': 0, 'total': 10, 'quantity': 1},
            {'order': 'PAY2', 'type': '平台赔付', 'net': 0, 'total': 10, 'quantity': 1}]
        response = await self.upload(rows)
        self.assertEqual(response.status_code, 200, response.text)
        expected = {(7, GIFTS[0][0]), (7, 'REFUND'), (7, 'FEE'), (7, 'PENDING')}
        self.assertEqual(await self.orders(), {date(2026, 9, 1): expected})
        self.assertEqual(await self.orders(8), {})
        self.assertEqual(await self.orders(start=date(2026, 10, 1), end=date(2026, 11, 1)), {})

    async def test_gift_import_rollback_restores_exact_deducted_stock_and_removes_financial_records(self):
        before = self.stock()
        response = await self.upload(self.source_rows())
        batch_id = response.json()['import_batch_id']
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 3)
        rolled_back = await self.request('POST', f'/sales/imports/{batch_id}/rollback')
        self.assertEqual(rolled_back.status_code, 200, rolled_back.text)
        self.assertEqual(rolled_back.json()['restored_quantity'], 7)
        self.assertEqual(self.stock(), before)
        self.assertEqual(await self.orders(), {})
        self.assertEqual((await self.request('GET', '/sales?month=2026-09')).json(), [])

    async def test_gift_requires_confirmed_mapping_and_stock_available_by_order_date(self):
        row = self.source_rows(mapped=False)[0]
        before = self.stock()
        response = await self.upload([row])
        self.assertEqual((response.json()['sales_created'], response.json()['pending_confirmation']), (0, 1))
        self.assertEqual(self.stock(), before)
        entry = self.session.scalar(select(SettlementEntry))
        self.session.get(InventoryBatch, 1).quantity = 1
        self.session.get(InventoryBatch, 1).remaining_quantity = 1
        for key in (2, 3):
            self.session.get(InventoryBatch, key).arrived_at = datetime(2026, 10, 1)
        self.session.commit()
        payload = {'entry_ids': [entry.id], 'components': [{'product_id': 1, 'multiplier': 2}]}
        preview = await self.request('POST', '/sales/pending-confirmations/preview', json=payload)
        self.assertEqual(preview.json()['rows'][0]['status'], 'pending')
        result = await self.request('POST', '/sales/pending-confirmations/retry', json={
            **payload, 'preview_token': preview.json()['preview_token']})
        self.assertEqual((result.json()['sales_created'], result.json()['pending_rows']), (0, 1))
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 1)
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])
        self.assertEqual(len((await self.orders())[date(2026, 9, 1)]), 1)

    async def test_bundle_gift_deducts_component_quantities_but_keeps_one_fulfillment_order(self):
        self.session.add(Product(id=2, sku='OTHER', name='组件'))
        self.session.flush()
        self.session.add(InventoryBatch(id=4, product_id=2, batch_no='OTHER', quantity=5, remaining_quantity=5,
            purchase_price=20, unit_cost=20, arrived_at=datetime(2026, 9, 1)))
        self.session.commit()
        response = await self.upload([{**self.source_rows()[0], '本地SKU': 'SKU*2+OTHER'}])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['sales_created'], 2)
        self.assertEqual((self.session.get(InventoryBatch, 1).remaining_quantity,
                          self.session.get(InventoryBatch, 4).remaining_quantity), (8, 4))
        rows = (await self.request('GET', '/sales?month=2026-09')).json()
        self.assertEqual(sorted(row['quantity'] for row in rows), [1, 2])
        self.assertEqual(sum(D(row['sales_revenue_cny']) for row in rows), D(181))
        self.assertTrue(all(D(row['total_cost']) == 0 and D(row['impairment_cny']) == 0 for row in rows))
        self.assertTrue(all(row['selling_price_cny'] is None and row['unit_total_cost_cny'] is None for row in rows))
        # 保留旧 API 的已保存原币单价，不能把整行收入塞进单价字段导致调用方再次乘数量。
        for row in rows:
            self.assertEqual(D(row['selling_price']), self.session.get(Sale, row['id']).selling_price)
        self.assertEqual(len((await self.orders())[date(2026, 9, 1)]), 1)

    async def test_old_settlement_only_gift_can_retry_once_without_new_source_or_amount_changes(self):
        response = await self.upload([self.source_rows(mapped=False)[0]])
        self.assertEqual(response.status_code, 200, response.text)
        entry = self.session.scalar(select(SettlementEntry))
        entry.mapping_status, entry.mapping_error = 'confirmed', None
        entry.raw_data = {**entry.raw_data, SETTLEMENT_ONLY_KEY: INCOME_ONLY_GIFT}
        self.session.commit()
        before = (entry.id, entry.source_key, entry.net_product_sales, entry.settlement_total, entry.exchange_rate_to_cny)
        pending = (await self.request('GET', '/sales/pending-confirmations')).json()
        self.assertEqual(pending['total'], 1)
        payload = {'entry_ids': [entry.id], 'components': [{'product_id': 1, 'multiplier': 1}]}
        preview = await self.request('POST', '/sales/pending-confirmations/preview', json=payload)
        self.assertEqual(preview.json()['rows'][0]['status'], 'deduct')
        result = await self.request('POST', '/sales/pending-confirmations/retry', json={
            **payload, 'preview_token': preview.json()['preview_token']})
        self.assertEqual(result.json()['sales_created'], 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 9)
        self.assertEqual((entry.id, entry.source_key, entry.net_product_sales, entry.settlement_total, entry.exchange_rate_to_cny), before)
        self.assertNotIn(SETTLEMENT_ONLY_KEY, entry.raw_data)
        self.assertEqual(entry.raw_data[FINANCIAL_CLASSIFICATION_KEY], INCOME_ONLY_GIFT)
        self.assertEqual((await self.request('GET', '/sales/pending-confirmations')).json()['total'], 0)
        repeat = await self.request('POST', '/sales/pending-confirmations/retry', json={'entry_ids': [entry.id]})
        self.assertEqual((repeat.json()['sales_created'], repeat.json()['skipped_rows']), (0, 1))

    async def test_order_counts_ignore_missing_type_empty_number_and_opening_deductions(self):
        await self.upload([{'order': 'VALID', 'quantity': 0, 'net': 0, 'total': 5}])
        original = self.session.scalar(select(SettlementEntry))
        for index, (number, kind) in enumerate((('NO-TYPE', None), ('ADJUST', '调整'), ('  ', '订单')), 20):
            self.session.add(SettlementEntry(id=index, source_key=f'boundary-{index}', source_row_number=index,
                store_id=7, import_batch_id=original.import_batch_id, order_id=number, platform_sku_id='X',
                transaction_type=kind, quantity=1, net_product_sales=1, settlement_total=1,
                order_created_at=datetime(2026, 9, 1), currency='CNY', raw_data={}))
        self.session.add(Sale(id=1, product_id=1, store_id=7, user_id=1, order_no='OPENING', quantity=1,
            sold_at=datetime(2026, 9, 1), selling_price=0, platform_fee=0, total_cost=0, profit=0,
            business_metadata={'kind': 'opening_deduction'}))
        self.session.flush()
        self.session.add(SettlementEntry(source_key='opening', source_row_number=30, store_id=7,
            import_batch_id=original.import_batch_id, sale_id=1, order_id='OPENING', platform_sku_id='X',
            transaction_type='订单', quantity=1, net_product_sales=0, settlement_total=0,
            order_created_at=datetime(2026, 9, 1), currency='CNY', raw_data={}))
        self.session.commit()
        self.assertEqual(await self.orders(), {date(2026, 9, 1): {(7, 'VALID')}})

    async def test_creation_month_is_shared_by_events_reports_and_reconciliation(self):
        source = {'order': 'CROSS-MONTH-FEE', 'ordered_on': '2026-09-30', 'settled_on': '2026-10-12',
                  'net': 0, 'total': 20, 'quantity': 0}
        response = await self.upload([source])
        self.assertEqual(response.status_code, 200, response.text)
        events = await unlinked_settlement_events(self.db, {7}, date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].occurred_at.date(), date(2026, 9, 30))
        self.assertEqual(events[0].original_expense, D(-20))
        self.assertEqual(await unlinked_settlement_events(self.db, {7}, date(2026, 10, 1), date(2026, 11, 1)), [])
        from services.financial_controls import reconciliation
        september = await reconciliation(self.db, 7, date(2026, 9, 1))
        october = await reconciliation(self.db, 7, date(2026, 10, 1))
        self.assertEqual(len(september['entries']), 1)
        self.assertEqual(october['entries'], [])
        entry = self.session.scalar(select(SettlementEntry))
        self.assertEqual(entry.settlement_date.date(), date(2026, 10, 12))
