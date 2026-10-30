"""销售导出：真实ASGI/隔离SQLite与Excel，不启动应用或连接业务库。"""
import os
import unittest
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from openpyxl import load_workbook
from sqlalchemy import event, select

from auth import get_current_user
from database import get_db
from models import (InventoryBatch, InventoryCostAdjustment, InventoryCostAdjustmentLine,
                    Product, Sale, SaleCostDetail, Store)
from routers import sales
from tests import test_financial_facts as facts
from tests.test_sales_import_store import Facade


class SalesExportTests(unittest.IsolatedAsyncioTestCase):
    entry = facts.FinancialFactsTests.entry

    def setUp(self):
        facts.FinancialFactsTests.setUp(self)
        self.flag = patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "0"})
        self.flag.start()
        self.actor = SimpleNamespace(id=1, role="operator", store_id=7)
        self.db = Facade(self.session)
        app = FastAPI()
        app.include_router(sales.router)
        async def user(): return self.actor
        async def db():
            try: yield self.db
            finally: self.session.rollback()
        app.dependency_overrides[get_current_user] = user
        app.dependency_overrides[get_db] = db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://unit.invalid")
        self.queries = []
        event.listen(self.engine, "before_cursor_execute", self.record_query)

    def record_query(self, _conn, _cursor, statement, _params, _context, _many):
        self.queries.append(statement.strip().split()[0].upper())

    async def asyncTearDown(self):
        await self.client.aclose()
        self.flag.stop()
        facts.FinancialFactsTests.tearDown(self)

    def add_sale(self, key, *, store=7, product=10, month=9, batch=1):
        self.session.add(Sale(id=key, product_id=product, user_id=1, store_id=store,
            order_no=f"SALE-{key}", quantity=1, selling_price=20, total_cost=10,
            platform_fee=0, profit=10, sold_at=datetime(2026, month, 11)))
        self.session.flush()
        self.session.add(SaleCostDetail(sale_id=key, batch_id=batch, quantity=1, unit_cost=10))
        self.session.get(InventoryBatch, batch).remaining_quantity -= 1

    async def exported(self, **params):
        response = await self.client.get("/sales/export", params={"month": "2026-09", **params})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        self.assertIn("spreadsheetml.sheet", response.headers["content-type"])
        self.assertIn("sales_202609.xlsx", response.headers["content-disposition"])
        return load_workbook(BytesIO(response.content), data_only=True)

    def rows(self, workbook):
        return list(workbook["销售记录"].iter_rows(min_row=2, max_row=workbook["销售记录"].max_row-1, values_only=True))

    async def test_all_matching_records_and_events_export_beyond_current_page(self):
        batch = self.session.get(InventoryBatch, 1)
        batch.quantity += 24
        batch.remaining_quantity += 24
        for key in range(2, 26): self.add_sale(key)
        self.session.commit()
        self.queries.clear()
        page = (await self.client.get("/sales", params={"month": "2026-09", "page": 2, "page_size": 1})).json()
        self.assertEqual(len(page["items"]), 1)
        workbook = await self.exported(page=2, page_size=1)
        rows = self.rows(workbook)
        self.assertEqual(len(rows), page["total"])
        self.assertEqual(len(rows), 27)
        self.assertEqual(sum(row[4] for row in rows), 28)
        self.assertEqual(sum(row[12] != "销售" for row in rows), 2)
        summary = (await self.client.get("/sales/monthly-summary", params={"month": "2026-09"})).json()[0]
        totals = list(workbook["销售记录"].values)[-1]
        for column, key in ((7, "sales_revenue_cny"), (8, "sales_cost_cny"),
                            (9, "platform_fee_cny"), (10, "gross_profit_cny")):
            self.assertEqual(Decimal(str(totals[column])), Decimal(summary[key]))
        self.assertTrue(set(self.queries) <= {"SELECT"}, self.queries)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 6)

    async def test_store_scope_including_viewer_and_unbound_rejection(self):
        self.add_sale(2, store=8)
        self.session.commit()
        for role in ("operator", "viewer"):
            self.actor.role = role
            rows = self.rows(await self.exported(store_id=8))
            self.assertEqual({row[0] for row in rows}, {"S7"})
            self.assertNotIn("SALE-2", {row[11] for row in rows})
            self.actor.store_id = None
            self.assertEqual((await self.client.get("/sales/export")).status_code, 403)
            self.actor.store_id = 7
        self.actor.role, self.actor.store_id = "admin", None
        self.assertEqual({row[0] for row in self.rows(await self.exported())}, {"S7", "S8"})

    async def test_month_product_keyword_and_parameter_validation(self):
        self.session.add(Product(id=11, sku="OTHER", name="其他商品"))
        self.session.flush()
        self.session.add(InventoryBatch(id=2, product_id=11, user_id=1, batch_no="OTHER",
            quantity=10, remaining_quantity=10, purchase_price=10, unit_cost=10, arrived_at=datetime(2026, 9, 1)))
        self.session.flush()
        self.add_sale(2, product=11, batch=2)
        self.add_sale(3, month=10)
        self.session.commit()
        for filters in ({"keyword": " P10 "}, {"product_id": 10}, {"keyword": "测试商品"}):
            rows = self.rows(await self.exported(**filters))
            self.assertEqual(len(rows), 2)
            self.assertEqual({row[11] for row in rows}, {"SALE", "ORDER-2"})
        october = await self.client.get("/sales/export", params={"month": "2026-10"})
        self.assertEqual(october.status_code, 200)
        self.assertEqual(len(self.rows(load_workbook(BytesIO(october.content)))), 1)
        all_months = await self.client.get("/sales/export")
        self.assertEqual(all_months.status_code, 200)
        self.assertIn("sales_all.xlsx", all_months.headers["content-disposition"])
        self.assertEqual(len(self.rows(load_workbook(BytesIO(all_months.content)))), 5)
        empty = await self.client.get("/sales/export", params={"keyword": "not-found"})
        self.assertEqual(empty.status_code, 404)
        self.assertEqual(empty.json()["detail"]["code"], "sales_export_empty")
        for params, status in (({"month": "2026-13"}, 400), ({"month": "bad"}, 422),
                               ({"keyword": "a"*256}, 422), ({"product_id": -1}, 422)):
            self.assertEqual((await self.client.get("/sales/export", params=params)).status_code, status)

    async def test_missing_fx_and_voided_cost_preserve_blank_amounts_and_totals(self):
        self.entry(6, net=-5, total=-4, quantity=-1, currency="USD", rate=None)
        self.session.get(InventoryBatch, 1).voided_at = datetime(2026, 10, 1)
        self.session.commit()
        workbook = await self.exported()
        rows = self.rows(workbook)
        original = next(row for row in rows if row[11] == "SALE")
        self.assertEqual(original[7:9], (100, 40))
        self.assertIsNone(original[10])
        self.assertIn("已作废", original[17])
        missing = next(row for row in rows if row[13] == "USD")
        for col in (5, 7, 9, 10, 14): self.assertIsNone(missing[col])
        self.assertIn("汇率缺失", missing[17])
        total = list(workbook["销售记录"].values)[-1]
        for col in (7, 9, 10): self.assertIsNone(total[col])
        self.assertEqual(total[8], 40)

    async def test_formula_like_text_and_leading_zero_sku_are_literal(self):
        self.session.get(Store, 7).name = '=HYPERLINK("https://unit.invalid","S")'
        product = self.session.get(Product, 10)
        product.sku, product.name = "0000123", "=1+2"
        self.session.get(Sale, 1).order_no = "=SUM(1,2)"
        self.session.commit()
        book = await self.exported(keyword="=1+2")
        sheet = book["销售记录"]
        row = next(row for row in sheet if row[11].value == "=SUM(1,2)")
        for col in (0, 2, 3, 11): self.assertEqual(row[col].data_type, "s")
        self.assertEqual(row[2].value, "0000123")
        self.assertEqual(book["说明"]["B3"].value, "=1+2")
        self.assertEqual(book["说明"]["B3"].data_type, "s")

    async def test_cost_adjustments_match_list_without_rewriting_source_cost(self):
        os.environ["FIFO_FINANCIAL_CONTROLS_ENABLED"] = "1"
        self.session.add(InventoryCostAdjustment(id=1, request_key="export-cost", source_ref="export-test",
            category="purchase", allocation_basis="quantity", amount=10, amount_cny=10, currency="CNY",
            exchange_rate=1, document_date=date(2026, 9, 1), rate_date=date(2026, 9, 1),
            is_final=True, created_by=1))
        self.session.flush()
        self.session.add(InventoryCostAdjustmentLine(adjustment_id=1, batch_id=1,
            quantity=10, allocation_weight=10, amount_cny=10))
        self.session.commit()
        listed = (await self.client.get("/sales", params={"month": "2026-09"})).json()
        original = next(row for row in listed if row["id"] == 1)
        row = next(row for row in self.rows(await self.exported()) if row[11] == "SALE")
        self.assertEqual(row[8], 44)
        self.assertEqual(Decimal(str(row[8])), Decimal(original["total_cost"]))
        self.assertEqual(Decimal(str(row[10])), Decimal(original["profit_cny"]))
        self.assertEqual(self.session.get(Sale, 1).total_cost, 40)

    async def test_over_limit_rejected_before_list_materialization(self):
        with patch("routers.sales.MAX_EXPORT_ROWS", 2), patch("routers.sales.list_sales", new_callable=AsyncMock) as loader:
            response = await self.client.get("/sales/export", params={"month": "2026-09"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["code"], "sales_export_too_large")
        loader.assert_not_awaited()

    async def test_records_removed_after_count_do_not_produce_empty_file(self):
        with patch("routers.sales.list_sales", new_callable=AsyncMock, return_value=[]):
            response = await self.client.get("/sales/export", params={"month": "2026-09"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "sales_export_empty")


if __name__ == "__main__": unittest.main()
