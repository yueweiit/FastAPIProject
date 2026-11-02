"""销售成本拆分/跌价的隔离SQLite、真实导入与Excel勾稽回归。"""
import os
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace as NS

from openpyxl import load_workbook
from sqlalchemy import select

from models import (BatchImpairmentStore, InventoryBatch, InventoryCostAdjustment,
                    InventoryCostAdjustmentLine, PlatformSkuComponent, PlatformSkuMapping,
                    Product, ProductImpairmentRule, Sale, SaleCostDetail, SettlementEntry)
from services.cost_layers import fifo_cost_components, fifo_cost_groups, rounded_cost_components
from tests import test_sales_export as exports, test_sales_import_store as imports

FIELDS = ('purchase_cost_cny', 'head_shipping_cost_cny', 'last_mile_cost_cny', 'other_cost_cny')


class CostAllocationTests(unittest.TestCase):
    def test_saved_fifo_precision_and_report_grouping_are_preserved(self):
        batch = NS(quantity=7, purchase_price=D('3.1'), shipping_cost=D('8.2'),
                   last_mile_cost=D('2.3'), other_cost=D('1.1'), cost_basis=None)
        detail = NS(quantity=3, unit_cost=D('4.7571'))
        parts = fifo_cost_components(detail, batch)
        self.assertAlmostEqual(sum(parts.values()), D('14.2713'), places=24)
        groups = fifo_cost_groups(detail, batch)
        self.assertEqual((parts['purchase'], parts['head_shipping']+parts['last_mile'], parts['other']), groups)
        shown = rounded_cost_components(parts, D('14.2713'))
        self.assertEqual(sum(shown.values()), D('14.27'))
        self.assertTrue(all(value.as_tuple().exponent == -2 for value in shown.values()))

    def test_frozen_basis_late_cost_and_reversal_use_original_quantities(self):
        batch = NS(quantity=1000, cost_basis={'components': dict(zip(
            ('purchase','head_shipping','last_mile','other'), ('3','2','1','0')))})
        detail = NS(quantity=3, unit_cost=D(6))
        layers = [(NS(amount_cny=D(20), quantity=10), NS(category='last_mile')),
                  (NS(amount_cny=D(-10), quantity=10), NS(category='last_mile')),
                  (NS(amount_cny=D(5), quantity=10), NS(category='other'))]
        self.assertEqual(list(fifo_cost_components(detail,batch,layers).values()), [D(9),D(6),D(6),D('1.5')])

    def test_zero_source_legacy_cost_and_zero_logistics_are_compatible(self):
        batch = NS(quantity=1, cost_basis=None, purchase_price=D(0), shipping_cost=D(0),
                   last_mile_cost=D(0), other_cost=D(0))
        self.assertEqual(list(fifo_cost_components(NS(quantity=2,unit_cost=D(7)),batch).values()),
                         [D(14),D(0),D(0),D(0)])
        batch.purchase_price=D(7)
        self.assertEqual(fifo_cost_components(NS(quantity=1,unit_cost=D(7)),batch)['head_shipping'],0)

    def test_cent_remainder_is_balanced_including_zero_and_tiny_amounts(self):
        for values in (('0.005','0.005','0.005','0.005'),('0','0','0','0'),('0.001','0.001','0.001','0.001')):
            parts=dict(zip(('purchase','head_shipping','last_mile','other'),map(D,values)))
            rounded=rounded_cost_components(parts,sum(parts.values()))
            self.assertEqual(sum(rounded.values()),sum(parts.values()).quantize(D('.01')))
            self.assertTrue(all(value>=0 for value in rounded.values()))


class SalesCostTests(unittest.IsolatedAsyncioTestCase):
    entry = exports.SalesExportTests.entry
    setUp = exports.SalesExportTests.setUp
    asyncTearDown = exports.SalesExportTests.asyncTearDown
    record_query = exports.SalesExportTests.record_query
    exported = exports.SalesExportTests.exported
    rows = exports.SalesExportTests.rows

    def costs(self, *, kind='new', safe=0):
        product=self.session.get(Product,10)
        product.product_type,product.safe_stock_quantity=kind,safe
        batch=self.session.get(InventoryBatch,1)
        batch.purchase_price,batch.shipping_cost,batch.last_mile_cost,batch.other_cost=D(5),D(20),D(10),D(20)
        self.session.commit()

    async def listed(self, **params):
        response=await self.client.get('/sales',params={'month':'2026-09',**params})
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        return data['items'] if isinstance(data,dict) else data

    def assert_parts(self,row,values):
        self.assertEqual([D(row[field]) for field in FIELDS],list(map(D,values)))
        self.assertEqual(sum(D(row[field]) for field in FIELDS),D(row['order_total_cost_cny']))

    async def test_list_detail_export_components_and_impairment_reconcile(self):
        self.costs()
        row=next(r for r in await self.listed() if r['id']==1)
        self.assert_parts(row,(20,8,4,8))
        self.assertEqual(D(row['impairment_cny']),D('1.8')) # 4 * 10 * 9 days * .5%
        self.assertEqual(D(row['profit_cny']),50) # no second impairment charge
        detail=await self.client.get('/sales/1')
        for field in (*FIELDS,'order_total_cost_cny','impairment_cny'):
            self.assertEqual(detail.json()[field],row[field])
        book=await self.exported()
        exported=next(r for r in self.rows(book) if r[11]=='SALE')
        self.assertEqual(exported[18:23],(20,8,4,8,1.8))
        self.assertEqual(sum(exported[18:22]),exported[8])
        total=list(book['销售记录'].values)[-1]
        self.assertEqual(total[18:23],exported[18:23])
        self.assertEqual(book['销售记录'].auto_filter.ref,'A1:AF4')
        for event in (r for r in await self.listed() if r['id']<0):
            self.assert_parts(event,(0,0,0,0))
            self.assertEqual(D(event['impairment_cny']),0)

    async def test_four_rules_arrival_day_and_rule_history(self):
        for kind,safe,expected in (('new',0,'1.8'),('stable',0,'3.6'),
                                    ('safe_stock',8,'1.8'),('no_impairment',0,'0')):
            self.db.info.clear()
            self.costs(kind=kind,safe=safe)
            row=next(r for r in await self.listed() if r['id']==1)
            self.assertEqual(D(row['impairment_cny']),D(expected),(kind,row))
        self.costs(kind='stable')
        self.session.add_all([ProductImpairmentRule(product_id=10,product_type='stable',safe_stock_quantity=0,
                                                   effective_date=date(2026,9,1)),
                              ProductImpairmentRule(product_id=10,product_type='new',safe_stock_quantity=0,
                                                   effective_date=date(2026,9,5))])
        self.session.commit(); self.db.info.clear()
        row=next(r for r in await self.listed() if r['id']==1)
        # 原类型从到货日生效，9/5起切换新品。
        self.assertEqual(D(row['impairment_cny']),D('2.4'))
        self.session.get(Sale,1).sold_at=datetime(2026,9,1)
        self.session.commit();self.db.info.clear()
        row=next(r for r in await self.listed() if r['id']==1)
        self.assertEqual(D(row['impairment_cny']),0)

    async def test_multi_batch_shipment_uses_each_age_and_actual_allocation(self):
        self.costs()
        batch=self.session.get(InventoryBatch,1);batch.remaining_quantity=8
        detail=self.session.scalar(select(SaleCostDetail));detail.quantity=2
        self.session.add(InventoryBatch(id=2,product_id=10,user_id=1,batch_no='SECOND',quantity=3,
            remaining_quantity=1,purchase_price=10,shipping_cost=15,last_mile_cost=9,other_cost=6,
            unit_cost=20,arrived_at=datetime(2026,9,5)))
        self.session.flush()
        self.session.add(SaleCostDetail(sale_id=1,batch_id=2,quantity=2,unit_cost=20))
        self.session.get(Sale,1).total_cost=60
        self.session.commit();self.db.info.clear()
        row=next(r for r in await self.listed() if r['id']==1)
        self.assert_parts(row,(30,14,8,8))
        self.assertEqual(D(row['impairment_cny']),D('1.9')) # .9 + 1

    async def test_shared_safety_stock_uses_other_shops_without_leaking_them(self):
        self.costs(kind='safe_stock',safe=8)
        self.session.add(InventoryBatch(id=2,product_id=10,user_id=None,batch_no='SECRET-BATCH',
            quantity=8,remaining_quantity=8,purchase_price=999,unit_cost=999,arrived_at=datetime(2026,9,1)))
        self.session.add(BatchImpairmentStore(batch_id=1,store_id=8))
        self.session.commit();self.db.info.clear()
        rows=await self.listed(page=1,page_size=1)
        row=rows[0] if rows[0]['id']==1 else (await self.client.get('/sales/1')).json()
        self.assertEqual(D(row['impairment_cny']),D('3.6')) # new layer protects 8; sold batch fully exposed
        self.assertNotIn('SECRET',str(rows))
        self.assertNotIn('S8',str(rows))
        # 本列是实物参考，全部准备不冒记到实际销售店铺。
        self.assertEqual(D(row['profit_cny']),50)

    async def test_missing_fx_does_not_hide_known_inventory_cost_or_impairment(self):
        self.costs()
        entry=self.session.get(SettlementEntry,1)
        entry.currency,entry.exchange_rate_to_cny='USD',None
        self.session.commit();self.db.info.clear()
        row=next(r for r in await self.listed() if r['id']==1)
        self.assert_parts(row,(20,8,4,8))
        self.assertEqual(D(row['impairment_cny']),D('1.8'))
        self.assertIsNone(row['profit_cny'])
        self.assertIsNone(row['sales_revenue_cny'])

    async def test_missing_original_component_weights_do_not_invent_purchase_cost(self):
        self.costs()
        batch=self.session.get(InventoryBatch,1)
        batch.purchase_price=batch.shipping_cost=batch.last_mile_cost=batch.other_cost=0
        self.session.commit();self.db.info.clear()
        row=next(r for r in await self.listed() if r['id']==1)
        self.assertTrue(all(row[field] is None for field in FIELDS))
        self.assertIsNone(row['order_total_cost_cny'])
        self.assertIn('成本构成缺失',row['cost_breakdown_review_reason'])
        self.assertEqual(D(row['total_cost']),40)
        self.assertEqual(D(row['impairment_cny']),D('1.8'))
        book=await self.exported()
        total=list(book['销售记录'].values)[-1]
        self.assertTrue(all(total[i] is None for i in range(18,22)))
        self.assertEqual(total[8],40)

    async def test_missing_inconsistent_voided_and_invalid_history_are_not_zero(self):
        self.costs()
        for scenario in ('missing','inconsistent','voided','before_arrival'):
            with self.subTest(scenario=scenario):
                self.session.rollback()
                batch=self.session.get(InventoryBatch,1)
                batch.voided_at=datetime(2026,10,1) if scenario=='voided' else None
                batch.arrived_at=datetime(2026,9,11) if scenario=='before_arrival' else datetime(2026,9,1)
                detail=self.session.scalar(select(SaleCostDetail))
                detail.quantity=3 if scenario=='missing' else 4
                detail.unit_cost=9 if scenario=='inconsistent' else 10
                self.session.commit();self.db.info.clear()
                row=next(r for r in await self.listed() if r['id']==1)
                self.assertIsNone(row['impairment_cny'])
                self.assertTrue(row['impairment_review_reason'])
                if scenario!='before_arrival':
                    self.assertTrue(all(row[field] is None for field in FIELDS))
                    book=await self.exported()
                    total=list(book['销售记录'].values)[-1]
                    self.assertTrue(all(total[i] is None for i in range(18,23)))
                self.assertEqual(D(row['total_cost']),40)

    async def test_cost_adjustment_and_frozen_basis_affect_components_and_impairment(self):
        self.costs()
        os.environ['FIFO_FINANCIAL_CONTROLS_ENABLED']='1'
        batch=self.session.get(InventoryBatch,1)
        batch.cost_basis={'components':dict(zip(('purchase','head_shipping','last_mile','other'),('5','2','1','2')))}
        batch.quantity=100 # frozen basis must still be used
        self.session.add(InventoryCostAdjustment(id=1,request_key='split',source_ref='test',category='head_shipping',
            allocation_basis='quantity',amount=20,amount_cny=20,currency='CNY',exchange_rate=1,
            document_date=date(2026,9,1),rate_date=date(2026,9,1),is_final=True,created_by=1))
        self.session.flush()
        self.session.add(InventoryCostAdjustmentLine(adjustment_id=1,batch_id=1,quantity=10,allocation_weight=10,amount_cny=20))
        self.session.commit();self.db.info.clear()
        row=next(r for r in await self.listed() if r['id']==1)
        self.assert_parts(row,(20,16,4,8))
        self.assertEqual(D(row['impairment_cny']),D('2.16'))
        self.assertEqual(self.session.get(Sale,1).total_cost,40)
        self.assertEqual(self.session.scalar(select(SaleCostDetail)).unit_cost,10)


class ImportedSalesCostTests(unittest.IsolatedAsyncioTestCase):
    setUp=imports.ImportStoreTests.setUp
    tearDown=imports.ImportStoreTests.tearDown
    request=imports.ImportStoreTests.request
    upload=imports.ImportStoreTests.upload

    async def test_manual_sale_response_includes_cost_parts_and_arrival_day_zero_provision(self):
        batch=self.session.get(InventoryBatch,1)
        batch.purchase_price,batch.shipping_cost,batch.last_mile_cost,batch.other_cost=4,20,10,30
        self.session.commit()
        response=await self.request('POST','/sales',json={'product_id':1,'store_id':7,
            'order_no':'MANUAL-COST','quantity':2,'selling_price':20,'sold_at':'2026-09-01T12:00:00'})
        self.assertEqual(response.status_code,200,response.text)
        row=response.json()
        self.assertEqual([D(row[f]) for f in FIELDS],[D(8),D(4),D(2),D(6)])
        self.assertEqual(D(row['order_total_cost_cny']),20)
        self.assertEqual(D(row['impairment_cny']),0)

    async def test_real_import_generates_multi_batch_combination_costs_and_export(self):
        first=self.session.get(InventoryBatch,1)
        first.quantity=first.remaining_quantity=1
        first.purchase_price,first.shipping_cost,first.last_mile_cost,first.other_cost=4,2,1,3
        second=self.session.get(InventoryBatch,2)
        second.purchase_price,second.shipping_cost,second.last_mile_cost,second.other_cost=4,20,10,30
        mapping=self.session.scalar(select(PlatformSkuMapping));mapping.components[0].quantity_per_sale=2
        self.session.add(Product(id=2,sku='COMPONENT',name='组合第二件',product_type='no_impairment'))
        self.session.flush()
        mapping.components.append(PlatformSkuComponent(product_id=2,quantity_per_sale=1))
        self.session.add(InventoryBatch(id=4,product_id=2,batch_no='COMP',quantity=10,remaining_quantity=10,
            purchase_price=5,unit_cost=5,arrived_at=datetime(2026,9,1)))
        self.session.commit()
        response=await self.request('POST','/sales/import',data={'store_id':'7'},
            files={'file':('orders.csv',imports.order_file('商品*2+组合第二件'),'text/csv')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['sales_created'],2)
        self.db.info.clear()
        response=await self.request('GET','/sales?month=2026-09')
        rows=response.json()
        component=next(r for r in rows if r['product_id']==1)
        self.assertEqual(component['quantity'],2)
        self.assertEqual(len(component['cost_details']),2)
        self.assertEqual([D(component[f]) for f in FIELDS],[D(8),D(4),D(2),D(6)])
        self.assertEqual({r['order_no'] for r in rows},{'OK-PLAT'})
        self.assertEqual(sum(D(r['order_total_cost_cny']) for r in rows),25)
        exported=await self.request('GET','/sales/export?month=2026-09')
        sheet=load_workbook(BytesIO(exported.content),data_only=True).active
        self.assertEqual(sheet['I4'].value,25)
        self.assertEqual(sum(sheet.cell(4,col).value for col in range(19,23)),25)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,0)
        self.assertEqual(self.session.get(InventoryBatch,2).remaining_quantity,9)


if __name__=='__main__': unittest.main()
