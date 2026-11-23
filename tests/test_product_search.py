"""商品联想精确SKU在分页前置顶；隔离SQLite/ASGI，不运行应用生命周期。"""
import unittest
from types import SimpleNamespace

from sqlalchemy import event, select

from models import InventoryBatch, Product
from routers import products
from tests import test_sales_import_store as fixtures


class ProductSearchTests(unittest.IsolatedAsyncioTestCase):
    request = fixtures.ImportStoreTests.request

    def setUp(self):
        fixtures.ImportStoreTests.setUp(self)
        self.app.include_router(products.router)
        self.session.add_all([
            Product(id=100+i, sku=f'SKU-{i:04}', name=f'Candidate {i}') for i in range(25)
        ])
        self.session.commit()
        self.operations=[]
        event.listen(self.engine,'before_cursor_execute',self.record_query)

    tearDown = fixtures.ImportStoreTests.tearDown

    def record_query(self,_conn,_cursor,statement,_params,_context,_many):
        self.operations.append(statement.strip().split()[0].upper())

    async def test_exact_sku_is_first_before_limit_and_all_pages_remain_complete(self):
        params={'keyword':' sku ','page':1,'page_size':20,'prioritize_exact_sku':'true'}
        response=await self.request('GET','/products',params=params)
        self.assertEqual(response.status_code,200,response.text)
        first=response.json()
        self.assertEqual((first['total'],len(first['items']),first['items'][0]['id']),(26,20,1))
        self.assertEqual([row['id'] for row in first['items'][1:]],list(range(124,105,-1)))
        second=(await self.request('GET','/products',params={**params,'page':2})).json()
        self.assertEqual((second['total'],len(second['items'])),(26,6))
        ids=[row['id'] for row in first['items']+second['items']]
        self.assertEqual(len(set(ids)),26);self.assertEqual(ids.count(1),1)
        self.assertEqual(set(ids),{1,*range(100,125)})

    async def test_default_false_internal_caller_and_array_contract_stay_compatible(self):
        base={'keyword':'SKU','page':1,'page_size':20}
        for params in (base,{**base,'prioritize_exact_sku':'false'}):
            data=(await self.request('GET','/products',params=params)).json()
            self.assertEqual([row['id'] for row in data['items']],list(range(124,104,-1)))
        # 库存路由直接调用list_products，省略新参数不能把Query描述对象当成True。
        internal=await products.list_products(keyword='SKU',page=1,page_size=20,db=self.db,user=self.actor)
        self.assertEqual([row.id for row in internal.items],list(range(124,104,-1)))
        array=(await self.request('GET','/products',params={'keyword':'SKU','prioritize_exact_sku':'true'})).json()
        self.assertIsInstance(array,list);self.assertEqual((len(array),array[0]['id']),(26,1))
        for keyword,total in (('',26),('Candidate',25),('not-found',0)):
            data=(await self.request('GET','/products',params={**base,'keyword':keyword,'prioritize_exact_sku':'true'})).json()
            self.assertEqual(data['total'],total)
            self.assertEqual([row['id'] for row in data['items']],list(range(124,104,-1)) if total else [])

    async def test_exact_sku_uses_literal_equality_for_percent_and_underscore(self):
        self.session.get(Product,1).sku='RAW_%'
        self.session.add_all([Product(id=200+i,sku=f'RAW_A{i}',name=f'Other {i}') for i in range(25)])
        self.session.commit()
        data=(await self.request('GET','/products',params={
            'keyword':'raw_%','page':1,'page_size':20,'prioritize_exact_sku':'true'})).json()
        self.assertEqual(data['total'],26)
        self.assertEqual((data['items'][0]['id'],data['items'][0]['sku']),(1,'RAW_%'))
        self.assertEqual([row['id'] for row in data['items'][1:]],list(range(224,205,-1)))

    async def test_shared_role_permissions_bounded_pages_and_reads_only(self):
        for role,store_id in (('admin',None),('operator',7),('viewer',8)):
            self.actor=SimpleNamespace(id=1,role=role,store_id=store_id)
            response=await self.request('GET','/products',params={
                'keyword':'SKU','page':1,'page_size':20,'prioritize_exact_sku':'true'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual((response.json()['items'][0]['id'],len(response.json()['items'])),(1,20))
        self.assertEqual((await self.request('GET','/products?prioritize_exact_sku=invalid')).status_code,422)
        self.assertEqual((await self.request('GET','/products?page=1&page_size=101&prioritize_exact_sku=true')).status_code,422)
        self.assertEqual([row.remaining_quantity for row in self.session.scalars(select(InventoryBatch))],[10,10,10])
        self.assertTrue(self.operations)
        self.assertEqual(set(self.operations),{'SELECT'})

    async def test_catalog_specification_search_is_opt_in_for_existing_callers(self):
        for product in self.session.scalars(select(Product)):
            product.specification = "黑色 / XL"
        self.session.commit()
        original = await self.request('GET', '/products', params={'keyword': 'XL', 'page': 1})
        self.assertEqual(original.json()['total'], 0)
        params = {'keyword': 'XL', 'search_specification': 'true', 'page': 1, 'page_size': 10}
        first = (await self.request('GET', '/products', params=params)).json()
        second = (await self.request('GET', '/products', params={**params, 'page': 2})).json()
        self.assertEqual((first['total'], second['total']), (26, 26))
        self.assertEqual(len({row['id'] for row in first['items'] + second['items']}), 20)
        internal = await products.list_products(keyword='XL', page=1, page_size=20, db=self.db, user=self.actor)
        self.assertEqual(internal.total, 0)

    async def test_completeness_filters_count_and_paginate_without_changing_legacy_rows(self):
        for product in self.session.scalars(select(Product)):
            product.specification = "完整规格"
            product.image = "/uploads/fixture.png"
        self.session.get(Product, 100).specification = None
        self.session.get(Product, 101).specification = "  "
        self.session.get(Product, 102).specification = ""
        self.session.get(Product, 101).image = None
        self.session.get(Product, 102).image = ""
        self.session.commit()
        self.operations.clear()
        for completeness, ids in (('missing_specification', [102, 101, 100]), ('missing_image', [102, 101])):
            params = {'completeness': completeness, 'page': 1, 'page_size': 2}
            first = (await self.request('GET', '/products', params=params)).json()
            self.assertEqual(first['total'], len(ids))
            self.assertEqual([row['id'] for row in first['items']], ids[:2])
            array = (await self.request('GET', '/products', params={'completeness': completeness})).json()
            self.assertEqual([row['id'] for row in array], ids)
        result = (await self.request('GET', '/products', params={
            'completeness': 'missing_specification', 'keyword': 'Candidate 0', 'page': 1,
        })).json()
        self.assertEqual([row['id'] for row in result['items']], [100])
        self.assertEqual(self.session.get(Product, 100).specification, None)
        self.assertEqual(self.session.get(Product, 101).specification, '  ')
        self.assertEqual(set(self.operations), {'SELECT'})

    async def test_new_filter_validation_and_export_scope_remain_explicit(self):
        for params in ({'completeness': 'unknown'}, {'search_specification': 'invalid'}):
            response = await self.request('GET', '/products', params=params)
            self.assertEqual(response.status_code, 422)
        # 导出继续按product_ids或全部资料，不因页面筛选隐式改变原格式/范围。
        from io import BytesIO
        from openpyxl import load_workbook
        response = await self.request('GET', '/products/export', params={'product_ids': [1, 100]})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        workbook = load_workbook(BytesIO(response.content))
        sheet = workbook.active
        self.assertIn('规格/型号', [cell.value for cell in sheet[1]])
        self.assertEqual(sheet.max_row, 3)


if __name__=='__main__':
    unittest.main()
