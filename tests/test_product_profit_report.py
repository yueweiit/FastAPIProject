"""保留产品线名称的逐商品毛利：隔离SQLite，仅核验读取，不启动main。"""
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from openpyxl import Workbook, load_workbook
from sqlalchemy import event, select

from models import (Product, ProductLine, StoreProduct, Sale, SaleCostDetail, InventoryBatch,
                    SettlementEntryAllocation)
from routers.reports import (_product_line_profit_data, product_line_profit_report,
                             _build_product_line_profit_sheet, PRODUCT_LINE_PROFIT_SHEET)
from tests import test_financial_facts as facts


class ProductProfitReportTests(unittest.IsolatedAsyncioTestCase):
    entry = facts.FinancialFactsTests.entry
    tearDown = facts.FinancialFactsTests.tearDown

    def setUp(self):
        facts.FinancialFactsTests.setUp(self)
        async def get(model, key): return self.session.get(model, key)
        self.db.get = get
        for target, value in (
            ('routers.reports._load_inventory_provision_data', ([], [], None)),
            ('routers.reports._sale_provision_adjustments', {key: (D(0), '') for key in range(1, 50)}),
        ):
            mocked = patch(target, new=AsyncMock(return_value=value))
            mocked.start()
            self.addCleanup(mocked.stop)

    def add_sale(self, key, product, *, store=7, quantity=2, revenue=60, day=None):
        self.session.add(InventoryBatch(id=100+key, product_id=product, batch_no=f'B{key}',
            quantity=100, remaining_quantity=100-quantity, unit_cost=10, purchase_price=10,
            user_id=1, arrived_at=datetime(2026, 1, 1)))
        self.session.flush()
        self.session.add(Sale(id=key, product_id=product, user_id=1, store_id=store,
            order_no=f'O{key}', quantity=quantity, selling_price=D(revenue)/quantity,
            total_cost=10*quantity, platform_fee=0, profit=revenue-10*quantity,
            sold_at=day or datetime(2026, 9, 15)))
        self.session.flush()
        self.session.add(SaleCostDetail(id=key, sale_id=key, batch_id=100+key,
            quantity=quantity, unit_cost=10))

    async def data(self, stores=None, month=date(2026, 9, 1)):
        return await _product_line_profit_data(self.db, stores or {7}, month, date(2026, 9, 30))

    async def test_products_stay_separate_without_mapping_and_ignore_conflicting_old_lines(self):
        self.session.add_all([Product(id=11, sku='P11', name='测试商品'),
            ProductLine(id=1, code='A', name='旧分类A'), ProductLine(id=2, code='B', name='旧分类B')])
        self.session.flush()
        self.session.add_all([StoreProduct(store_id=7, product_id=10, product_line_id=index,
            store_sku=f'OLD-{index}', effective_from=date(2026,1,1)) for index in (1,2)])
        self.add_sale(2,11)
        self.add_sale(3,10,quantity=3,revenue=90)
        self.session.commit()
        statements=[]
        def capture(_conn,_cursor,sql,*_args): statements.append(sql)
        event.listen(self.engine,'before_cursor_execute',capture)
        self.addCleanup(lambda:event.remove(self.engine,'before_cursor_execute',capture))
        snapshot=[(b.id,b.remaining_quantity) for b in self.session.scalars(select(InventoryBatch))]
        rows={row['product_id']:row for row in await self.data() if row['row_type']=='product'}
        self.assertEqual(set(rows),{10,11})
        self.assertEqual(rows[10]['name'],'P10 · 测试商品')
        self.assertEqual(rows[11]['name'],'P11 · 测试商品')
        self.assertEqual(rows[10]['current_quantity'],7)
        self.assertEqual(rows[11]['current_quantity'],2)
        self.assertEqual(rows[10]['sku_count'],1)
        self.assertEqual(rows[10]['current']['revenue'],D(170))  # 100+90-20退款
        self.assertEqual(rows[11]['current']['revenue'],D(60))
        # 公共销售上下文仍保留历史店铺映射兜底；本报表不读取产品线来决定商品分组。
        self.assertFalse(any('product_lines' in sql for sql in statements))
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in statements))
        self.assertEqual(snapshot,[(b.id,b.remaining_quantity) for b in self.session.scalars(select(InventoryBatch))])
        self.assertEqual(self.session.get(Sale,1).total_cost,D(40))

    async def test_bundle_uses_saved_component_shares_and_counts_inventory_units(self):
        self.session.add(Product(id=11,sku='PART',name='组合组件'));self.session.flush()
        self.add_sale(2,10,quantity=4,revenue=0)
        self.add_sale(3,11,quantity=2,revenue=0)
        entry=self.entry(6,net=120,total=100,quantity=2,product=None)
        self.session.flush()
        self.session.add_all([
            SettlementEntryAllocation(settlement_entry_id=entry.id,product_id=10,sale_id=2,
                component_name='测试商品',multiplier=2,quantity=4),
            SettlementEntryAllocation(settlement_entry_id=entry.id,product_id=11,sale_id=3,
                component_name='组合组件',multiplier=1,quantity=2)])
        self.session.commit()
        from routers.sales import _sale_import_context
        sales=[self.session.get(Sale,key) for key in (2,3)]
        context,_=await _sale_import_context(self.db,sales)
        rows={row['product_id']:row for row in await self.data()}
        self.assertEqual(rows[10]['current_quantity'],8)
        self.assertEqual(rows[11]['current_quantity'],2)
        self.assertEqual(rows[10]['current']['revenue'],D(80)+context[2]['source_net_product_sales'])
        self.assertEqual(rows[11]['current']['revenue'],context[3]['source_net_product_sales'])
        self.assertEqual(sum(row['current']['revenue'] for row in rows.values()),D(205))

    async def test_unlinked_compensation_is_separate_and_has_no_sale_quantity(self):
        rows=await self.data()
        adjustment=next(row for row in rows if row['row_type']=='unallocated_settlement')
        product=next(row for row in rows if row['product_id']==10)
        self.assertEqual(adjustment['current_quantity'],0)
        self.assertEqual(adjustment['current']['revenue'],D(5))
        self.assertEqual(adjustment['current']['cost'],D(5)/D(85)*D(4))  # 沿用原店铺收入比例费用分摊
        self.assertEqual(adjustment['sku_count'],0)
        self.assertEqual(product['current_quantity'],4)  # 退款不恢复或反扣销售件数
        self.assertEqual(sum(row['current']['revenue'] for row in rows),D(85))

    async def test_api_store_scope_empty_results_and_excel_match_without_changing_title(self):
        self.session.add(Product(id=11,sku='OTHER',name='另一店商品'));self.session.flush()
        self.add_sale(2,11,store=8,revenue=600);self.session.commit()
        async def api(user,store=None,month=date(2026,9,1)):
            return await product_line_profit_report(month,date(2026,9,30),store,self.db,user)
        rows=await api(NS(role='viewer',store_id=7))
        self.assertFalse(any(row['product_id']==11 for row in rows))
        self.assertTrue(all(row['group_by']=='product' for row in rows))
        admin=await api(NS(role='admin',store_id=None))
        self.assertTrue(any(row['product_id']==11 for row in admin))
        with self.assertRaises(HTTPException) as error: await api(NS(role='operator',store_id=7),8)
        self.assertEqual(error.exception.status_code,403)
        with self.assertRaises(HTTPException): await api(NS(role='viewer',store_id=None))
        self.assertEqual(await api(NS(role='admin',store_id=None),month=date(2025,9,1)),[])
        data=await self.data()
        book=Workbook();_build_product_line_profit_sheet(book,data,date(2026,9,1))
        saved=BytesIO();book.save(saved);saved.seek(0)
        sheet=load_workbook(saved,data_only=True)[PRODUCT_LINE_PROFIT_SHEET]
        self.assertEqual(sheet['A1'].value,'商品编码 / 名称')
        self.assertEqual(sheet['B1'].value,'本月出库数量')
        exported={row[0].value:row for row in sheet.iter_rows(min_row=2,max_row=1+len(rows))}
        for row in rows:
            self.assertEqual(exported[row['name']][1].value,row['current_quantity'])
            self.assertAlmostEqual(exported[row['name']][4].value,float(row['current_gross']))

    async def test_unknown_cost_only_masks_affected_product_and_excel_totals(self):
        self.session.add(Product(id=11,sku='HEALTHY',name='正常商品'));self.session.flush()
        self.add_sale(2,11);self.session.commit()
        with patch('routers.reports._sale_provision_adjustments',new=AsyncMock(
                return_value={1:(None,'历史待核对'),2:(D(0),'')})):
            rows=await self.data()
        by_product={row['product_id']:row for row in rows}
        self.assertIsNone(by_product[10]['current']['cost'])
        self.assertIsNotNone(by_product[11]['current']['cost'])
        self.assertIn('历史待核对',by_product[10]['note'])
        book=Workbook();_build_product_line_profit_sheet(book,rows,date(2026,9,1))
        total=next(row for row in book[PRODUCT_LINE_PROFIT_SHEET] if row[0].value=='合计')
        self.assertIsNone(total[3].value);self.assertIsNone(total[4].value)

    async def test_product_names_are_literal_excel_text(self):
        product=self.session.get(Product,10);product.sku='=SUM(1,2)';product.name='<script>商品</script>'
        self.session.commit()
        rows=await self.data();book=Workbook();_build_product_line_profit_sheet(book,rows,date(2026,9,1))
        saved=BytesIO();book.save(saved);saved.seek(0)
        sheet=load_workbook(saved,data_only=False)[PRODUCT_LINE_PROFIT_SHEET]
        self.assertEqual(sheet['A2'].value,'=SUM(1,2) · <script>商品</script>')
        self.assertEqual(sheet['A2'].data_type,'s')


if __name__=='__main__':unittest.main()
