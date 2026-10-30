"""单件成本、整单解释与公式导出：隔离SQLite/ASGI，不连接业务库。"""
import unittest
from datetime import date, datetime
from decimal import Decimal as D, ROUND_HALF_UP
from unittest.mock import patch

from sqlalchemy import select
from fastapi import HTTPException

from models import (InventoryBatch, Product, Sale, SaleCostDetail, User,
                    InventoryCostAdjustment, InventoryCostAdjustmentLine, ProductLine, StoreProduct)
from routers.sales import _order_cost_data
from services.cost_layers import fifo_cost_components
from services.sales_fields import sales_field_help
from tests import test_sales_export as exports


class SalesCostExplanationTests(unittest.IsolatedAsyncioTestCase):
    entry = exports.SalesExportTests.entry
    setUp = exports.SalesExportTests.setUp
    asyncTearDown = exports.SalesExportTests.asyncTearDown
    record_query = exports.SalesExportTests.record_query
    exported = exports.SalesExportTests.exported
    add_sale = exports.SalesExportTests.add_sale

    async def explanation(self, key=1):
        response = await self.client.get(f'/sales/{key}/cost-breakdown')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def sibling(self, *, store=7, user=1):
        self.session.add(Product(id=11, sku='COMPONENT-2', name='组合组件二'))
        self.session.add(InventoryBatch(id=2, product_id=11, user_id=user, batch_no='SECOND',
            quantity=10, remaining_quantity=8, purchase_price=3, unit_cost=3,
            arrived_at=datetime(2026,9,1)))
        self.session.flush()
        self.session.add(Sale(id=2,product_id=11,user_id=user,store_id=store,order_no='SALE',
            quantity=2,selling_price=20,total_cost=6,platform_fee=0,profit=34,sold_at=datetime(2026,10,1)))
        self.session.flush()
        self.session.add(SaleCostDetail(sale_id=2,batch_id=2,quantity=2,unit_cost=3))
        self.session.commit(); self.db.info.clear()

    async def test_unit_cost_uses_unrounded_components_and_four_decimal_places(self):
        batch=self.session.get(InventoryBatch,1)
        batch.purchase_price=D('1.2345');batch.shipping_cost=D('7.6543')
        batch.last_mile_cost=D('0.1234');batch.other_cost=D('0.2345');batch.unit_cost=D('2.0357')
        batch.remaining_quantity=7
        sale=self.session.get(Sale,1);sale.quantity=3;sale.total_cost=D('6.1071')
        detail=self.session.scalar(select(SaleCostDetail));detail.quantity=3;detail.unit_cost=D('2.0357')
        self.session.commit();self.db.info.clear()
        data=await self.explanation();row=data['sale']
        parts=fifo_cost_components(detail,batch)
        for key in ('purchase','head_shipping','last_mile','other'):
            expected=(parts[key]/D(3)).quantize(D('.0001'),rounding=ROUND_HALF_UP)
            self.assertEqual(D(row['unit_'+key+'_cost_cny']),expected)
        self.assertEqual(D(row['unit_total_cost_cny']),D('2.0357'))
        # 两位的本行采购展示额不能反推正确的四位单件采购成本。
        self.assertNotEqual(D(row['unit_purchase_cost_cny']),
            (D(row['purchase_cost_cny'])/3).quantize(D('.0001'),rounding=ROUND_HALF_UP))
        self.assertEqual(sum(D(row[key+'_cost_cny']) for key in ('purchase','head_shipping','last_mile','other')),D(row['order_total_cost_cny']))
        self.assertEqual(D(data['cost_batches'][0]['total_cost_cny']),D('6.1071'))
        self.assertEqual(data['quantity_basis'],'inventory_units')

    async def test_order_is_complete_across_product_month_and_page_filters(self):
        self.sibling()
        self.queries.clear()
        response=await self.client.get('/sales',params={'month':'2026-09','product_id':10,'page':2,'page_size':1})
        self.assertEqual(response.status_code,200)
        data=await self.explanation(response.json()['items'][0]['id'])
        self.assertEqual({row['id'] for row in data['order_lines']},{1,2})
        summary=data['order_summary']
        self.assertTrue(summary['complete']);self.assertEqual(summary['line_count'],2)
        self.assertEqual(D(summary['total_cost_cny']),46)
        self.assertEqual(D(summary['impairment_cny']),sum(D(row['impairment_cny']) for row in data['order_lines']))
        self.assertEqual(data['cost_batches'][0]['batch_no'],'TEST')
        self.assertEqual(sum(D(row['impairment_cny']) for row in data['impairment_batches']),D(data['sale']['impairment_cny']))
        book=await self.exported(product_id=10)
        orders=list(book['订单汇总'].values)
        self.assertEqual(orders[1][1:4],('SALE',2,46))
        values=list(book['销售记录'].values)
        self.assertTrue(all(row[2]=='P10' or row[2]=='-' for row in values[1:-1]))
        self.assertEqual(book['销售记录'].max_column,29)
        self.assertEqual(next(row for row in values[1:-1] if row[11]=='SALE')[23:27],(10,0,0,0))
        self.assertTrue(all(value is None for value in values[-1][23:27]))
        self.assertTrue(set(self.queries)<={'SELECT'})

    async def test_multi_batch_weighted_units_follow_late_cost_and_reversal(self):
        batch=self.session.get(InventoryBatch,1)
        batch.purchase_price=5;batch.shipping_cost=20;batch.last_mile_cost=10;batch.other_cost=20
        batch.remaining_quantity=8
        detail=self.session.scalar(select(SaleCostDetail));detail.quantity=2
        self.session.get(Product,10).product_type='new'
        self.session.add(InventoryBatch(id=2,product_id=10,user_id=1,batch_no='SECOND',quantity=3,
            remaining_quantity=1,purchase_price=10,shipping_cost=15,last_mile_cost=9,other_cost=6,
            unit_cost=20,arrived_at=datetime(2026,9,5)))
        self.session.flush()
        self.session.add(SaleCostDetail(sale_id=1,batch_id=2,quantity=2,unit_cost=20))
        self.session.get(Sale,1).total_cost=60
        self.session.commit();self.db.info.clear()
        data=await self.explanation()
        self.assertEqual([D(data['sale']['unit_'+key+'_cost_cny']) for key in ('purchase','head_shipping','last_mile','other','total')],
                         [D('7.5'),D('3.5'),D(2),D(2),D(15)])
        with patch.dict('os.environ',{'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            for key,amount,expected in ((1,20,16),(2,-20,15)):
                self.session.add(InventoryCostAdjustment(id=key,request_key=f'unit-{key}',source_ref=f'unit-{key}',
                    category='head_shipping',allocation_basis='quantity',amount=amount,amount_cny=amount,currency='CNY',
                    exchange_rate=1,document_date=datetime(2026,10,30).date(),rate_date=datetime(2026,10,30).date(),
                    is_final=True,created_by=1,reverses_id=1 if key==2 else None))
                self.session.flush()
                self.session.add(InventoryCostAdjustmentLine(adjustment_id=key,batch_id=1,quantity=10,allocation_weight=10,amount_cny=amount))
                self.session.commit();self.db.info.clear()
                data=await self.explanation()
                self.assertEqual(D(data['sale']['unit_total_cost_cny']),expected)
                self.assertEqual(D(data['sale']['unit_head_shipping_cost_cny']),D('4.5') if key==1 else D('3.5'))
                self.assertEqual(self.session.get(Sale,1).total_cost,60)
                self.assertEqual(self.session.scalar(select(SaleCostDetail).where(SaleCostDetail.batch_id==1)).unit_cost,10)

    async def test_order_scope_does_not_merge_same_number_across_stores_or_legacy_owners(self):
        self.sibling(store=None)
        self.session.add(User(id=2,username='shop8',role='operator',store_id=8,password_hash='test'))
        self.session.flush()
        self.session.add(Sale(id=3,product_id=11,user_id=2,store_id=None,order_no='SALE',quantity=1,
            selling_price=30,total_cost=3,platform_fee=0,profit=27,sold_at=datetime(2026,9,20)))
        self.session.flush();self.session.add(SaleCostDetail(sale_id=3,batch_id=2,quantity=1,unit_cost=3))
        self.session.commit();self.db.info.clear()
        for role in ('operator','viewer','admin'):
            self.actor.role=role
            data=await self.explanation()
            self.assertEqual({row['id'] for row in data['order_lines']},{1,2})
            self.assertEqual(D(data['order_summary']['total_cost_cny']),46)
        self.actor.role='viewer'
        self.assertEqual((await self.client.get('/sales/3/cost-breakdown')).status_code,404)
        self.actor.store_id=None
        self.assertEqual((await self.client.get('/sales/1/cost-breakdown')).status_code,403)

    async def test_unassigned_history_does_not_guess_whole_order(self):
        self.actor.role='admin'
        sale=self.session.get(Sale,1);sale.store_id=None;sale.user_id=None
        self.session.commit();self.db.info.clear()
        data=await self.explanation()
        self.assertFalse(data['order_summary']['complete'])
        self.assertIsNone(data['order_summary']['total_cost_cny'])
        self.assertIsNone(data['order_summary']['impairment_cny'])
        self.assertIn('店铺归属缺失',data['order_summary']['review_reason'])

    async def test_order_store_name_matches_resolved_id_with_missing_or_conflicting_mapping(self):
        sale=self.session.get(Sale,1);sale.store_id=None
        self.session.commit();self.db.info.clear()
        for mapping in (False,True):
            if mapping:
                self.session.add(ProductLine(id=1,code='TEST',name='测试产品线'));self.session.flush()
                self.session.add(StoreProduct(store_id=8,product_id=10,product_line_id=1,
                    store_sku='P10',platform_sku_id='P10',effective_from=date(2026,1,1)))
                self.session.commit();self.db.info.clear()
            for role in ('operator','viewer','admin'):
                self.actor.role=role
                data=await self.explanation()
                summary=data['order_summary']
                self.assertEqual((summary['store_id'],summary['store_name']),(7,'S7'))
                self.assertTrue(summary['complete'])
                self.assertEqual(data['sale']['store_name'],'S7')
                self.assertEqual({row['store_name'] for row in data['order_lines']},{'S7'})
                book=await self.exported()
                self.assertEqual(list(book['订单汇总'].values)[1][0],'S7')
        # 固化店铺优先于创建人的绑定和商品映射。
        sale.store_id=8;self.session.commit();self.db.info.clear()
        data=await self.explanation()
        self.assertEqual((data['order_summary']['store_id'],data['order_summary']['store_name']),(8,'S8'))
        self.actor.role='operator'
        self.assertEqual((await self.client.get('/sales/1/cost-breakdown')).status_code,404)
        self.actor.role='admin';sale.store_id=None;sale.user_id=None
        self.session.commit();self.db.info.clear()
        data=await self.explanation()
        self.assertIsNone(data['order_summary']['store_name'])
        self.assertIsNone(data['sale']['store_name'])
        self.assertFalse(data['order_summary']['complete'])

    async def test_no_outbound_unknown_costs_and_limits_remain_explicit(self):
        rows=(await self.client.get('/sales')).json()
        for row in rows:
            if row['quantity']==0:
                self.assertTrue(all(row['unit_'+key+'_cost_cny'] is None for key in ('purchase','head_shipping','last_mile','other','total')))
                self.assertEqual((await self.client.get(f"/sales/{row['id']}/cost-breakdown")).status_code,404)
        self.sibling()
        with self.assertRaises(HTTPException) as caught:
            await _order_cost_data(self.db,[self.session.get(Sale,1)],self.actor,max_rows=1)
        self.assertEqual(caught.exception.detail['code'],'sales_order_too_large')
        self.session.get(InventoryBatch,1).voided_at=datetime(2026,10,1)
        self.session.commit();self.db.info.clear()
        data=await self.explanation()
        self.assertIsNone(data['sale']['unit_total_cost_cny'])
        self.assertIsNone(data['order_summary']['total_cost_cny'])
        self.assertIsNone(data['order_summary']['impairment_cny'])

    async def test_export_formulas_reuse_the_same_field_resource(self):
        book=await self.exported()
        fields={row[0]:row for row in list(book['字段口径说明'].values)[1:] if row[0]}
        resource=sales_field_help()
        for meta in [*resource['fields'],*resource['extra_fields']]:
            if meta['kind']!='action':
                self.assertEqual(fields[meta['key']][1:6],tuple(meta[key] for key in ('label','group','formula','source','note')))
        self.assertEqual(len(resource['fields']),24)
