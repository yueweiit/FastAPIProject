"""Source order display is independent from persisted FIFO/deduplication keys."""
import io
import unittest
from datetime import datetime
from types import SimpleNamespace

from openpyxl import load_workbook
from sqlalchemy import delete, select

from models import InventoryBatch, Product, Sale, SaleCostDetail, SettlementEntry, SettlementEntryAllocation
from tests import test_sales_import_store as fixtures
from tests.test_sales_name_import import source_file


class OrderDisplayTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ImportStoreTests.setUp
    tearDown = fixtures.ImportStoreTests.tearDown
    request = fixtures.ImportStoreTests.request

    async def upload(self, rows):
        response = await self.request('POST', '/sales/import', data={'store_id':'7'},
            files={'file':('orders.csv', source_file(rows), 'text/csv')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_two_skus_display_original_id_without_merging_cost_or_dedup_keys(self):
        rows = [{'order':'000123-A-B', 'sku':'SOURCE-A', 'name':'商品', 'quantity':1},
                {'order':'000123-A-B', 'sku':'SOURCE-B', 'name':'商品', 'quantity':2}]
        self.assertEqual((await self.upload(rows))['sales_created'], 2)
        persisted = self.session.scalars(select(Sale).order_by(Sale.id)).all()
        internal = [s.order_no for s in persisted]
        claims = [s.order_claim for s in persisted]
        self.assertEqual(len(set(internal)), 2)
        self.assertEqual(claims, internal)
        stock = self.session.get(InventoryBatch, 1).remaining_quantity
        details = [(d.sale_id, d.batch_id, d.quantity, d.unit_cost) for d in self.session.scalars(select(SaleCostDetail))]
        result = (await self.request('GET', '/sales?page=1&page_size=1')).json()
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['items'][0]['display_order_no'], '000123-A-B')
        for sale in persisted:
            single = (await self.request('GET', f'/sales/{sale.id}')).json()
            self.assertEqual((single['order_no'], single['display_order_no']), (sale.order_no, '000123-A-B'))
            cost = (await self.request('GET', f'/sales/{sale.id}/cost-breakdown')).json()
            self.assertEqual(cost['sale']['display_order_no'], '000123-A-B')
            self.assertEqual(cost['order_summary']['display_order_no'], '000123-A-B')
            self.assertEqual(cost['order_summary']['line_count'], 1)
            self.assertEqual(cost['order_summary']['order_no'], sale.order_no)
        exported = await self.request('GET', '/sales/export')
        book = load_workbook(io.BytesIO(exported.content), data_only=True)
        self.assertEqual([r[11] for r in list(book['销售记录'].values)[1:-1]], ['000123-A-B'] * 2)
        orders = list(book['订单汇总'].values)[1:]
        self.assertEqual(len(orders), 2)
        self.assertEqual([r[1] for r in orders], ['000123-A-B'] * 2)
        self.assertEqual([s.order_no for s in persisted], internal)
        self.assertEqual([s.order_claim for s in persisted], claims)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, stock)
        self.assertEqual([(d.sale_id, d.batch_id, d.quantity, d.unit_cost) for d in self.session.scalars(select(SaleCostDetail))], details)
        self.assertEqual((await self.upload(rows))['skipped_duplicates'], 2)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, stock)
        self.actor = SimpleNamespace(id=2, role='viewer', store_id=8)
        self.assertEqual((await self.request('GET', '/sales')).json(), [])

    async def test_old_direct_source_uses_original_id_and_missing_source_does_not_guess(self):
        await self.upload([{'order':'00017-SOURCE-ID', 'sku':'SOURCE-ID', 'name':'商品', 'quantity':1}])
        sale = self.session.scalar(select(Sale))
        entry = self.session.scalar(select(SettlementEntry))
        self.session.execute(delete(SettlementEntryAllocation))
        self.session.commit()
        response = (await self.request('GET', f'/sales/{sale.id}')).json()
        self.assertEqual(response['display_order_no'], '00017-SOURCE-ID')
        raw = dict(entry.raw_data)
        raw.pop('订单ID/调整单ID')
        entry.raw_data = raw
        self.session.commit()
        response = (await self.request('GET', f'/sales/{sale.id}')).json()
        self.assertEqual(response['display_order_no'], sale.order_no)

    async def test_bundle_components_keep_one_internal_group_with_original_number(self):
        self.session.add(Product(id=2, sku='ACCESSORY', name='配件'))
        self.session.flush()
        self.session.add(InventoryBatch(id=4, product_id=2, batch_no='BUNDLE', quantity=10,
            remaining_quantity=10, purchase_price=30, unit_cost=30, arrived_at=datetime(2026,9,1)))
        self.session.commit()
        await self.upload([{'order':'00042-KIT', 'sku':'KIT-SKU', 'name':'商品*2+配件', 'quantity':1}])
        rows = (await self.request('GET', '/sales')).json()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['display_order_no'] for r in rows}, {'00042-KIT'})
        self.assertEqual(len({r['order_no'] for r in rows}), 1)
        cost = (await self.request('GET', f"/sales/{rows[0]['id']}/cost-breakdown")).json()
        self.assertEqual(cost['order_summary']['line_count'], 2)
        self.assertEqual(cost['order_summary']['display_order_no'], '00042-KIT')
        self.assertEqual({r['display_order_no'] for r in cost['order_lines']}, {'00042-KIT'})
        self.assertEqual([self.session.get(InventoryBatch, key).remaining_quantity for key in (1,4)], [8,9])

    async def test_legacy_csv_and_hand_entered_hyphenated_order_numbers_are_unchanged(self):
        response = await self.request('POST', '/sales/import', data={'store_id':'7'},
            files={'file':('legacy.csv', b'SKU,2026-09-01,1\n', 'text/csv')})
        self.assertEqual(response.status_code, 200, response.text)
        legacy = (await self.request('GET', '/sales')).json()[0]
        self.assertEqual(legacy['display_order_no'], legacy['order_no'])
        response = await self.request('POST', '/sales', json={'product_id':1, 'store_id':7,
            'order_no':'MANUAL-00017-SKU', 'quantity':1, 'sold_at':'2026-09-02T12:00:00'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['display_order_no'], 'MANUAL-00017-SKU')

    async def test_excel_source_order_is_literal_text_and_financial_events_remain_original(self):
        await self.upload([{'order':'=1+2', 'sku':'SOURCE', 'name':'商品', 'quantity':1},
            {'type':'物流赔付', 'order':'0007-CLAIM', 'name':'赔付', 'quantity':0, 'net':0, 'total':5}])
        rows = (await self.request('GET', '/sales')).json()
        self.assertEqual({r['display_order_no'] for r in rows}, {'=1+2', '0007-CLAIM'})
        exported = await self.request('GET', '/sales/export')
        book = load_workbook(io.BytesIO(exported.content))
        cells = [book['销售记录'].cell(row, 12) for row in (2, 3)]
        self.assertEqual({cell.value for cell in cells}, {'=1+2', '0007-CLAIM'})
        self.assertTrue(all(cell.data_type == 's' for cell in cells))
        self.assertEqual(book['订单汇总']['B2'].value, '=1+2')
        self.assertEqual(book['订单汇总']['B2'].data_type, 's')
