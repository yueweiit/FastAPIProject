"""库存工作台：独立SQLite/ASGI只读查询，不启动main或连接业务数据库。"""

import os
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openpyxl import load_workbook
from fastapi import FastAPI
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from auth import get_current_user
from database import Base, get_db
from models import BatchChangeLog, InventoryBatch, Product, Sale, SaleCostDetail, Store, User
from routers.inventory import router
from services.inventory_activity import build_activity, local_audit_time
from tests.test_batch_edit import AsyncFacade


class InventoryReadSession(AsyncFacade):
    async def rollback(self): self.session.rollback()


class InventoryWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flag = patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "0"})
        self.flag.start()
        self.engine = create_engine("sqlite://")
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.session.add_all([Store(id=i, platform_store_id=str(i), name=f"Store{i}",
            country_or_region="CN", settlement_currency="CNY") for i in (7, 8)])
        self.session.flush()
        self.session.add_all([User(id=i, username=f"U{i}", password_hash="test", role="operator", store_id=i)
                              for i in (7, 8)])
        self.session.add_all([Product(id=1, sku="SKU-1", name="<商品1>", product_type="safe_stock", safe_stock_quantity=90),
                              Product(id=2, sku="SKU-2", name="商品2", product_type="no_impairment"),
                              Product(id=3, sku="SKU-3", name="商品3")])
        self.session.flush()
        self.session.add_all([
            InventoryBatch(id=1, product_id=1, user_id=7, batch_no="B1", quantity=100,
                remaining_quantity=70, purchase_price=10, unit_cost=10, arrived_at=datetime(2026, 9, 1),
                dingtalk_order_no="IN-1"),
            InventoryBatch(id=2, product_id=1, user_id=8, batch_no="B2", quantity=50,
                remaining_quantity=0, purchase_price=12, unit_cost=12, arrived_at=datetime(2026, 9, 10),
                voided_at=datetime(2026, 9, 14, 16, 0)),  # 上海9/15
            InventoryBatch(id=3, product_id=2, user_id=7, batch_no="B3", quantity=10,
                remaining_quantity=10, purchase_price=3, unit_cost=3, arrived_at=datetime(2026, 8, 10)),
        ])
        self.session.flush()
        self.add_sale(1, 1, 20, datetime(2026, 9, 3), 7)
        self.add_sale(2, 1, 10, datetime(2026, 9, 4), 8)
        # 作废保留原余量，这里B2为无销售的50件软作废。
        self.session.get(InventoryBatch, 2).remaining_quantity = 50
        self.session.commit()
        self.actor = SimpleNamespace(id=10, role="admin", store_id=None)
        self.app = FastAPI()
        self.app.include_router(router)

        async def actor(): return self.actor
        async def db():
            try:
                yield InventoryReadSession(self.session)
            finally:
                self.session.rollback()
        self.app.dependency_overrides[get_current_user] = actor
        self.app.dependency_overrides[get_db] = db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://unit.invalid")
        self.queries = []
        self.sql = []
        event.listen(self.engine, "before_cursor_execute", self.record_query)

    def record_query(self, _conn, _cursor, statement, _params, _context, _many):
        self.queries.append(statement.strip().split()[0].upper())
        self.sql.append(statement)

    def add_sale(self, sale_id, batch_id, quantity, sold_at, store_id, user_id=None):
        batch = self.session.get(InventoryBatch, batch_id)
        self.session.add(Sale(id=sale_id, product_id=batch.product_id, user_id=user_id or store_id,
            store_id=store_id, order_no=f"ORDER-{sale_id}", quantity=quantity, selling_price=20,
            total_cost=quantity * batch.unit_cost, platform_fee=0, profit=0, sold_at=sold_at))
        self.session.flush()
        self.session.add(SaleCostDetail(sale_id=sale_id, batch_id=batch_id, quantity=quantity, unit_cost=batch.unit_cost))
        self.session.flush()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.session.close()
        self.engine.dispose()
        self.flag.stop()

    async def daily(self, **params):
        response = await self.client.get("/inventory/daily", params={"month": "2026-09-01", **params})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def exported(self, view="overview", **params):
        query = {"view": view, **({"month": "2026-09-01"} if view != "overview" else {}), **params}
        response = await self.client.get("/inventory/export", params=query)
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        self.assertIn("spreadsheetml.sheet", response.headers["content-type"])
        self.assertIn(f"inventory_{view}_", response.headers["content-disposition"])
        return load_workbook(BytesIO(response.content))

    def sheet_row(self, sheet, sku):
        return next(row for row in sheet.iter_rows(min_row=2) if row[0].value == sku)

    def item(self, data, product_id=1):
        return next(row for row in data["items"] if row["product_id"] == product_id)

    async def test_daily_reconciles_arrivals_sales_void_and_carry_forward(self):
        data = await self.daily()
        days = self.item(data)["days"]
        self.assertEqual(len(days), 30)
        self.assertEqual(days[0]["inbound"], 100)
        self.assertEqual(days[2]["outbound"], 20)
        self.assertEqual(days[3]["outbound"], 10)
        self.assertEqual(days[9]["inbound"], 50)
        self.assertEqual(days[14]["adjustment"], -50)
        self.assertEqual(days[-1]["closing_quantity"], 70)
        for day in days:
            self.assertEqual(day["opening_quantity"] + day["inbound"] - day["outbound"] + day["adjustment"], day["closing_quantity"])
        self.assertEqual(self.item(data, 2)["days"][0]["opening_quantity"], 10)
        self.assertIn("inventory_current_documents", data["warnings"])
        self.assertTrue(set(self.queries) <= {"SELECT"}, self.queries)

    async def test_overview_reuses_current_shared_cost_and_safety(self):
        response = await self.client.get("/inventory/overview")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        product = next(row for row in data["items"] if row["id"] == 1)
        self.assertEqual(product["stock_quantity"], 70)
        self.assertEqual(Decimal(str(product["stock_value"])), Decimal(700))
        self.assertEqual(product["stock_status"], "low")
        self.assertEqual(data["summary"]["quantity"], 80)
        self.assertEqual(data["summary"]["low_count"], 1)
        self.assertEqual(data["summary"]["zero_count"], 1)

    async def test_overview_suggestion_filters_exact_product_without_prefix_collisions(self):
        self.session.add(Product(id=4, sku="SKU-10", name="相似商品", product_type="new"))
        self.session.commit()
        self.queries.clear()
        broad = (await self.client.get("/inventory/overview", params={"keyword": "SKU-1"})).json()
        self.assertEqual({row["id"] for row in broad["items"]}, {1, 4})
        response = await self.client.get("/inventory/overview", params={"keyword": "SKU-1", "product_id": 1, "page": 99})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual([row["id"] for row in data["items"]], [1])
        self.assertEqual((data["total"], data["page"]), (1, 1))
        self.assertEqual(data["summary"]["quantity"], 70)
        self.assertEqual(Decimal(str(data["summary"]["inventory_amount"])), Decimal(700))
        book = await self.exported(keyword="SKU-1", product_id=1)
        self.assertEqual(book.active.max_row, 3)
        self.assertEqual(book.active["A2"].value, "SKU-1")
        self.assertTrue(set(self.queries) <= {"SELECT"})

    async def test_overview_product_filter_keeps_shared_scope_and_validates_id(self):
        self.actor = SimpleNamespace(id=7, role="viewer", store_id=None)
        response = await self.client.get("/inventory/overview", params={"product_id": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "shared")
        for params in ({"product_id": 9999}, {"product_id": 1, "keyword": "SKU-2"}):
            data = (await self.client.get("/inventory/overview", params=params)).json()
            self.assertEqual((data["items"], data["total"], data["summary"]["quantity"]), ([], 0, 0))
        for product_id in (0, -1, "invalid"):
            self.assertEqual((await self.client.get("/inventory/overview", params={"product_id": product_id})).status_code, 422)

    async def test_quantity_edits_are_adjustments_not_duplicate_receipts(self):
        batch = self.session.get(InventoryBatch, 1)
        batch.quantity = 120
        batch.remaining_quantity = 90
        self.session.add(BatchChangeLog(batch_id=1, batch_no="B1", actor_id=7, actor_username="U7", actor_role="operator",
            before_values={"quantity": 100}, after_values={"quantity": 120}, created_at=datetime(2026, 9, 5)))
        self.session.commit()
        days = self.item(await self.daily())["days"]
        self.assertEqual(days[0]["inbound"], 100)
        self.assertEqual(days[4]["adjustment"], 20)
        self.assertEqual(days[-1]["closing_quantity"], 90)
        correction = await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-05", "kind": "adjustment"})
        self.assertEqual(correction.json()["items"][0]["quantity"], 20)
        received = await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-01", "kind": "inbound"})
        self.assertEqual(received.json()["items"][0]["quantity"], 100)

    async def test_later_quantity_edit_does_not_restate_previous_month_balance(self):
        batch = self.session.get(InventoryBatch, 3)
        batch.quantity = 15
        batch.remaining_quantity = 15
        self.session.add(BatchChangeLog(batch_id=3, batch_no="B3", actor_id=7, actor_username="U7", actor_role="operator",
            before_values={"quantity": 10}, after_values={"quantity": 15}, created_at=datetime(2026, 10, 3)))
        self.session.commit()
        days = self.item(await self.daily(), 2)["days"]
        self.assertEqual(days[0]["opening_quantity"], 10)
        self.assertEqual(days[-1]["closing_quantity"], 10)

    async def test_invalid_remaining_does_not_create_fake_balancing_adjustment(self):
        self.session.get(InventoryBatch, 1).remaining_quantity = 99
        self.session.commit()
        item = self.item(await self.daily())
        self.assertIn("inventory_history_inconsistent", item["warnings"])
        for key in ("opening_quantity", "closing_quantity", "adjustment"):
            self.assertIsNone(item["days"][-1][key])

    async def test_inconsistent_correction_chain_does_not_invent_receipt_quantity(self):
        self.session.add(BatchChangeLog(batch_id=1, batch_no="B1", actor_id=7, actor_username="U7", actor_role="operator",
            before_values={"quantity": 100}, after_values={"quantity": 120}, created_at=datetime(2026, 9, 5)))
        self.session.commit()  # 当前仍是100，与日志的120不一致。
        item = self.item(await self.daily())
        self.assertIsNone(item["days"][0]["inbound"])
        response = await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-01", "kind": "inbound"})
        self.assertIsNone(response.json()["items"][0]["quantity"])

    async def test_negative_recorded_stock_is_pending_not_zero(self):
        self.session.get(InventoryBatch, 1).remaining_quantity = -1
        self.session.commit()
        response = await self.client.get("/inventory/overview")
        row = next(item for item in response.json()["items"] if item["id"] == 1)
        self.assertEqual(row["stock_status"], "pending")

    async def test_extra_store_parameter_cannot_expand_outbound_scope(self):
        self.actor = SimpleNamespace(id=7, role="operator", store_id=7)
        data = await self.daily(store_id=8)
        self.assertEqual(sum(day["outbound"] for day in self.item(data)["days"]), 20)

    async def test_store_users_never_receive_other_store_outbound_or_global_balances(self):
        for role in ("operator", "viewer"):
            self.actor = SimpleNamespace(id=7, role=role, store_id=7)
            item = self.item(await self.daily())
            self.assertEqual(sum(day["outbound"] for day in item["days"]), 20)
            self.assertTrue(all(day["closing_quantity"] is None and day["opening_quantity"] is None
                                and day["adjustment"] is None for day in item["days"]))
            other = await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-04", "kind": "outbound"})
            self.assertEqual(other.json()["total"], 0)
            own = await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-03", "kind": "outbound"})
            self.assertEqual(own.json()["items"][0]["reference"], "ORDER-1")
            self.assertNotIn("ORDER-2", str(item))
            self.assertEqual((await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-05", "kind": "adjustment"})).status_code, 403)

    async def test_unbound_user_can_read_shared_overview_but_not_outbound(self):
        self.actor = SimpleNamespace(id=7, role="viewer", store_id=None)
        self.assertEqual((await self.client.get("/inventory/overview")).status_code, 200)
        self.assertEqual((await self.client.get("/inventory/daily", params={"month": "2026-09-01"})).status_code, 403)
        self.assertEqual((await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-03"})).status_code, 403)

    async def test_legacy_null_store_uses_current_creator_binding(self):
        self.session.get(Sale, 1).store_id = None
        self.session.commit()
        self.actor = SimpleNamespace(id=7, role="viewer", store_id=7)
        self.assertEqual(sum(day["outbound"] for day in self.item(await self.daily())["days"]), 20)

    async def test_viewer_does_not_get_voided_batch_inbound_sources(self):
        self.actor = SimpleNamespace(id=7, role="viewer", store_id=7)
        response = await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-10", "kind": "inbound"})
        self.assertEqual(response.json()["total"], 0)

    async def test_movement_pagination_and_signed_outbound(self):
        response = await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-03", "kind": "outbound", "page": 99, "page_size": 1})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["items"][0]["quantity"], -20)
        response = await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2026-09-15", "kind": "adjustment"})
        self.assertEqual(response.json()["items"][0]["quantity"], -50)

    async def test_search_pagination_empty_products_and_bad_inputs(self):
        data = await self.daily(keyword="SKU-2", page=99, page_size=1)
        self.assertEqual((data["total"], data["page"]), (1, 1))
        self.assertEqual(data["items"][0]["product_id"], 2)
        data = await self.daily(keyword="不存在")
        self.assertEqual(data["items"], [])
        data = await self.daily(product_id=3)
        self.assertTrue(all(day["closing_quantity"] == 0 for day in data["items"][0]["days"]))
        for params in ({"month": "2026-09-02"}, {"month": "2099-01-01"},
                       {"month": "2026-09-01", "page_size": 101}):
            self.assertEqual((await self.client.get("/inventory/daily", params=params)).status_code, 422)
        self.assertEqual((await self.client.get("/inventory/movements", params={"product_id": 1, "day": "2099-01-01"})).status_code, 422)

    async def test_receipts_before_month_and_same_day_sales(self):
        self.session.get(InventoryBatch, 3).remaining_quantity = 6
        self.add_sale(3, 3, 4, datetime(2026, 8, 10, 12), 7)
        self.session.commit()
        days = self.item(await self.daily(), 2)["days"]
        self.assertEqual(days[0]["opening_quantity"], 6)
        self.assertEqual(days[-1]["closing_quantity"], 6)

    def test_audit_business_date_uses_shanghai_and_year_boundary(self):
        self.assertEqual(local_audit_time(datetime(2025, 12, 31, 16)), datetime(2026, 1, 1))

    async def test_disabled_finance_does_not_query_financial_tables(self):
        await self.daily()
        self.assertTrue(set(self.queries) <= {"SELECT"})
        self.assertFalse(any("inventory_cost_adjustment" in statement for statement in self.sql))

    async def test_source_limit_rejects_partial_results_and_can_narrow_to_one_product(self):
        with patch("services.inventory_activity.MAX_SOURCE_ROWS", 2):
            response = await self.client.get("/inventory/daily", params={"month": "2026-09-01"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "inventory_sources_too_large")
            self.assertNotIn("items", response.json())
            self.assertEqual((await self.daily(product_id=2))["total"], 1)

    async def test_rollback_recalculates_effective_history_without_fake_return(self):
        detail = self.session.scalar(select(SaleCostDetail).where(SaleCostDetail.sale_id == 1))
        self.session.delete(detail)
        self.session.flush()
        self.session.delete(self.session.get(Sale, 1))
        self.session.get(InventoryBatch, 1).remaining_quantity += 20
        self.session.commit()
        data = await self.daily()
        days = self.item(data)["days"]
        self.assertEqual(days[2]["outbound"], 0)
        self.assertEqual(days[2]["inbound"], 0)
        self.assertEqual(days[-1]["closing_quantity"], 90)
        self.assertIn("inventory_current_documents", data["warnings"])

    async def test_same_day_edit_before_arrival_is_in_receipt(self):
        batch = self.session.get(InventoryBatch, 1)
        batch.arrived_at = datetime(2026, 9, 1, 12)
        batch.quantity, batch.remaining_quantity = 120, 90
        self.session.add(BatchChangeLog(batch_id=1, batch_no="B1", actor_id=7, actor_username="U7", actor_role="operator",
            before_values={"quantity": 100}, after_values={"quantity": 120}, created_at=datetime(2026, 9, 1, 1)))  # 上海09:00
        self.session.commit()
        days = self.item(await self.daily())["days"]
        self.assertEqual(days[0]["inbound"], 120)
        self.assertEqual(days[0]["adjustment"], 0)

    async def test_cancelled_future_receipt_never_becomes_stock(self):
        batch = self.session.get(InventoryBatch, 2)
        batch.voided_at = datetime(2026, 8, 31, 16)  # 上海9/1，早于9/10到货
        self.session.commit()
        days = self.item(await self.daily())["days"]
        self.assertEqual(sum(day["inbound"] for day in days), 100)
        self.assertEqual(sum(day["adjustment"] for day in days), 0)
        self.assertEqual(days[-1]["closing_quantity"], 70)

    async def test_quantity_edit_before_receipt_is_part_of_receipt_not_earlier_stock(self):
        batch = self.session.get(InventoryBatch, 1)
        batch.quantity = 120
        batch.remaining_quantity = 90
        self.session.add(BatchChangeLog(batch_id=1, batch_no="B1", actor_id=7, actor_username="U7", actor_role="operator",
            before_values={"quantity": 100}, after_values={"quantity": 120}, created_at=datetime(2026, 8, 29)))
        self.session.commit()
        days = self.item(await self.daily())["days"]
        self.assertEqual(days[0]["inbound"], 120)
        self.assertEqual(days[0]["opening_quantity"], 0)
        self.assertEqual(days[-1]["closing_quantity"], 90)

    async def test_current_month_is_capped_at_business_today(self):
        with patch("services.inventory_activity.business_today", return_value=date(2026, 9, 5)):
            data = await self.daily()
        self.assertEqual(data["end"], "2026-09-05")
        self.assertEqual(len(self.item(data)["days"]), 5)

    async def test_enabled_cost_adjustments_use_same_inventory_value_as_products(self):
        from models import InventoryCostAdjustment, InventoryCostAdjustmentLine
        with patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "1"}):
            adjustment = InventoryCostAdjustment(request_key="workspace-cost", source_ref="unit-test", category="other",
                amount=100, currency="CNY", exchange_rate=1, amount_cny=100,
                rate_date=date(2026, 9, 1), document_date=date(2026, 9, 1),
                allocation_basis="quantity", is_final=True, created_by=7)
            self.session.add(adjustment)
            self.session.flush()
            self.session.add(InventoryCostAdjustmentLine(adjustment_id=adjustment.id, batch_id=1,
                quantity=100, allocation_weight=100, amount_cny=100))
            self.session.commit()
            response = await self.client.get("/inventory/overview")
        self.assertEqual(response.status_code, 200, response.text)
        item = next(row for row in response.json()["items"] if row["id"] == 1)
        self.assertEqual(Decimal(str(item["stock_value"])), Decimal(770))

    async def test_export_overview_covers_all_matching_pages_and_same_cost(self):
        book = await self.exported(page_size=1)
        sheet = book["库存总览"]
        self.assertEqual(sheet.max_row, 5)
        row = self.sheet_row(sheet, "SKU-1")
        self.assertEqual([cell.value for cell in row[2:6]], ["稳健商品", 90, 70, 700])
        self.assertEqual(sheet.cell(5, 5).value, 80)
        self.assertEqual(sheet.cell(5, 6).value, 730)
        self.assertIn("说明", book.sheetnames)
        self.assertTrue(set(self.queries) <= {"SELECT"})
        narrowed = await self.exported(keyword="SKU-2")
        self.assertEqual(narrowed.active.max_row, 3)
        self.assertEqual(narrowed.active["A2"].value, "SKU-2")

    async def test_export_daily_summary_and_every_day_match_api(self):
        expected = await self.daily()
        book = await self.exported("daily")
        row = self.sheet_row(book["月度汇总"], "SKU-1")
        self.assertEqual([cell.value for cell in row[2:7]], [0, 150, 30, -50, 70])
        detail = book["每日进出"]
        self.assertEqual(detail.max_row, 91)
        cells = {(row[1].value, row[0].value.date().isoformat()): [cell.value for cell in row[3:8]]
                 for row in detail.iter_rows(min_row=2)}
        for product in expected["items"]:
            for day in product["days"]:
                self.assertEqual(cells[(product["sku"], day["date"])],
                    [day[key] for key in ("opening_quantity", "inbound", "outbound", "adjustment", "closing_quantity")])

    async def test_export_matrix_and_selected_product(self):
        book = await self.exported("matrix", product_id=1)
        sheet = book["每日横向表"]
        self.assertEqual(sheet.freeze_panes, "C3")
        self.assertIn("C1:F1", str(sheet.merged_cells))
        self.assertEqual(sheet["C2"].value, "入库")
        self.assertEqual(sheet["E2"].value, "调整")
        self.assertEqual(sheet["A3"].value, "SKU-1")
        self.assertEqual(sheet.max_row, 3)
        self.assertEqual(sheet.cell(3, 3).value, 100)
        self.assertEqual(sheet.cell(3, 3 + 2 * 4 + 1).value, 20)
        self.assertEqual(sheet.cell(3, 3 + 14 * 4 + 2).value, -50)
        self.assertEqual(sheet.cell(3, 3 + 29 * 4 + 3).value, 70)
        self.assertEqual(book["说明"]["B3"].value, "SKU-1")

    async def test_export_scoped_users_have_blank_global_values_and_only_own_outbound(self):
        for role in ("operator", "viewer"):
            self.actor = SimpleNamespace(id=7, role=role, store_id=7)
            book = await self.exported("daily", store_id=8)
            row = self.sheet_row(book["月度汇总"], "SKU-1")
            self.assertEqual(row[4].value, 20)
            for index in (2, 5, 6):
                self.assertIsNone(row[index].value)
            self.assertIn("绑定店铺", row[7].value)
            matrix = await self.exported("matrix", product_id=1)
            self.assertIsNone(matrix["每日横向表"]["E3"].value)
            self.assertIsNone(matrix["每日横向表"]["F3"].value)

    async def test_export_unbound_only_allows_shared_overview(self):
        self.actor = SimpleNamespace(id=7, role="viewer", store_id=None)
        await self.exported()
        for view in ("daily", "matrix"):
            response = await self.client.get("/inventory/export", params={"view": view, "month": "2026-09-01"})
            self.assertEqual(response.status_code, 403)

    async def test_export_unknown_values_and_totals_are_not_zeroed(self):
        self.session.get(InventoryBatch, 1).remaining_quantity = 99
        self.session.commit()
        book = await self.exported("daily")
        row = self.sheet_row(book["月度汇总"], "SKU-1")
        self.assertIsNone(row[6].value)
        self.assertIn("待核对", row[7].value)
        self.assertIsNone(book["月度汇总"]["G5"].value)
        self.session.add(BatchChangeLog(batch_id=1, batch_no="B1", actor_id=7, actor_username="U7", actor_role="operator",
            before_values={"quantity": 100}, after_values={"quantity": 120}, created_at=datetime(2026, 9, 5)))
        self.session.commit()
        matrix = await self.exported("matrix", product_id=1)
        self.assertIsNone(matrix["每日横向表"]["C3"].value)
        self.assertIsNone(matrix["月度汇总"]["D2"].value)

    async def test_export_preserves_sku_text_and_does_not_create_excel_formulas(self):
        product = self.session.get(Product, 1)
        product.sku = "0000123"
        product.name = '=HYPERLINK("https://unit.invalid","x")'
        self.session.commit()
        for view in ("overview", "daily", "matrix"):
            book = await self.exported(view, product_id=1)
            self.assertEqual(book.active["A2"].value, "0000123")
            self.assertEqual(book.active["A2"].data_type, "s")
            self.assertEqual(book.active["B2"].data_type, "s")
            self.assertTrue(book.active["B2"].value.startswith("=HYPERLINK"))
            self.assertFalse(any(cell.data_type == "f" for sheet in book for row in sheet for cell in row))

    async def test_export_limits_empty_and_bad_arguments(self):
        with patch("routers.inventory.MAX_EXPORT_PRODUCTS", 2):
            response = await self.client.get("/inventory/export")
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "inventory_export_too_large")
            await self.exported(keyword="SKU-2")
        with patch("services.inventory_activity.MAX_SOURCE_ROWS", 2):
            response = await self.client.get("/inventory/export", params={"view": "daily", "month": "2026-09-01"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["code"], "inventory_export_sources_too_large")
        response = await self.client.get("/inventory/export", params={"keyword": "not-found"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "inventory_export_empty")
        for params in ({"view": "other"}, {"view": "daily"}, {"view": "daily", "month": "2099-01-01"}, {"product_id": 0}):
            self.assertEqual((await self.client.get("/inventory/export", params=params)).status_code, 422)

    async def test_export_multiple_chunks_keep_one_period_and_complete_results(self):
        with patch("routers.inventory.EXPORT_BATCH_SIZE", 1), patch("routers.inventory.month_bounds",
            side_effect=[(date(2026, 9, 1), date(2026, 9, 30)), AssertionError("Period must be frozen")]):
            book = await self.exported("daily")
        self.assertEqual(book["月度汇总"].max_row, 5)
        self.assertEqual(book["每日进出"].max_row, 91)
