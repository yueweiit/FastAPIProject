"""规格识别、销售展示和Excel一致性；隔离SQLite/ASGI，不连接业务库。"""
import csv
import io
import unittest
from datetime import datetime
from types import SimpleNamespace

from openpyxl import Workbook, load_workbook
from sqlalchemy import select

from models import InventoryBatch, Product, Sale, SaleCostDetail, SettlementEntry
from routers.sales import _resolve_product_components
from tests import test_sales_import_store as fixtures
from tests import test_sales_name_import as names


class SalesSpecificationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ImportStoreTests.setUp
    tearDown = fixtures.ImportStoreTests.tearDown
    request = fixtures.ImportStoreTests.request
    upload = names.NameImportTests.upload

    def variants(self):
        pink = self.session.get(Product, 1)
        pink.name, pink.specification = '垃圾袋', '粉色 / 厚度1.2丝'
        self.session.add(Product(id=2, sku='PURPLE', name='垃圾袋', specification='紫色 / 厚度1.2丝'))
        self.session.flush()
        self.session.add(InventoryBatch(id=4, product_id=2, batch_no='PURPLE-B', quantity=10,
            remaining_quantity=10, purchase_price=10, unit_cost=10, arrived_at=datetime(2026, 9, 1)))
        self.session.commit()

    async def upload_xlsx(self, rows, headers=('规格/型号', 'SKU 名称')):
        content = names.source_file(rows, headers).decode('utf-8-sig')
        book = Workbook()
        sheet = book.active
        sheet.title = '订单详情'
        for values in csv.reader(io.StringIO(content)):
            sheet.append(values)
            for cell in sheet[sheet.max_row]:
                cell.data_type = 's'
        output = io.BytesIO()
        book.save(output)
        return await self.request('POST', '/sales/import', data={'store_id': '7'},
            files={'file': ('spec.xlsx', output.getvalue(),
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})

    async def test_name_and_specification_match_without_guessing_platform_variant(self):
        self.variants()
        rows = [
            {'order':'PINK','name':'垃圾袋*2','quantity':1,'规格/型号':'粉色 / 厚度１.２丝','SKU 名称':'Rosa'},
            {'order':'PURPLE','name':'垃圾袋','quantity':1,'规格/型号':'紫色 / 厚度1.2丝','SKU 名称':'Morado'},
            {'order':'NO-SPEC','name':'垃圾袋','quantity':1,'SKU 名称':'粉色 / 厚度1.2丝'},
            {'order':'WRONG-SPEC','name':'垃圾袋','quantity':1,'规格/型号':'绿色'},
        ]
        headers = ('规格/型号','SKU 名称')
        imported = await self.upload(rows, extra_headers=headers)
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual((imported.json()['sales_created'], imported.json()['pending_confirmation']), (2,2))
        sales = self.session.scalars(select(Sale).order_by(Sale.id)).all()
        self.assertEqual([(s.product_id,s.quantity) for s in sales], [(1,2),(2,1)])
        self.assertEqual([self.session.get(InventoryBatch,i).remaining_quantity for i in (1,4)], [8,9])
        pending = (await self.request('GET','/sales/pending-confirmations')).json()['items']
        self.assertEqual({r['requested_local_specification'] for r in pending}, {None,'绿色'})
        repeated = await self.upload(rows, extra_headers=headers)
        self.assertEqual(repeated.json()['skipped_duplicates'], 4)
        self.assertEqual(len(self.session.scalars(select(SaleCostDetail)).all()), 2)

    async def test_list_detail_and_both_exports_keep_source_and_local_specs_separate(self):
        self.variants()
        imported = await self.upload([{'order':'SPEC','name':'垃圾袋*2','quantity':1,
            '规格/型号':'粉色 / 厚度1.2丝','SKU 名称':'Rosa (2 unidades)'}], extra_headers=('规格/型号','SKU 名称'))
        self.assertEqual(imported.status_code, 200, imported.text)
        entry = self.session.scalar(select(SettlementEntry))
        source_key = entry.source_key
        row = (await self.request('GET','/sales')).json()[0]
        expected = ('SKU','垃圾袋','粉色 / 厚度1.2丝','Rosa (2 unidades)')
        keys = ('local_sku','local_product_name','local_specification','source_specification')
        self.assertEqual(tuple(row[k] for k in keys), expected)
        detail = (await self.request('GET',f"/sales/{row['id']}")).json()
        self.assertEqual(tuple(detail[k] for k in keys), expected)
        for url in ('/sales/export',f"/sales/imports/{imported.json()['import_batch_id']}/export"):
            exported = await self.request('GET',url)
            self.assertEqual(exported.status_code,200)
            book = load_workbook(io.BytesIO(exported.content),data_only=True)
            for sheet in ('原始结算对账','销售拆分明细'):
                values=list(book[sheet].values); record=dict(zip(values[0],values[1]))
                self.assertEqual(record['来源规格（SKU 名称）'],'Rosa (2 unidades)')
                self.assertEqual(record['导入指定规格/型号'],'粉色 / 厚度1.2丝')
                if sheet=='销售拆分明细':self.assertEqual(record['本地商品规格/型号'],'粉色 / 厚度1.2丝')
        self.assertEqual(entry.source_key,source_key)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,8)
        self.actor=SimpleNamespace(id=2,role='viewer',store_id=8)
        self.assertEqual((await self.request('GET','/sales')).json(),[])
        self.assertEqual((await self.request('GET',f"/sales/{row['id']}")).status_code,403)

    async def test_old_data_stays_nullable_and_refunds_never_deduct_stock(self):
        imported=await self.upload([{'name':'商品','quantity':1,'SKU 名称':'1 Unidad'},
            {'order':'REFUND','name':'商品','type':'退款','quantity':-1,'net':-20,'total':-18,'SKU 名称':'1 Unidad'}],extra_headers=('SKU 名称',))
        self.assertEqual(imported.status_code,200,imported.text)
        rows=(await self.request('GET','/sales')).json()
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertIsNone(row['local_specification'])
            self.assertEqual(row['local_sku'],'SKU')
            self.assertEqual(row['source_specification'],'1 Unidad')
        self.assertEqual(sum(row['quantity'] for row in rows),1)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,9)

    async def test_pending_retry_uses_original_spec_and_remains_idempotent(self):
        await self.upload([{'name':'商品','quantity':2,'规格/型号':'XL','SKU 名称':'Grande'}],extra_headers=('规格/型号','SKU 名称'))
        entry=self.session.scalar(select(SettlementEntry));self.assertEqual(entry.mapping_status,'pending_confirmation')
        raw=dict(entry.raw_data);key=entry.source_key
        self.session.get(Product,1).specification='XL';self.session.commit()
        mapped=await self.request('POST','/sales/product-mappings',json={'store_id':7,'key_kind':'platform_sku',
            'key_value':entry.platform_sku_id,'effective_from':'2026-09-01','components':[{'product_id':1,'multiplier':1}]})
        self.assertEqual(mapped.status_code,201,mapped.text)
        for expected in ('confirmed_rows','skipped_rows'):
            response=await self.request('POST','/sales/pending-confirmations/retry',json={'entry_ids':[entry.id]})
            self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()[expected],1)
        self.assertEqual(entry.source_key,key)
        self.assertEqual({k:v for k,v in entry.raw_data.items() if not k.startswith('_pending') and k!='_product_resolution'},raw)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,8)
        self.assertEqual(len(self.session.scalars(select(Sale)).all()),1)

    async def test_xlsx_same_name_same_spec_is_still_ambiguous_and_text_is_literal(self):
        self.variants()
        for p in self.session.scalars(select(Product)):p.specification='=1+1'
        self.session.commit()
        imported=await self.upload_xlsx([{'name':'垃圾袋','规格/型号':'=1+1','SKU 名称':'=Rosa'}])
        self.assertEqual(imported.status_code,200,imported.text)
        self.assertEqual(imported.json()['pending_confirmation'],1)
        self.assertEqual(self.session.scalars(select(Sale)).all(),[])
        exported=await self.request('GET',f"/sales/imports/{imported.json()['import_batch_id']}/export")
        sheet=load_workbook(io.BytesIO(exported.content),data_only=False).active
        headers=[c.value for c in sheet[1]]
        cell=sheet.cell(2,headers.index('导入指定规格/型号')+1)
        self.assertEqual((cell.value,cell.data_type),('=1+1','s'))

    async def test_xlsx_local_code_and_spec_select_variant_without_changing_long_order_number(self):
        self.variants()
        rows = [{'order': '00586306814641538373', 'sku': '1736250585985025154',
            'name': '垃圾袋', 'quantity': 2, '规格/型号': '紫色 / 厚度1.2丝', 'SKU 名称': 'Morado', '本地SKU':'PURPLE'}]
        imported = await self.upload_xlsx(rows, headers=('规格/型号','SKU 名称','本地SKU'))
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['sales_created'], 1)
        sale = self.session.scalar(select(Sale))
        self.assertEqual((sale.product_id, sale.quantity), (2, 2))
        entry = self.session.scalar(select(SettlementEntry))
        self.assertEqual(entry.order_id, rows[0]['order'])
        self.assertEqual(entry.platform_sku_id, rows[0]['sku'])
        repeated = await self.upload_xlsx(rows, headers=('规格/型号','SKU 名称','本地SKU'))
        self.assertEqual(repeated.json()['skipped_duplicates'], 1)
        self.assertEqual([self.session.get(InventoryBatch, i).remaining_quantity for i in (1, 4)], [10, 8])

    async def test_saved_components_expose_distinct_specs_without_partial_stock_deduction(self):
        self.variants()
        await self.upload([{'name': '垃圾袋', 'quantity': 2, 'SKU 名称': 'Set'}], extra_headers=('SKU 名称',))
        entry = self.session.scalar(select(SettlementEntry))
        response = await self.request('POST', '/sales/pending-confirmations/retry', json={
            'entry_ids': [entry.id], 'components': [
                {'product_id': 1, 'multiplier': 16}, {'product_id': 2, 'multiplier': 1}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['pending_rows'], 1)
        pending = (await self.request('GET', '/sales/pending-confirmations')).json()['items'][0]
        self.assertEqual(pending['confirmed_components'], [
            {'product_id': 1, 'multiplier': 16, 'sku': 'SKU', 'name': '垃圾袋', 'specification': '粉色 / 厚度1.2丝'},
            {'product_id': 2, 'multiplier': 1, 'sku': 'PURPLE', 'name': '垃圾袋', 'specification': '紫色 / 厚度1.2丝'}])
        self.assertEqual([self.session.get(InventoryBatch, i).remaining_quantity for i in (1, 4)], [10, 10])
        self.assertEqual(self.session.scalars(select(Sale)).all(), [])

    async def test_historical_invalid_spec_characters_export_visibly_without_modifying_source(self):
        imported = await self.upload([{'name': '商品', 'quantity': 1}], extra_headers=())
        entry = self.session.scalar(select(SettlementEntry))
        self.session.get(Product, 1).specification = '历史\x01规格'
        entry.sku_name = '平台\x02规格'
        entry.raw_data = {**entry.raw_data, '规格/型号': '指定\x03规格'}
        self.session.commit()
        exported = await self.request('GET', f"/sales/imports/{imported.json()['import_batch_id']}/export")
        self.assertEqual(exported.status_code, 200)
        sheet = load_workbook(io.BytesIO(exported.content))['销售拆分明细']
        values = list(sheet.values)
        row = dict(zip(values[0], values[1]))
        self.assertEqual(row['本地商品规格/型号'], '历史[U+0001]规格')
        self.assertEqual(row['来源规格（SKU 名称）'], '平台[U+0002]规格')
        self.assertEqual(row['导入指定规格/型号'], '指定[U+0003]规格')
        pending = await self.upload([{'order': 'INVALID', 'name': '未知商品', '规格/型号': '异常\x01规格'}],
            extra_headers=('规格/型号',))
        pending_export = await self.request('GET', '/sales/pending-confirmations/export')
        self.assertEqual(pending_export.status_code, 200)
        sheet = load_workbook(io.BytesIO(pending_export.content)).active
        values = list(sheet.values)
        row = dict(zip(values[0], values[1]))
        self.assertEqual(row['导入指定规格/型号'], '异常[U+0001]规格')
        self.assertIn('异常[U+0001]规格', row['待确认原因'])
        for url in ('/sales/export', f"/sales/imports/{pending.json()['import_batch_id']}/export"):
            exported = await self.request('GET', url)
            self.assertEqual(exported.status_code, 200)
            book = load_workbook(io.BytesIO(exported.content))
            text = '\n'.join(str(value) for row in book.active.values for value in row)
            self.assertIn('异常[U+0001]规格', text)
        self.assertEqual(entry.raw_data['规格/型号'], '指定\x03规格')
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 9)


class SpecificationParserTests(unittest.TestCase):
    def test_explicit_spec_never_falls_back_to_a_matching_sku_or_partial_name(self):
        products=[SimpleNamespace(id=1,sku='SAME',name='垃圾袋',specification='粉色')]
        for row in ({'产品名':'垃圾袋','规格/型号':'紫色','SKU ID':'SAME'},
                    {'产品名':'垃圾','规格/型号':'粉色','SKU ID':'SAME'}):
            parts,error=_resolve_product_components(row,products,[],None)
            self.assertIsNone(parts);self.assertIn('未找到',error)

    def test_combination_never_applies_one_spec_to_multiple_products(self):
        products=[SimpleNamespace(id=1,name='A',specification='red'),SimpleNamespace(id=2,name='B',specification='red')]
        parts,error=_resolve_product_components({'产品名':'A+B','规格/型号':'red'},products,[],None,strict_names=True)
        self.assertIsNone(parts);self.assertIn('逐项确认',error)
        parts,error=_resolve_product_components({'产品名':'A+B'},products,[],None,strict_names=True)
        self.assertIsNone(error);self.assertEqual([p['product'].id for p in parts],[1,2])
