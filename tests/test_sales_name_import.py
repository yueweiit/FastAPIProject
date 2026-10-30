"""原始SKU展示、按名称拆单及独立结算补录；只使用隔离SQLite/ASGI。"""
import csv
import io
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace as NS

from openpyxl import load_workbook
from sqlalchemy import select

from models import (Product, InventoryBatch, Sale, SaleCostDetail, SettlementEntry,
                    SettlementEntryAllocation, SalesImportBatch, ProductLine, StoreProduct)
from routers.reports import _product_line_profit_data
from routers.sales import _product_expression_parts, _resolve_product_components
from tests import test_sales_import_store as fixtures


def source_file(rows):
    output=io.StringIO();writer=csv.writer(output)
    writer.writerow(['结算日期','结算单 ID','付款 ID','状态','货币','交易类型','订单ID/调整单ID',
        'SKU ID','数量','订单创建日期','净商品销售额','结算总金额','产品名','商品名称'])
    for index,row in enumerate(rows,1):
        writer.writerow(['2026-09-02',f'B{index}','P','已结算','CNY',row.get('type','订单'),
            row.get('order',f'ORDER-{index}'),row.get('sku','00123456789012345678'),row.get('quantity',2),
            '2026-09-01',row.get('net',200),row.get('total',180),row.get('name','商品*4'),row.get('original','原始商品标题')])
    return output.getvalue().encode('utf-8-sig')


class NameParserTests(unittest.TestCase):
    def test_counts_combinations_and_repeated_components(self):
        self.assertEqual(_product_expression_parts('商品*4+黑色×2＋白色'),[('商品',4),('黑色',2),('白色',1)])
        parts,error=_resolve_product_components({'产品名':'黑色*2+黑色*3'},[NS(id=1,name='黑色')],[],None,strict_names=True)
        self.assertIsNone(error);self.assertEqual(parts[0]['multiplier'],5)

    def test_names_are_exact_after_normalization_and_never_fall_back_to_sku(self):
        products=[NS(id=1,sku='MATCH',name='吸盘逗猫棒（黑色）')]
        parts,error=_resolve_product_components({'产品名':'吸盘逗猫棒(黑色)'},products,[],None,strict_names=True)
        self.assertIsNone(error);self.assertEqual(parts[0]['product'].id,1)
        for value in ('','吸盘逗猫棒','其他*0','黑色+','黑色*-2'):
            parts,error=_resolve_product_components({'产品名':value,'SKU ID':'MATCH'},products,[],None,strict_names=True)
            self.assertIsNone(parts);self.assertTrue(error)
        parts,error=_resolve_product_components({'产品名':'吸盘逗猫棒(黑色)'},
            products+[NS(id=2,sku='OTHER',name='吸盘逗猫棒(黑色)')],[],None,strict_names=True)
        self.assertIsNone(parts);self.assertIn('多个',error)


class NameImportTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.ImportStoreTests.setUp
    tearDown=fixtures.ImportStoreTests.tearDown
    request=fixtures.ImportStoreTests.request

    async def upload(self,rows):
        return await self.request('POST','/sales/import',data={'store_id':'7'},
            files={'file':('source.csv',source_file(rows),'text/csv')})

    def add_bundle_products(self):
        self.session.add_all([Product(id=2,sku='BLACK',name='吸盘逗猫棒（黑色）'),
            Product(id=3,sku='WHITE',name='吸盘逗猫棒（白色）')]);self.session.flush()
        for key,price in ((2,20),(3,30)):
            self.session.add(InventoryBatch(id=key+2,product_id=key,batch_no=f'BUNDLE-{key}',quantity=10,
                remaining_quantity=10,purchase_price=price,unit_cost=price,arrived_at=datetime(2026,9,1)))
        self.session.commit()

    async def test_unmapped_source_sku_uses_name_multiplier_and_is_idempotent(self):
        response=await self.upload([{'name':'商品*4'}]);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((response.json()['sales_created'],response.json()['pending_confirmation']),(1,0))
        sale=self.session.scalar(select(Sale));self.assertEqual(sale.quantity,8)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,2)
        row=(await self.request('GET','/sales?keyword=00123456789012345678')).json()[0]
        self.assertEqual((row['display_sku'],row['display_name'],row['transaction_type']),('00123456789012345678','商品*4','订单'))
        self.assertEqual(row['source_quantity'],2);self.assertEqual(D(row['total_cost']),80)
        response=await self.upload([{'name':'商品*4'}]);self.assertEqual(response.json()['skipped_duplicates'],1)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,2)

    async def test_bundle_component_filters_do_not_duplicate_source_income(self):
        self.add_bundle_products()
        response=await self.upload([{'name':'吸盘逗猫棒(黑色)+吸盘逗猫棒（白色）'}])
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['sales_created'],2)
        rows=(await self.request('GET','/sales?keyword=00123456789012345678')).json()
        self.assertEqual({r['quantity'] for r in rows},{2})
        self.assertEqual(sum(D(r['total_cost']) for r in rows),100)
        self.assertEqual(sum(D(r['sales_revenue_cny']) for r in rows),200)
        black=(await self.request('GET','/sales?product_id=2')).json()[0]
        self.assertEqual(D(black['sales_revenue_cny']),80);self.assertEqual(D(black['platform_fee_cny']),8)
        detail=(await self.request('GET',f"/sales/{black['id']}")).json()
        self.assertEqual(D(detail['sales_revenue_cny']),80)
        summary=(await self.request('GET','/sales/monthly-summary?product_id=2')).json()[0]
        self.assertEqual(D(summary['sales_revenue_cny']),80)
        response=await self.request('GET','/sales/export?product_id=2')
        book=load_workbook(io.BytesIO(response.content),data_only=True)
        values=list(book['销售记录'].values)
        self.assertEqual(values[1][2],'00123456789012345678');self.assertEqual(values[1][27:29],('订单',2))
        self.assertEqual(values[1][7],80);self.assertEqual(values[-1][7],80)
        self.assertIsNone(values[-1][28]);self.assertEqual(list(book['订单汇总'].values)[1][3],100)

    async def test_unknown_name_and_partial_bundle_do_not_fall_back_or_partially_deduct(self):
        for index,name in enumerate(('未知商品','商品+未知商品','商品*0',''),1):
            response=await self.upload([{'name':name,'sku':'PLAT','order':f'UNKNOWN-{index}'}])
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual((response.json()['sales_created'],response.json()['pending_confirmation']),(0,1))
        self.assertEqual(self.session.scalars(select(Sale)).all(),[])
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)

    async def test_zero_revenue_bundle_keeps_saved_fee_shares_when_filtered(self):
        self.add_bundle_products()
        response=await self.upload([{'name':'吸盘逗猫棒（黑色）+吸盘逗猫棒（白色）','net':0,'total':-20}])
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['sales_created'],2)
        black=(await self.request('GET','/sales?product_id=2')).json()[0]
        white=(await self.request('GET','/sales?product_id=3')).json()[0]
        self.assertEqual(D(black['sales_revenue_cny']),0)
        self.assertEqual(D(black['platform_fee_cny']),8);self.assertEqual(D(white['platform_fee_cny']),12)

    async def test_compensation_and_refund_do_not_need_products_or_deduct_stock(self):
        response=await self.upload([{'type':'物流赔付','name':'物流赔付','sku':'/','quantity':'/','net':0,'total':'117.5'},
            {'type':'退款','name':'不存在','sku':'UNMAPPED','quantity':-1,'net':-20,'total':-18}])
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['pending_confirmation'],0)
        rows=(await self.request('GET','/sales')).json();comp=next(r for r in rows if r['transaction_type']=='物流赔付')
        self.assertEqual(D(comp['sales_revenue_cny']),D('117.5'));self.assertEqual(D(comp['platform_fee_cny']),0)
        self.assertEqual(D(comp['profit_cny']),D('117.5'));self.assertEqual(comp['display_sku'],'/')
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)

    async def test_named_refund_keeps_product_filters_export_and_product_line_without_stock_changes(self):
        self.session.add(ProductLine(id=1,code='TEST',name='退款产品线'))
        self.session.flush()
        self.session.add(StoreProduct(store_id=7,product_id=1,product_line_id=1,store_sku='STORE-SKU',
            platform_sku_id='RAW-REFUND',effective_from=date(2026,1,1)))
        self.session.commit()
        response=await self.upload([{'type':'退款','name':'商品*2','sku':'RAW-REFUND',
            'quantity':-2,'net':-40,'total':-36}])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((response.json()['sales_created'],response.json()['pending_confirmation']),(0,0))
        entry=self.session.scalar(select(SettlementEntry))
        allocation=self.session.scalar(select(SettlementEntryAllocation))
        self.assertEqual(entry.product_id,1);self.assertIsNone(entry.sale_id)
        self.assertEqual((allocation.product_id,allocation.quantity,allocation.multiplier),(1,-4,2))
        self.assertIsNone(allocation.sale_id)
        for query in ('product_id=1','keyword=SKU'):
            rows=(await self.request('GET',f'/sales?month=2026-09&{query}')).json()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['display_sku'],'RAW-REFUND')
            self.assertEqual(D(rows[0]['sales_revenue_cny']),-40)
            self.assertEqual(D(rows[0]['profit_cny']),-36)
            self.assertEqual((rows[0]['quantity'],D(rows[0]['total_cost'])),(0,0))
            summary=(await self.request('GET',f'/sales/monthly-summary?month=2026-09&{query}')).json()[0]
            self.assertEqual(D(summary['sales_revenue_cny']),-40)
            self.assertEqual(D(summary['platform_fee_cny']),-4)
        exported=await self.request('GET','/sales/export?product_id=1&month=2026-09')
        self.assertEqual(exported.status_code,200,exported.text if exported.status_code!=200 else '')
        rows=list(load_workbook(io.BytesIO(exported.content),data_only=True)['销售记录'].values)
        self.assertEqual(rows[1][2:5],('RAW-REFUND','商品*2',0))
        self.assertEqual((rows[1][7],rows[-1][7]),(-40,-40))
        lines=await _product_line_profit_data(self.db,{7},date(2026,9,1),date(2026,9,30))
        line=next(row for row in lines if row['name']=='退款产品线')
        self.assertEqual((line['current']['revenue'],line['current']['cost']),(D(-40),D(-4)))
        self.assertFalse(any(row['name']=='未分配产品线（结算调整）' for row in lines))
        self.assertEqual(self.session.scalars(select(Sale)).all(),[])
        self.assertEqual(self.session.scalars(select(SaleCostDetail)).all(),[])
        self.assertEqual([b.remaining_quantity for b in self.session.scalars(select(InventoryBatch))],[10,10,10])
        self.actor=NS(id=2,role='operator',store_id=8)
        self.assertEqual((await self.request('GET','/sales?product_id=1')).json(),[])
        self.actor=NS(id=1,role='admin',store_id=None)
        rollback=await self.request('POST',f"/sales/imports/{response.json()['import_batch_id']}/rollback")
        self.assertEqual(rollback.status_code,200,rollback.text)
        self.assertEqual(rollback.json()['restored_quantity'],0)
        self.assertEqual(self.session.scalars(select(SettlementEntryAllocation)).all(),[])
        self.assertEqual([b.remaining_quantity for b in self.session.scalars(select(InventoryBatch))],[10,10,10])

    async def test_refund_components_are_optional_complete_and_never_create_inventory_sales(self):
        self.add_bundle_products()
        response=await self.upload([
            {'type':'退款','name':'吸盘逗猫棒（黑色）+吸盘逗猫棒（白色）*2','quantity':-2,'net':-50,'total':-45},
            {'type':'退款','name':'商品+不存在','quantity':-1,'net':-10,'total':-9},
            {'type':'退款','name':'','quantity':-1,'net':-10,'total':-9},
            {'type':'订单','name':'商品','quantity':1,'net':-10,'total':-9},
            {'type':'物流赔付','name':'商品','quantity':0,'net':0,'total':5},
        ])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((response.json()['sales_created'],response.json()['pending_confirmation']),(0,0))
        entries=self.session.scalars(select(SettlementEntry).order_by(SettlementEntry.id)).all()
        self.assertEqual([row.product_id for row in entries],[None,None,None,1,None])
        allocations=self.session.scalars(select(SettlementEntryAllocation).order_by(SettlementEntryAllocation.id)).all()
        self.assertEqual([(a.product_id,a.quantity) for a in allocations],[(2,-2),(3,-4),(1,1)])
        self.assertTrue(all(a.sale_id is None for a in allocations))
        self.assertEqual(self.session.scalars(select(Sale)).all(),[])
        self.assertEqual(self.session.scalars(select(SaleCostDetail)).all(),[])
        self.assertTrue(all(b.remaining_quantity==10 for b in self.session.scalars(select(InventoryBatch))))

    async def test_old_direct_source_is_displayed_without_rebuilding_allocations(self):
        original=await self.request('POST','/sales',json={'product_id':1,'store_id':7,'order_no':'OLD',
            'quantity':2,'selling_price':30,'sold_at':'2026-09-01T12:00:00'})
        self.assertEqual(original.status_code,200,original.text)
        sale_id=original.json()['id']
        self.session.add(SettlementEntry(source_key='old-direct',source_row_number=1,order_id='OLD',
            platform_sku_id='00000012345678901234',product_name='旧来源产品名',transaction_type='订单',quantity=1,
            sale_id=sale_id,product_id=1,store_id=7,currency='CNY',settlement_date=datetime(2026,9,1),
            net_product_sales=100,settlement_total=90,raw_data={}))
        self.session.commit();self.db.info.clear()
        row=(await self.request('GET',f'/sales/{sale_id}')).json()
        self.assertEqual(row['display_sku'],'00000012345678901234');self.assertEqual(row['display_name'],'旧来源产品名')
        self.assertEqual(row['source_quantity'],1);self.assertEqual(row['quantity'],2)
        self.assertEqual(self.session.scalars(select(SettlementEntryAllocation)).all(),[])
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,8)
        self.assertEqual(self.session.get(Sale,sale_id).total_cost,20)


class ManualSettlementTests(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.ImportStoreTests.setUp
    tearDown=fixtures.ImportStoreTests.tearDown
    request=fixtures.ImportStoreTests.request
    async def test_manual_is_nonstock_and_idempotent(self):
        payload={'store_id':7,'request_key':'manual-test-key-0001','occurred_on':'2026-09-03','order_no':'MANUAL-1',
            'sku_id':'00123456789012345678','product_name':'商品*4','transaction_type':'订单','quantity':2,
            'net_product_sales':'100.1234','settlement_total':'90.0000'}
        response=await self.request('POST','/sales/manual-settlements',json=payload)
        self.assertEqual(response.status_code,200,response.text);data=response.json()
        self.assertEqual(data['quantity'],0);self.assertEqual(data['source_quantity'],2)
        self.assertEqual(D(data['profit_cny']),90);self.assertEqual(D(data['total_cost']),0)
        self.assertEqual(data['record_type'],'manual_settlement')
        second=await self.request('POST','/sales/manual-settlements',json=payload)
        self.assertEqual(second.json()['id'],data['id'])
        equivalent=await self.request('POST','/sales/manual-settlements',json={**payload,'order_no':' MANUAL-1 ',
            'net_product_sales':'100.1234','settlement_total':'90'})
        self.assertEqual(equivalent.json()['id'],data['id'])
        self.assertEqual(len(self.session.scalars(select(SalesImportBatch)).all()),1)
        conflict=await self.request('POST','/sales/manual-settlements',json={**payload,'settlement_total':'91'})
        self.assertEqual(conflict.status_code,409)
        for model in (Sale,SaleCostDetail,SettlementEntryAllocation):self.assertEqual(self.session.scalars(select(model)).all(),[])
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)

    async def test_manual_invalid_fields_refund_sign_and_store_permissions(self):
        payload={'store_id':7,'request_key':'manual-test-negative','occurred_on':'2026-09-03','order_no':'REFUND',
            'quantity':-1,'net_product_sales':'-100','settlement_total':'-90','transaction_type':'退款'}
        for change in ({'order_no':' '},{'transaction_type':' '},{'quantity':1.5},{'net_product_sales':'NaN'},
                       {'settlement_total':'12345678901234567'},{'occurred_on':'bad-date'}):
            response=await self.request('POST','/sales/manual-settlements',json={**payload,**change})
            self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(self.session.scalars(select(SettlementEntry)).all(),[])
        self.assertEqual(self.session.scalars(select(SalesImportBatch)).all(),[])
        data=(await self.request('POST','/sales/manual-settlements',json=payload)).json()
        self.assertEqual(data['source_quantity'],-1);self.assertEqual(data['quantity'],0)
        self.assertEqual(D(data['profit_cny']),-90);self.assertEqual(D(data['total_cost']),0)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)
        self.assertEqual(len((await self.request('GET','/sales?month=2026-09')).json()),1)
        self.actor=NS(id=2,role='operator',store_id=8)
        self.assertEqual((await self.request('GET','/sales')).json(),[])
        self.assertEqual((await self.request('POST','/sales/manual-settlements',json=payload)).status_code,403)
        self.actor=NS(id=2,role='viewer',store_id=7)
        self.assertEqual((await self.request('POST','/sales/manual-settlements',json=payload)).status_code,403)

    async def test_manual_compensation_and_rollback_keep_inventory_unchanged(self):
        payload={'store_id':7,'request_key':'manual-test-key-0002','occurred_on':'2026-09-03','order_no':'CLAIM',
            'transaction_type':'物流赔付','quantity':0,'net_product_sales':'0','settlement_total':'117.50'}
        data=(await self.request('POST','/sales/manual-settlements',json=payload)).json()
        self.assertEqual(D(data['sales_revenue_cny']),D('117.5'));self.assertEqual(D(data['platform_fee_cny']),0)
        batch=self.session.scalar(select(SalesImportBatch))
        response=await self.request('POST',f'/sales/imports/{batch.id}/rollback')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)
        self.assertEqual((await self.request('GET','/sales')).json(),[])

    async def test_unique_key_race_rolls_back_only_the_new_attempt(self):
        payload={'store_id':7,'request_key':'manual-test-key-race','occurred_on':'2026-09-03','order_no':'RACE',
            'net_product_sales':'100','settlement_total':'90'}
        first=await self.request('POST','/sales/manual-settlements',json=payload)
        self.assertEqual(first.status_code,200,first.text)
        original=self.db.execute;missed=False
        async def race(statement):
            nonlocal missed
            if not missed and 'WHERE settlement_entries.source_key =' in str(statement):
                missed=True
                return NS(scalar_one_or_none=lambda:None)
            return await original(statement)
        self.db.execute=race
        retry=await self.request('POST','/sales/manual-settlements',json=payload)
        self.assertEqual(retry.status_code,200,retry.text)
        self.assertEqual(retry.json()['id'],first.json()['id'])
        self.assertEqual(len(self.session.scalars(select(SalesImportBatch)).all()),1)
        self.assertEqual(len(self.session.scalars(select(SettlementEntry)).all()),1)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)
