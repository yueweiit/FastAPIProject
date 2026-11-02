"""原币核对字段：隔离SQLite/ASGI及Excel，不连接业务库或查询外部汇率。"""
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from models import InventoryBatch, Product, Sale, SaleCostDetail, SettlementEntry, SettlementEntryAllocation
from tests import test_sales_export as exports


class SalesCurrencyReconciliationTests(unittest.IsolatedAsyncioTestCase):
    entry = exports.SalesExportTests.entry
    setUp = exports.SalesExportTests.setUp
    asyncTearDown = exports.SalesExportTests.asyncTearDown
    record_query = exports.SalesExportTests.record_query
    exported = exports.SalesExportTests.exported

    async def listed(self, **params):
        response = await self.client.get('/sales', params={'month': '2026-09', **params})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        return data['items'] if isinstance(data, dict) else data

    def mxn_order(self):
        entry = self.session.get(SettlementEntry, 1)
        entry.net_product_sales, entry.settlement_total = D('84.90'), D('58.73')
        entry.currency, entry.exchange_rate_to_cny = 'MXN', D('0.36989000')
        entry.exchange_rate_date, entry.exchange_rate_source = date(2026, 9, 30), 'Frankfurter/ECB'
        entry.quantity = 1
        sale = self.session.get(Sale, 1)
        sale.quantity, sale.selling_price, sale.platform_fee, sale.total_cost = 1, D('84.9'), D('26.17'), D('5.4')
        batch = self.session.get(InventoryBatch, 1)
        batch.remaining_quantity, batch.purchase_price, batch.unit_cost = 9, D('5.4'), D('5.4')
        detail = self.session.scalar(select(SaleCostDetail))
        detail.quantity, detail.unit_cost = 1, D('5.4')
        self.session.commit()
        return entry

    async def test_mxn_source_rate_and_cny_amounts_reconcile_without_writes(self):
        self.mxn_order()
        self.queries.clear()
        with patch('routers.sales.get_cny_rates', new=AsyncMock(side_effect=AssertionError('must use saved rate'))):
            row = next(r for r in await self.listed() if r['id'] == 1)
            detail = await self.client.get('/sales/1')
            self.assertEqual(detail.status_code, 200, detail.text)
            workbook = await self.exported()
        self.assertEqual(row['currency'], 'MXN')
        self.assertEqual(D(row['source_row_net_product_sales']), D('84.9'))
        self.assertEqual(D(row['source_row_settlement_total']), D('58.73'))
        self.assertEqual(D(row['exchange_rate_to_cny']), D('.36989'))
        self.assertEqual(row['exchange_rate_date'], '2026-09-30')
        self.assertEqual(D(row['sales_revenue_cny']), D('31.403661'))
        self.assertEqual(D(row['platform_fee_cny']), D('9.6800213'))
        self.assertEqual(D(row['profit_cny']), D('16.3236397'))
        self.assertEqual(detail.json()['source_row_net_product_sales'], row['source_row_net_product_sales'])
        sheet = workbook['销售记录']
        headers = [c.value for c in sheet[1]]
        cells = next(c for c in sheet.iter_rows(min_row=2, max_row=sheet.max_row-1) if c[11].value == 'SALE')
        for label, expected in [('来源净商品销售额（原币）', D('84.9')), ('来源结算总金额（原币）', D('58.73'))]:
            index = headers.index(label)
            self.assertEqual(D(str(cells[index].value)), expected)
            self.assertNotIn('¥', cells[index].number_format)
            self.assertIsNone(sheet.cell(sheet.max_row, index+1).value)
        self.assertEqual(cells[headers.index('汇率来源')].value, 'Frankfurter/ECB')
        self.assertTrue(set(self.queries) <= {'SELECT'}, self.queries)

    async def test_bundle_repeats_original_row_while_cny_shares_stay_allocated(self):
        self.session.add(Product(id=11, sku='PART-2', name='第二组件'))
        self.session.flush()
        self.session.add(Sale(id=2, product_id=11, user_id=1, store_id=7, order_no='SALE',
            quantity=1, selling_price=100, total_cost=0, platform_fee=10, profit=90,
            sold_at=datetime(2026, 9, 10)))
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=1, product_id=11,
            sale_id=2, component_name='PART-2', quantity=1))
        self.session.commit()
        rows = [r for r in await self.listed() if r['record_type'] == 'sale']
        self.assertEqual(len(rows), 2)
        self.assertEqual({D(r['source_row_net_product_sales']) for r in rows}, {D(100)})
        self.assertEqual({D(r['source_row_settlement_total']) for r in rows}, {D(90)})
        self.assertEqual(sum(D(r['sales_revenue_cny']) for r in rows), D(100))
        filtered = await self.listed(product_id=11, page=1, page_size=1)
        self.assertEqual(D(filtered[0]['source_row_net_product_sales']), D(100))
        self.assertEqual(D(filtered[0]['sales_revenue_cny']), D(50))

    async def test_refund_and_compensation_preserve_actual_source_net_and_settlement(self):
        rows = {r['id']: r for r in await self.listed()}
        self.assertEqual(D(rows[-2]['source_row_net_product_sales']), D(-20))
        self.assertEqual(D(rows[-2]['source_row_settlement_total']), D(-18))
        self.assertEqual(D(rows[-3]['source_row_net_product_sales']), D(0))
        self.assertEqual(D(rows[-3]['source_row_settlement_total']), D(5))
        self.assertEqual(D(rows[-3]['sales_revenue_cny']), D(5))

    async def test_missing_fx_keeps_original_amounts_and_unknown_cny(self):
        entry = self.mxn_order()
        entry.exchange_rate_to_cny = None
        self.session.commit()
        row = next(r for r in await self.listed() if r['id'] == 1)
        self.assertEqual(D(row['source_row_net_product_sales']), D('84.9'))
        self.assertEqual(D(row['source_row_settlement_total']), D('58.73'))
        self.assertIsNone(row['sales_revenue_cny'])
        self.assertIsNone(row['profit_cny'])

    async def test_manual_cny_settlement_displays_supplied_source_amounts(self):
        response = await self.client.post('/sales/manual-settlements', json={
            'store_id': 7, 'request_key': 'currency-manual-0001', 'occurred_on': '2026-09-30',
            'order_no': 'MANUAL-CNY', 'quantity': 0, 'net_product_sales': '12.3456', 'settlement_total': '10.1234',
        })
        self.assertEqual(response.status_code, 200, response.text)
        row = next(r for r in await self.listed() if r['order_no'] == 'MANUAL-CNY')
        self.assertEqual(row['currency'], 'CNY')
        self.assertEqual(D(row['source_row_net_product_sales']), D('12.3456'))
        self.assertEqual(D(row['source_row_settlement_total']), D('10.1234'))
        self.assertEqual(D(row['exchange_rate_to_cny']), D(1))
        self.assertEqual(row['quantity'], 0)

    async def test_legacy_direct_link_and_missing_source_do_not_invent_amounts(self):
        entry = self.session.get(SettlementEntry, 1)
        self.session.delete(self.session.scalar(select(SettlementEntryAllocation)))
        entry.sale_id = 1
        self.session.commit()
        row = next(r for r in await self.listed() if r['id'] == 1)
        self.assertEqual(D(row['source_row_net_product_sales']), D(100))
        self.assertEqual(D(row['source_row_settlement_total']), D(90))
        entry.sale_id = None
        self.session.commit()
        row = next(r for r in await self.listed() if r['id'] == 1)
        self.assertIsNone(row['source_row_net_product_sales'])
        self.assertIsNone(row['source_row_settlement_total'])


if __name__ == '__main__':
    unittest.main()
