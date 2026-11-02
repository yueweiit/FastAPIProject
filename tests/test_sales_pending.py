"""待确认筛选及原记录重试；隔离SQLite/ASGI，不连接业务库或启动main。"""
import copy
import io
import os
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from openpyxl import load_workbook
from sqlalchemy import delete, event, select

from models import InventoryBatch, Product, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation, SettlementClassification
from routers.sales import _order_no
from tests import test_sales_import_store as fixtures
from tests import test_sales_name_import as names


class PendingSalesTests(unittest.IsolatedAsyncioTestCase):
    request = fixtures.ImportStoreTests.request
    upload = names.NameImportTests.upload

    def setUp(self):
        fixtures.ImportStoreTests.setUp(self)
        # 本夹具仅保留一批10件，明确区分销售日前库存与新增的晚到批次。
        self.session.execute(delete(InventoryBatch).where(InventoryBatch.id != 1))
        self.session.commit()
        flag = patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "0"})
        flag.start(); self.addCleanup(flag.stop)

    tearDown = fixtures.ImportStoreTests.tearDown

    def entries(self):
        return self.session.scalars(select(SettlementEntry).order_by(SettlementEntry.id)).all()

    async def retry(self, **payload):
        return await self.request("POST", "/sales/pending-confirmations/retry", json=payload)

    async def test_manual_confirmation_reuses_source_and_preserves_values_then_rolls_back(self):
        imported = await self.upload([{"name": "未识别商品", "quantity": 2}])
        self.assertEqual(imported.json()["pending_confirmation"], 1)
        entry = self.entries()[0]
        original = (entry.id, entry.source_key, entry.net_product_sales, entry.settlement_total,
                    entry.order_created_at, entry.exchange_rate_to_cny, copy.deepcopy(entry.raw_data))
        body = {"entry_ids": [entry.id], "components": [{"product_id": 1, "multiplier": 4}]}
        response = await self.retry(**body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["confirmed_rows"], response.json()["sales_created"]), (1, 1))
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 2)
        self.assertEqual((entry.id, entry.source_key, entry.net_product_sales, entry.settlement_total,
                          entry.order_created_at, entry.exchange_rate_to_cny), original[:6])
        self.assertEqual(entry.raw_data["产品名"], original[6]["产品名"])
        self.assertEqual(entry.raw_data["_pending_resolution"]["actor_id"], 1)
        header = self.session.get(SalesImportBatch, entry.import_batch_id)
        self.assertEqual((header.sales_created, header.pending_confirmation), (1, 0))
        self.assertEqual((await self.retry(**body)).json()["skipped_rows"], 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 2)
        self.assertEqual((await self.upload([{"name": "未识别商品", "quantity": 2}])).json()["skipped_duplicates"], 1)
        rollback = await self.request("POST", f"/sales/imports/{header.id}/rollback")
        self.assertEqual(rollback.status_code, 200, rollback.text)
        self.assertEqual(rollback.json()["restored_quantity"], 8)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
        self.assertFalse(self.entries())

    async def test_name_fix_can_retry_without_upload_or_manual_components(self):
        await self.upload([{"name": "新商品", "quantity": 2}])
        self.session.add(Product(id=2, sku="NEW", name="新商品")); self.session.flush()
        self.session.add(InventoryBatch(product_id=2, batch_no="NEW-B", quantity=5, remaining_quantity=5,
            purchase_price=5, unit_cost=5, arrived_at=datetime(2026, 9, 1)))
        self.session.commit()
        response = await self.retry(reason="product_missing")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["confirmed_rows"], 1)
        self.assertEqual(self.entries()[0].product_id, 2)

    async def test_future_arrival_cannot_resolve_historical_shortage(self):
        await self.upload([{"name": "商品", "quantity": 20}])
        entry = self.entries()[0]
        batch = InventoryBatch(product_id=1, batch_no="LATE", quantity=20, remaining_quantity=20,
            purchase_price=10, unit_cost=10, arrived_at=datetime(2026, 9, 2))
        self.session.add(batch); self.session.commit()
        response = await self.retry(entry_ids=[entry.id])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["pending_rows"], 1)
        self.assertEqual(response.json()["results"][0]["reason_code"], "stock_shortage")
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
        self.assertEqual(batch.remaining_quantity, 20)
        batch.arrived_at = datetime(2026, 9, 1); self.session.commit()
        self.assertEqual((await self.retry(entry_ids=[entry.id])).json()["confirmed_rows"], 1)
        self.assertEqual(sum(b.remaining_quantity for b in self.session.scalars(select(InventoryBatch))), 10)

    async def test_bundle_is_atomic_and_saved_choice_is_reused_after_stock_fix(self):
        await self.upload([{"name": "组合待识别", "quantity": 2}])
        self.session.add(Product(id=2, sku="SECOND", name="第二个商品")); self.session.flush()
        batch = InventoryBatch(product_id=2, batch_no="SECOND-B", quantity=1, remaining_quantity=1,
            purchase_price=20, unit_cost=20, arrived_at=datetime(2026, 9, 1))
        self.session.add(batch); self.session.commit()
        entry = self.entries()[0]
        response = await self.retry(entry_ids=[entry.id], components=[{"product_id": 1, "multiplier": 2}, {"product_id": 2, "multiplier": 1}])
        self.assertEqual(response.json()["pending_rows"], 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
        self.assertEqual(batch.remaining_quantity, 1)
        self.assertFalse(self.session.scalars(select(Sale)).all())
        self.assertEqual(len(entry.raw_data["_pending_resolution"]["components"]), 2)
        batch.quantity = batch.remaining_quantity = 3; self.session.commit()
        response = await self.retry(entry_ids=[entry.id])
        self.assertEqual(response.json()["sales_created"], 2)
        self.assertEqual((self.session.get(InventoryBatch, 1).remaining_quantity, batch.remaining_quantity), (6, 1))
        sales = self.session.scalars(select(Sale)).all()
        self.assertEqual(sum(s.selling_price * s.quantity for s in sales), Decimal(200))
        self.assertEqual(sum(s.platform_fee for s in sales), Decimal(20))
        self.assertIsNone(entry.sale_id)
        self.assertEqual(len(self.session.scalars(select(SettlementEntryAllocation).where(SettlementEntryAllocation.sale_id.is_not(None))).all()), 2)

    async def test_reason_counts_search_export_and_readonly_permissions(self):
        await self.upload([{"name": "未识别", "order": "MISSING"}, {"name": "商品", "quantity": 20, "order": "STOCK"},
                           {"name": "商品+", "order": "EXPRESSION"}])
        response = await self.request("GET", "/sales/pending-confirmations")
        self.assertEqual(response.json()["total"], 3)
        self.assertEqual({k: v for k, v in response.json()["reason_counts"].items() if v},
                         {"product_missing": 1, "stock_shortage": 1, "expression_invalid": 1})
        filtered = await self.request("GET", "/sales/pending-confirmations?reason=stock_shortage&keyword=STOCK")
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["items"][0]["reason_code"], "stock_shortage")
        export = await self.request("GET", "/sales/pending-confirmations/export?reason=product_missing")
        sheet = load_workbook(io.BytesIO(export.content), data_only=True).active
        self.assertEqual(sheet.max_row, 2)
        self.assertIn("未识别", sheet.cell(2, 8).value)
        for query in ("reason=unsupported", "keyword=" + "x"*256):
            self.assertEqual((await self.request("GET", "/sales/pending-confirmations?" + query)).status_code, 422)
        self.actor.role, self.actor.store_id = "viewer", 7
        self.assertEqual((await self.retry()).status_code, 403)
        self.actor.store_id = 8
        self.assertEqual((await self.request("GET", "/sales/pending-confirmations")).json()["total"], 0)
        self.actor.role = "operator"
        self.assertEqual((await self.retry(entry_ids=[self.entries()[0].id])).status_code, 404)
        self.actor.store_id = None
        self.assertEqual((await self.request("GET", "/sales/pending-confirmations")).status_code, 403)

    async def test_existing_order_and_invalid_component_never_deduct_twice(self):
        await self.upload([{"name": "未识别"}])
        entry = self.entries()[0]
        existing = await self.request("POST", "/sales", json={"product_id": 1, "store_id": 7,
            "order_no": _order_no(entry.order_id, entry.platform_sku_id), "quantity": 1,
            "sold_at": "2026-09-01T00:00:00"})
        self.assertEqual(existing.status_code, 200, existing.text)
        invalid = await self.retry(entry_ids=[entry.id], components=[{"product_id": 999}])
        self.assertEqual(invalid.status_code, 422)
        response = await self.retry(entry_ids=[entry.id], components=[{"product_id": 1}])
        self.assertEqual(response.json()["results"][0]["reason_code"], "order_conflict")
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 9)

    async def test_write_failure_rolls_back_inventory_source_and_counters(self):
        await self.upload([{"name": "未识别"}])
        entry = self.entries()[0]; before = copy.deepcopy(entry.raw_data)
        def fail(*args): raise RuntimeError("test write failure")
        event.listen(SaleCostDetail, "before_insert", fail)
        try:
            with self.assertRaisesRegex(RuntimeError, "test write failure"):
                await self.retry(entry_ids=[entry.id], components=[{"product_id": 1}])
        finally:
            event.remove(SaleCostDetail, "before_insert", fail)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
        self.assertEqual(entry.raw_data, before)
        self.assertEqual(entry.mapping_status, "pending_confirmation")
        self.assertEqual(self.session.get(SalesImportBatch, entry.import_batch_id).pending_confirmation, 1)

    async def test_batch_limit_and_unknown_legacy_reason_are_retained(self):
        await self.upload([{"name": "未识别", "order": f"ORDER-{i}"} for i in range(201)])
        response = await self.retry(reason="product_missing")
        self.assertEqual(response.status_code, 422)
        entry = self.entries()[0]; entry.mapping_error = "旧版本未知异常"; self.session.commit()
        filtered = await self.request("GET", "/sales/pending-confirmations?reason=other")
        self.assertEqual(filtered.json()["total"], 1)
        self.assertEqual(filtered.json()["items"][0]["mapping_error"], "旧版本未知异常")
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)

    async def test_ambiguous_name_requires_explicit_product_and_does_not_guess(self):
        self.session.add(Product(id=2, sku="SAME-NAME", name="商品")); self.session.commit()
        await self.upload([{"name": "商品"}])
        entry = self.entries()[0]
        self.assertEqual((await self.request("GET", "/sales/pending-confirmations")).json()["items"][0]["reason_code"], "product_ambiguous")
        self.assertEqual((await self.retry(entry_ids=[entry.id])).json()["pending_rows"], 1)
        self.assertEqual((await self.retry(entry_ids=[entry.id], components=[{"product_id": 1}])).json()["confirmed_rows"], 1)
        self.assertEqual(entry.product_id, 1)

    async def test_financial_references_and_invalid_source_prevent_new_sales(self):
        await self.upload([{"name": "未识别"}]); entry = self.entries()[0]
        self.session.add(SettlementClassification(settlement_entry_id=entry.id, breakdown={}, confirmed_by=1)); self.session.commit()
        with patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "1"}):
            response = await self.retry(entry_ids=[entry.id], components=[{"product_id": 1}])
        self.assertEqual(response.json()["pending_rows"], 1)
        self.assertIn("结算分类", response.json()["results"][0]["message"])
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
        self.assertFalse(self.session.scalars(select(Sale)).all())
        header = self.session.get(SalesImportBatch, entry.import_batch_id)
        header.status = "rolled_back"; self.session.commit()
        self.assertEqual((await self.retry(entry_ids=[entry.id])).status_code, 409)

    async def test_batch_failure_after_first_row_rolls_back_every_row(self):
        await self.upload([{"name": "稍后更名", "order": "FIRST"}, {"name": "稍后更名", "order": "SECOND"}])
        self.session.get(Product, 1).name = "稍后更名"; self.session.commit()
        calls = 0
        def fail_on_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2: raise RuntimeError("second row failure")
        event.listen(SaleCostDetail, "before_insert", fail_on_second)
        try:
            with self.assertRaisesRegex(RuntimeError, "second row failure"):
                await self.retry()
        finally:
            event.remove(SaleCostDetail, "before_insert", fail_on_second)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)
        self.assertFalse(self.session.scalars(select(Sale)).all())
        self.assertTrue(all(row.mapping_status == "pending_confirmation" for row in self.entries()))
        self.assertEqual(self.session.scalar(select(SalesImportBatch)).pending_confirmation, 2)

    async def test_pending_refund_confirms_without_inventory_or_new_settlement(self):
        await self.upload([{"name": "未识别退款", "type": "退款", "quantity": -2, "net": -100, "total": -90}])
        entry = self.entries()[0]; entry.mapping_status = "pending_confirmation"; entry.mapping_error = "未找到本地商品映射: 旧版退款"
        self.session.get(SalesImportBatch, entry.import_batch_id).pending_confirmation = 1; self.session.commit()
        response = await self.retry(entry_ids=[entry.id])
        self.assertEqual((response.json()["confirmed_rows"], response.json()["sales_created"]), (1, 0))
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)

    async def test_filtered_export_is_readonly_and_text_safe(self):
        await self.upload([{"name": "未识别"}]); entry = self.entries()[0]
        entry.platform_sku_id = "=SUM(1)"; entry.source_file = '=HYPERLINK("x")'; self.session.commit()
        statements = []
        def record(_conn, _cursor, statement, _params, _context, _many): statements.append(statement.strip().split()[0].upper())
        event.listen(self.engine, "before_cursor_execute", record)
        response = await self.request("GET", "/sales/pending-confirmations/export?reason=product_missing")
        event.remove(self.engine, "before_cursor_execute", record)
        self.assertTrue(statements and set(statements) == {"SELECT"})
        sheet = load_workbook(io.BytesIO(response.content)).active
        self.assertEqual((sheet.cell(2, 1).data_type, sheet.cell(2, 11).data_type), ("s", "s"))
        self.assertEqual(sheet.cell(2, 12).value, "未找到本地商品")


if __name__ == "__main__":
    unittest.main()
