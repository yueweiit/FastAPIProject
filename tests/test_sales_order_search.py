"""Unified order keyword filtering: SQLite/ASGI, no business database writes."""
import io
import unittest
from types import SimpleNamespace

from openpyxl import load_workbook
from sqlalchemy import delete, event, select

from models import Sale, SettlementEntry, SettlementEntryAllocation
from tests import test_sales_import_store as fixtures
from tests import test_sales_order_display as display


class OrderSearchTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ImportStoreTests.setUp
    tearDown = fixtures.ImportStoreTests.tearDown
    request = fixtures.ImportStoreTests.request
    upload = display.OrderDisplayTests.upload

    async def seed(self):
        await self.upload([
            {'order':'000123-LOOKUP', 'sku':'SOURCE-A', 'name':'商品*2', 'quantity':1},
            {'order':'000123-LOOKUP', 'sku':'SOURCE-B', 'name':'商品', 'quantity':1},
            {'order':'999-OTHER', 'sku':'SOURCE-A', 'name':'商品', 'quantity':1},
            {'order':'000123-LOOKUP', 'sku':'/', 'type':'物流赔付', 'name':'赔付', 'quantity':0, 'net':0, 'total':5}])

    async def test_original_order_and_partial_keyword_match_list_summary_and_all_export_rows(self):
        await self.seed()
        statements = []
        def record(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement.strip().split()[0].upper())
        event.listen(self.engine, 'before_cursor_execute', record)
        try:
            for keyword in (' 000123-LOOKUP ', '123-LOOK'):
                params = {'keyword':keyword, 'month':'2026-09'}
                response = await self.request('GET', '/sales', params=params)
                self.assertEqual(response.status_code, 200, response.text)
                rows = response.json()
                self.assertEqual(len(rows), 3)
                self.assertEqual({r['display_order_no'] for r in rows}, {'000123-LOOKUP'})
                summary = (await self.request('GET', '/sales/monthly-summary', params=params)).json()
                self.assertEqual((summary[0]['sales_count'], summary[0]['settlement_event_count'], summary[0]['sold_quantity']), (2,1,3))
                page = (await self.request('GET', '/sales', params={**params, 'page':1, 'page_size':1})).json()
                self.assertEqual((page['total'], len(page['items'])), (3,1))
                response = await self.request('GET', '/sales/export', params=params)
                self.assertEqual(response.status_code, 200, response.text[:80])
                book = load_workbook(io.BytesIO(response.content), data_only=True)
                exported = list(book['销售记录'].values)[1:-1]
                self.assertEqual(len(exported), 3)
                self.assertEqual({r[11] for r in exported}, {'000123-LOOKUP'})
                self.assertEqual(book['说明']['A3'].value, '商品名称/SKU/订单号')
                self.assertEqual(book['说明']['B3'].value, keyword.strip())
            params = {'keyword':'000123-LOOKUP', 'month':'2026-10'}
            self.assertEqual((await self.request('GET', '/sales', params=params)).json(), [])
            self.assertEqual((await self.request('GET', '/sales/monthly-summary', params=params)).json(), [])
            self.assertEqual((await self.request('GET', '/sales/export', params=params)).status_code, 404)
            scoped = (await self.request('GET', '/sales', params={'keyword':'000123-LOOKUP','product_id':1})).json()
            self.assertEqual(len(scoped), 2)
        finally:
            event.remove(self.engine, 'before_cursor_execute', record)
        self.assertTrue(statements)
        self.assertEqual(set(statements), {'SELECT'})

    async def test_direct_legacy_source_and_displayed_raw_order_are_searchable(self):
        await self.seed()
        entry = self.session.scalar(select(SettlementEntry).where(SettlementEntry.platform_sku_id=='SOURCE-B'))
        sale = self.session.get(Sale, entry.sale_id)
        self.session.execute(delete(SettlementEntryAllocation).where(SettlementEntryAllocation.sale_id==sale.id))
        entry.order_id = 'persisted-source-id'
        sale.order_no = 'internal-opaque-key'
        self.session.commit()
        rows = (await self.request('GET', '/sales', params={'keyword':'000123-LOOKUP'})).json()
        self.assertIn(sale.id, {row['id'] for row in rows})
        persisted = (await self.request('GET', '/sales', params={'keyword':'persisted-source-id'})).json()
        self.assertEqual([row['id'] for row in persisted], [sale.id])
        internal = (await self.request('GET', '/sales', params={'keyword':'internal-opaque-key'})).json()
        self.assertEqual([row['id'] for row in internal], [sale.id])

    async def test_keyword_keeps_store_permissions_for_sales_and_financial_events(self):
        await self.seed()
        response = await self.request('POST', '/sales', json={'product_id':1, 'store_id':8,
            'order_no':'000123-LOOKUP-STORE8', 'quantity':1, 'sold_at':'2026-09-02T12:00:00'})
        self.assertEqual(response.status_code, 200, response.text)
        foreign_sale = response.json()['id']
        response = await self.request('POST', '/sales/manual-settlements', json={'store_id':8,
            'request_key':'order-search-store8-001', 'occurred_on':'2026-09-02', 'order_no':'000123-LOOKUP-STORE8',
            'transaction_type':'物流赔付', 'quantity':0, 'net_product_sales':0, 'settlement_total':9})
        self.assertEqual(response.status_code, 200, response.text)
        params = {'keyword':'000123-LOOKUP', 'month':'2026-09'}
        self.assertEqual(len((await self.request('GET', '/sales', params=params)).json()), 5)
        for role in ('operator','viewer'):
            self.actor = SimpleNamespace(id=2, role=role, store_id=7)
            rows = (await self.request('GET', '/sales', params=params)).json()
            self.assertEqual(len(rows), 3)
            self.assertNotIn(foreign_sale, {row['id'] for row in rows})
            summary = (await self.request('GET', '/sales/monthly-summary', params=params)).json()[0]
            self.assertEqual((summary['sales_count'],summary['settlement_event_count']), (2,1))
            exported = await self.request('GET', '/sales/export', params=params)
            self.assertEqual(len(list(load_workbook(io.BytesIO(exported.content))['销售记录'].values)[1:-1]), 3)
        self.actor.store_id = None
        for route in ('/sales','/sales/monthly-summary','/sales/export'):
            self.assertEqual((await self.request('GET', route, params=params)).status_code, 403)

    async def test_manual_and_legacy_csv_fallback_numbers_remain_searchable(self):
        response = await self.request('POST', '/sales/import', data={'store_id':'7'},
            files={'file':('legacy.csv', b'SKU,2026-09-01,1\n', 'text/csv')})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.request('POST', '/sales', json={'product_id':1, 'store_id':7,
            'order_no':'MANUAL-00017-SKU', 'quantity':1, 'sold_at':'2026-09-02T12:00:00'})
        self.assertEqual(response.status_code, 200, response.text)
        for keyword in ('2026-09-01-SKU','MANUAL-00017-SKU'):
            rows = (await self.request('GET', '/sales', params={'keyword':keyword})).json()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['display_order_no'], keyword)
