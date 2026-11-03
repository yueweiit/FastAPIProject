"""手工跌价归属：隔离SQLite/ASGI，真实批次路由与报表；不运行main。"""
import unittest
from io import BytesIO
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import event, select
from openpyxl import load_workbook

from models import BatchChangeLog, BatchImpairmentStore, BatchImpairmentRule, InventoryBatch, Product, Sale, Store, User
from routers.batches import router as batch_router
from routers.finance_masters import router as master_router
from routers.reports import (_load_inventory_impairment_report_rows, _month_periods,
                             _product_line_profit_data, _store_provision_financial_values,
                             _build_inventory_impairment_workbook, _workbook_bytes_with_formula_cache)
from services.batch_store_allocations import (AssignedBatch, allocated_amount, allocated_batch_amount, allocated_provision_period,
                                             allocated_provision_balance, has_unassigned_provision)
from services.inventory_provision_movements import ProvisionAccrual, ProvisionReplay, ProvisionSale, ProvisionWriteoff
from services.inventory_impairment import InventoryLayer
from services.inventory_provision_movements import replay_inventory_provisions
from tests.test_inventory_workspace import InventoryReadSession
from tests import test_inventory_workspace as workspace_tests


class BatchStoreAllocationTests(unittest.IsolatedAsyncioTestCase):
    add_sale = workspace_tests.InventoryWorkspaceTests.add_sale
    record_query = workspace_tests.InventoryWorkspaceTests.record_query
    asyncTearDown = workspace_tests.InventoryWorkspaceTests.asyncTearDown

    def setUp(self):
        workspace_tests.InventoryWorkspaceTests.setUp(self)
        self.app.include_router(batch_router)
        self.app.include_router(master_router)
        self.actor = SimpleNamespace(id=7, username="U7", role="admin", store_id=7)
        self.session.delete(self.session.get(InventoryBatch, 2))
        product = self.session.get(Product, 1)
        product.product_type, product.safe_stock_quantity = "stable", 0
        self.session.add(Store(id=9, platform_store_id="9", name="停用", is_active=False,
                               country_or_region="CN", settlement_currency="CNY"))
        self.session.commit()

    async def batch(self, batch_id=1):
        response = await self.client.get("/batches")
        self.assertEqual(response.status_code, 200, response.text)
        return next(row for row in response.json() if row["id"] == batch_id)

    async def save(self, ids=None, *, omit=False, version=None, mode=None, batch_id=1):
        row = await self.batch(batch_id)
        payload = {key: row[key] for key in ("product_id", "quantity", "total_amount", "shipping_cost",
                    "last_mile_cost", "other_cost", "arrived_at", "shipping_date")}
        payload["edit_version"] = version or row["edit_version"]
        if not omit:
            payload["impairment_store_ids"] = ids
        if mode is not None:
            payload["impairment_change_mode"] = mode
        return await self.client.put(f"/batches/{batch_id}", json=payload)

    async def test_create_many_stores_and_preserve_single_stock_quantity(self):
        response = await self.client.post("/batches", json={"product_id": 1, "quantity": 100,
            "total_amount": "1000", "impairment_store_ids": [8, 7, 8]})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        self.assertEqual(row["impairment_store_ids"], [7, 8])
        self.assertEqual(row["impairment_store_names"], ["Store7", "Store8"])
        self.assertEqual((row["quantity"], row["remaining_quantity"]), (100, 100))

    async def test_new_batch_can_wait_for_assignment_without_using_creator_store(self):
        original_batches = self.session.query(InventoryBatch).count()
        for number, selection in enumerate((None, []), 1):
            payload = {"product_id": 1, "quantity": 100, "total_amount": "1000", "arrived_at": "2026-09-01T00:00:00"}
            if selection is not None:
                payload["impairment_store_ids"] = selection
            response = await self.client.post("/batches", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            row = response.json()
            self.assertEqual(row["impairment_store_ids"], [])
            self.assertEqual(row["impairment_store_history"], [{"effective_date":"2026-09-01", "store_ids":[],"store_names":[],"is_explicit":True}])
            self.assertEqual(self.session.query(InventoryBatch).count(), original_batches + number)
        rows = await _load_inventory_impairment_report_rows(InventoryReadSession(self.session), date(2026,9,2))
        pending = [row for row in rows if row["store_id"] is None]
        self.assertEqual(len(pending), 2)
        self.assertTrue(all(row["impairment_amount"] == Decimal(10) for row in pending))
        self.assertTrue(all("待分配" in row["comparison_note"] for row in pending))
        periods = {"current": (date(2026,9,1),date(2026,9,3))}
        for owner in (7,8):
            values = await _store_provision_financial_values(InventoryReadSession(self.session),owner,periods)
            self.assertIsNone(values["inventory_impairment"]["current"])
            self.assertTrue(values["_warnings"])

    async def test_voided_unassigned_batch_keeps_historical_unknown_for_unrelated_stores(self):
        from routers.reports import _voided_inventory_holding_counts
        response=await self.client.post('/batches',json={'product_id':1,'quantity':100,'total_amount':'1000','arrived_at':'2026-09-01T00:00:00'})
        self.assertEqual(response.status_code,200,response.text)
        row=response.json()
        periods={'current':(date(2026,9,1),date(2026,9,3)),
                 'previous':(date(2026,8,1),date(2026,9,1)),
                 'ytd':(date(2026,1,1),date(2026,9,3))}
        for owner in (7,8,9):
            self.session.info.clear()
            values=await _store_provision_financial_values(InventoryReadSession(self.session),owner,periods)
            self.assertIsNone(values['inventory_impairment']['current'])
            self.assertIsNone(values['inventory_writeoff']['current'])
        voided=await self.client.post(f"/batches/{row['id']}/void",json={'edit_version':row['edit_version'],'reason':'regression'})
        self.assertEqual(voided.status_code,200,voided.text)
        self.session.get(InventoryBatch,row['id']).voided_at=datetime(2026,10,14,16) # 上海10/15
        self.session.commit()
        for owner in (7,8,9):
            self.session.info.clear()
            values=await _store_provision_financial_values(InventoryReadSession(self.session),owner,periods)
            self.assertIsNone(values['inventory_impairment']['current'])
            self.assertIsNone(values['inventory_writeoff']['current'])
            self.assertIsNone(values['inventory_impairment']['ytd'])
            self.assertEqual(values['inventory_impairment']['previous'],0)
            self.assertTrue(any('已作废批次' in value for value in values['_warnings']))
            counts=await _voided_inventory_holding_counts(InventoryReadSession(self.session),owner,
                {'before':(date(2026,8,1),date(2026,9,2)),
                 'held':(date(2026,9,2),date(2026,9,3)),
                 'after':(date(2026,10,16),date(2026,11,1))})
            self.assertEqual(counts,{'before':0,'held':1,'after':0})

    async def test_zero_cost_pending_batch_keeps_known_zero_before_and_after_void(self):
        response=await self.client.post('/batches',json={'product_id':1,'quantity':100,'total_amount':'0','arrived_at':'2026-09-01T00:00:00'})
        self.assertEqual(response.status_code,200,response.text)
        row=response.json()
        periods={'current':(date(2026,9,1),date(2026,9,3))}
        for after_void in (False,True):
            if after_void:
                voided=await self.client.post(f"/batches/{row['id']}/void",json={'edit_version':row['edit_version'],'reason':'zero cost'})
                self.assertEqual(voided.status_code,200,voided.text)
            self.session.info.clear()
            values=await _store_provision_financial_values(InventoryReadSession(self.session),8,periods)
            self.assertEqual(values['inventory_impairment']['current'],0)
            self.assertEqual(values['inventory_writeoff']['current'],0)
            self.assertEqual(values['_warnings'],[])

    async def test_pending_provision_checks_amount_without_masking_unknown_units(self):
        from services.batch_impairment_history import StoreRule
        for cost,accrued,written,expected in [('10',0,0,False),('0',1,0,False),('0',0,1,False),
                ('0.0001',1,0,True),('0',None,0,True),('10',0,None,True)]:
            with self.subTest(cost=cost,accrued=accrued,written=written):
                batch=AssignedBatch(SimpleNamespace(id=999,unit_cost=Decimal(cost)),[],[StoreRule(date.min,[],True)])
                replay=SimpleNamespace(accrued_units=lambda *args:accrued,written_off_units=lambda *args:written)
                self.assertEqual(has_unassigned_provision(replay,batch,date(2026,9,1),date(2026,10,1)),expected)

    async def test_batch_edit_can_keep_unassigned_without_forcing_assignment(self):
        response = await self.save(omit=True)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["impairment_store_ids"],[])
        self.assertEqual((await self.save([])).status_code,422)
        self.assertEqual((await self.save([7])).status_code,200)

    async def test_inventory_assignment_endpoint_only_changes_ownership_and_checks_version(self):
        before = await self.batch()
        data = {"impairment_store_ids":[8,7,8],"edit_version":before["edit_version"],"impairment_change_mode":"from_arrival"}
        response = await self.client.put("/batches/1/impairment-stores",json=data)
        self.assertEqual(response.status_code,200,response.text)
        after=response.json()
        self.assertEqual(after["impairment_store_ids"],[7,8])
        for field in ("quantity","remaining_quantity","purchase_price","unit_cost","total_amount","shipping_cost","last_mile_cost","other_cost","arrived_at","shipping_date"):
            self.assertEqual(after[field],before[field],field)
        self.assertEqual((await self.client.put("/batches/1/impairment-stores",json=data)).status_code,409)
        log=self.session.scalar(select(BatchChangeLog).order_by(BatchChangeLog.id.desc()))
        self.assertEqual(log.after_values["impairment_store_ids"],[7,8])
        current={**data,"edit_version":after["edit_version"]}
        self.assertEqual((await self.client.put("/batches/1/impairment-stores",json={**current,"quantity":999})).status_code,422)
        self.assertEqual((await self.client.put("/batches/1/impairment-stores",json={**current,"impairment_store_ids":[]})).status_code,422)
        self.assertEqual((await self.client.put("/batches/1/impairment-stores",json={**current,"impairment_store_ids":[999]})).status_code,422)
        self.actor.role='viewer'
        self.assertEqual((await self.client.put("/batches/1/impairment-stores",json=current)).status_code,403)

    async def test_exact_batch_filter_reads_old_batch_without_changing_data(self):
        self.session.add(InventoryBatch(product_id=1, user_id=8, batch_no="NEWER", quantity=10,
            remaining_quantity=10, purchase_price=20, unit_cost=20, arrived_at=datetime(2026, 10, 1)))
        self.session.commit()
        before = await self.batch()
        self.queries.clear()
        for role in ("admin", "operator", "viewer"):
            self.actor.role = role
            response = await self.client.get("/batches", params={"product_id":1,"batch_id":1,"page":99,"page_size":1})
            self.assertEqual(response.status_code,200,response.text)
            data = response.json()
            self.assertEqual((data["total"],data["page"]),(1,1))
            self.assertEqual(data["items"],[before])
        self.assertTrue(all(query == "SELECT" for query in self.queries),self.queries)
        self.assertEqual((await self.client.get("/batches?batch_id=1&product_id=2")).json(),[])
        self.assertEqual((await self.client.get("/batches?batch_id=999")).json(),[])
        self.assertEqual((await self.client.get("/batches?batch_id=0")).status_code,422)
        self.session.get(InventoryBatch,1).voided_at = datetime(2026,10,1)
        self.session.commit()
        self.assertEqual((await self.client.get("/batches?batch_id=1")).json(),[])

    async def test_sold_out_legacy_batch_can_change_only_ownership_with_controls_disabled(self):
        self.add_sale(3,1,70,datetime(2026,9,5),7)
        self.session.get(InventoryBatch,1).remaining_quantity = 0
        self.session.commit()
        before = await self.batch()
        self.assertEqual(before["impairment_store_history"],[])
        self.actor.id,self.actor.username,self.actor.role,self.actor.store_id = 8,"U8","operator",8
        with patch("services.finance_config.financial_controls_enabled", return_value=False), \
             patch("routers.batches.financial_controls_enabled", return_value=False):
            response = await self.client.put("/batches/1/impairment-stores",json={
                "impairment_store_ids":[8],"edit_version":before["edit_version"],"impairment_change_mode":"from_arrival"})
        self.assertEqual(response.status_code,200,response.text)
        after = response.json()
        self.assertEqual(after["impairment_store_ids"],[8])
        self.assertEqual(after["impairment_store_history"][0]["effective_date"],"2026-09-01")
        for field in ("quantity","remaining_quantity","unit_cost","total_amount","purchase_price","shipping_cost","last_mile_cost","other_cost","arrived_at"):
            self.assertEqual(after[field],before[field],field)

    async def test_inventory_assignment_allows_operator_and_rolls_back_failed_store_write(self):
        before = await self.batch()
        payload={"impairment_store_ids":[7,8],"edit_version":before["edit_version"],"impairment_change_mode":"from_arrival"}
        with patch("routers.batches.replace_stores", new=AsyncMock(side_effect=RuntimeError('write failed'))):
            with self.assertRaises(RuntimeError):
                await self.client.put('/batches/1/impairment-stores',json=payload)
        self.assertEqual((await self.batch())["edit_version"],before["edit_version"])
        self.assertEqual(self.session.query(BatchImpairmentRule).count(),0)
        self.assertEqual(self.session.query(BatchChangeLog).count(),0)
        self.actor.id,self.actor.username,self.actor.role,self.actor.store_id=8,'U8','operator',8
        response=await self.client.put('/batches/1/impairment-stores',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["impairment_store_ids"],[7,8])
        self.assertEqual(response.json()["remaining_quantity"],before["remaining_quantity"])

    async def test_required_stores_cannot_be_cleared_and_omission_preserves_history(self):
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            changed = await self.save([8], mode="from_change_date")
        self.assertEqual(changed.status_code, 200, changed.text)
        before = await self.batch()
        log_count = self.session.query(BatchChangeLog).count()
        for mode in ("from_arrival", "from_change_date"):
            with self.subTest(mode=mode):
                response = await self.save([], mode=mode)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()["detail"]["code"], "batch_impairment_store_required")
                self.assertEqual(await self.batch(), before)
                self.assertEqual(self.session.query(BatchChangeLog).count(), log_count)
        kept = await self.save(omit=True)
        self.assertEqual(kept.status_code, 200, kept.text)
        self.assertEqual(kept.json()["impairment_store_ids"], [8])
        self.assertEqual(kept.json()["impairment_store_history"], before["impairment_store_history"])

    async def test_edit_preserves_cost_stock_and_logs_assignments_with_stale_guard(self):
        old = await self.batch()
        response = await self.save([7, 8])
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        for key in ("quantity", "remaining_quantity", "unit_cost", "total_amount"):
            self.assertEqual(updated[key], old[key])
        self.assertNotEqual(updated["edit_version"], old["edit_version"])
        self.assertEqual((await self.save([7], version=old["edit_version"])).status_code, 409)
        log = self.session.scalar(select(BatchChangeLog).order_by(BatchChangeLog.id.desc()))
        self.assertEqual(log.before_values["impairment_store_ids"], [])
        self.assertEqual(log.after_values["impairment_store_ids"], [7, 8])
        self.assertEqual((await self.save(omit=True)).json()["impairment_store_ids"], [7, 8])
        self.assertEqual((await self.save([])).status_code, 422)
        self.assertEqual((await self.batch())["impairment_store_ids"], [7, 8])

    async def test_invalid_inactive_and_readonly_accounts_cannot_change_assignments(self):
        for ids in ([999], [9], [0], [-1]):
            self.assertEqual((await self.save(ids)).status_code, 422)
        self.actor.role = "viewer"
        self.assertEqual((await self.save([7])).status_code, 403)
        self.assertEqual((await self.batch())["impairment_store_ids"], [])

    async def test_inactive_existing_assignment_is_preserved_and_linked_store_cannot_be_deleted(self):
        await self.save([7, 8])
        self.session.get(Store, 7).is_active = False
        self.session.commit()
        self.assertEqual((await self.save([7, 8])).status_code, 200)
        self.assertEqual((await self.client.post("/batches", json={"product_id": 1, "quantity": 1, "impairment_store_ids": [7]})).status_code, 422)
        response = await self.client.delete("/finance-masters/stores/8")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "store_has_impairment_batches")

    async def test_audit_failure_rolls_back_assignment_changes(self):
        await self.save([7])
        def fail(*_args): raise RuntimeError("audit failure")
        event.listen(BatchChangeLog, "before_insert", fail)
        try:
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                await self.save([7, 8])
        finally:
            event.remove(BatchChangeLog, "before_insert", fail)
        self.assertEqual((await self.batch())["impairment_store_ids"], [7])

    async def test_remaining_impairment_and_period_movements_are_equal_not_sales_quota(self):
        await self.save([7, 8])
        db = InventoryReadSession(self.session)
        for owner in (7, 8):
            rows = await _load_inventory_impairment_report_rows(db, date(2026, 9, 30), owner)
            row = next(row for row in rows if row["batch_id"] == 1)
            self.assertEqual(row["quantity"], Decimal(35))
            self.assertEqual(row["impairment_amount"], Decimal("101.50"))
            values = await _store_provision_financial_values(db, owner, _month_periods(date(2026, 9, 1)))
            self.assertEqual(values["inventory_impairment"]["current"], Decimal(105))
            self.assertEqual(values["inventory_writeoff"]["current"], Decimal("3.50"))
        global_rows = await _load_inventory_impairment_report_rows(db, date(2026, 9, 30))
        row = next(row for row in global_rows if row["batch_id"] == 1)
        self.assertEqual((row["quantity"], row["impairment_amount"]), (70, Decimal(203)))

    async def test_reassignment_recomputes_earlier_month_and_single_store_gets_all(self):
        await self.save([7, 8])
        db = InventoryReadSession(self.session)
        first = await _load_inventory_impairment_report_rows(db, date(2026, 9, 30), 7)
        self.assertEqual(next(r for r in first if r["batch_id"] == 1)["impairment_amount"], Decimal("101.50"))
        await self.save([8])
        self.assertFalse(any(r["batch_id"] == 1 for r in await _load_inventory_impairment_report_rows(db, date(2026, 9, 30), 7)))
        rows = await _load_inventory_impairment_report_rows(db, date(2026, 9, 30), 8)
        self.assertEqual(next(r for r in rows if r["batch_id"] == 1)["impairment_amount"], Decimal(203))

    async def test_invalid_history_preserves_null_for_every_assigned_store(self):
        await self.save([7, 8])
        sale = self.session.scalar(select(Sale).order_by(Sale.id))
        sale.sold_at = sale.sold_at.replace(month=8)
        self.session.commit()
        db = InventoryReadSession(self.session)
        for owner in (7, 8):
            row = next(r for r in await _load_inventory_impairment_report_rows(db, date(2026, 9, 30), owner) if r["batch_id"] == 1)
            self.assertIsNone(row["impairment_amount"])
            values = await _store_provision_financial_values(db, owner, _month_periods(date(2026, 9, 1)))
            self.assertIsNone(values["inventory_impairment"]["current"])

    async def test_product_line_credits_follow_owners_and_reconcile_store_costs(self):
        await self.save([7, 8])
        db = InventoryReadSession(self.session)
        for owner, expected in ((7, Decimal("200.5")), (8, Decimal("100.5"))):
            rows = await _product_line_profit_data(db, {owner}, date(2026, 9, 1), date(2026, 9, 30))
            self.assertEqual(sum(row["current"]["cost"] for row in rows), expected)
            product = next(row for row in rows if row["product_id"] == 1)
            self.assertEqual(product["row_type"], "product")
            self.assertIn("按批次跌价归属店铺", product["note"])
            quantities = [sale.quantity for sale in self.session.scalars(select(Sale)).all()
                if sale.product_id == 1 and (sale.store_id or self.session.get(User, sale.user_id).store_id) == owner
                and date(2026, 9, 1) <= sale.sold_at.date() < date(2026, 10, 1)]
            self.assertEqual(product["current_quantity"], sum(quantities))

    async def test_store_workbook_keeps_allocated_cost_reserve_and_cached_book_value(self):
        await self.save([7, 8])
        rows = await _load_inventory_impairment_report_rows(InventoryReadSession(self.session), date(2026, 9, 30), 7)
        rows = [row for row in rows if row["batch_id"] == 1]
        book, cache = _build_inventory_impairment_workbook(rows, date(2026, 9, 30))
        sheet = load_workbook(_workbook_bytes_with_formula_cache(book, cache), data_only=True).active
        self.assertEqual(sheet["K2"].value, 35)
        self.assertEqual(sheet["M2"].value, 350)
        self.assertEqual(sheet["O2"].value, 101.5)
        self.assertEqual(sheet["P2"].value, 248.5)

    async def test_batch_export_appends_assignment_as_literal_excel_text(self):
        self.session.get(Store, 8).name = '=HYPERLINK("https://unit.invalid","B")'
        self.session.commit()
        await self.save([8])
        response = await self.client.get("/batches/export", params={"batch_ids": 1})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet.cell(1, sheet.max_column).value, "跌价归属店铺（均分）")
        self.assertEqual(sheet.cell(2, sheet.max_column).data_type, "s")
        self.assertTrue(sheet.cell(2, sheet.max_column).value.startswith('=HYPERLINK'))

    async def test_excel_recalculation_cannot_overwrite_allocated_precision_tail(self):
        self.session.get(Store, 9).is_active = True
        self.session.commit()
        await self.save([7, 8, 9])
        rows = [row for row in await _load_inventory_impairment_report_rows(
            InventoryReadSession(self.session), date(2026, 9, 30), 9) if row["batch_id"] == 1]
        book, _cache = _build_inventory_impairment_workbook(rows, date(2026, 9, 30))
        self.assertEqual(book.active["M2"].value, Decimal("233.3333334"))
        self.assertEqual(book.active["M2"].data_type, "n")
        self.assertEqual(book.active["H2"].value, Decimal("333.3333334"))
        self.assertEqual(book.active["H2"].data_type, "n")
        self.assertEqual(book.active["K2"].number_format, '#,##0.####')
        self.assertEqual(book.active["P2"].data_type, "f")

    async def test_small_shared_batch_report_and_excel_keep_nonnegative_reserves(self):
        self.session.get(Store, 9).is_active = True
        self.session.commit()
        response = await self.client.post("/batches", json={"product_id": 1, "quantity": 3,
            "total_amount": "3", "arrived_at": "2026-09-01T00:00:00", "impairment_store_ids": [7, 8, 9]})
        self.assertEqual(response.status_code, 200, response.text)
        batch_id = response.json()["id"]
        self.add_sale(99, batch_id, 2, datetime(2026, 9, 2), 7)
        self.session.get(InventoryBatch, batch_id).remaining_quantity = 1
        self.session.commit()
        reserves, books = [], []
        for owner in (7, 8, 9):
            row = next(row for row in await _load_inventory_impairment_report_rows(
                InventoryReadSession(self.session), date(2026, 9, 2), owner) if row["batch_id"] == batch_id)
            self.assertGreaterEqual(row["impairment_amount"], 0)
            self.assertEqual(row["opening_impairment_amount"] + row["monthly_total_impairment_amount"]
                             - row["monthly_writeoff_amount"], row["impairment_amount"])
            book, cache = _build_inventory_impairment_workbook([row], date(2026, 9, 2))
            self.assertEqual(book.active["O2"].value, row["impairment_amount"])
            sheet = load_workbook(_workbook_bytes_with_formula_cache(book, cache), data_only=True).active
            self.assertGreaterEqual(sheet["O2"].value, 0)
            self.assertGreaterEqual(sheet["P2"].value, 0)
            reserves.append(row["impairment_amount"])
            books.append(row["inventory_amount"] - row["impairment_amount"])
        self.assertEqual(sum(reserves), Decimal("0.01"))
        self.assertEqual(sum(books), Decimal("0.99"))

    def test_money_tail_and_unknown_are_preserved(self):
        stores = [{"id": 7}, {"id": 8}, {"id": 9}]
        for value in (Decimal("0.01"), Decimal("100.1234"), Decimal("-0.01")):
            self.assertEqual(sum(allocated_amount(value, stores, owner) for owner in (7, 8, 9)), value)
        self.assertIsNone(allocated_amount(None, stores, 7))
        self.assertEqual(allocated_amount(None, stores, 99), 0)

    async def report_row(self, owner, day=date(2026, 9, 30)):
        rows = await _load_inventory_impairment_report_rows(InventoryReadSession(self.session), day, owner)
        return next((row for row in rows if row["batch_id"] == 1), None)

    def assert_rollforward(self, row):
        self.assertEqual(row["opening_impairment_amount"] + row["monthly_total_impairment_amount"]
                         - row["monthly_writeoff_amount"] + row["monthly_transfer_in_amount"]
                         - row["monthly_transfer_out_amount"], row["impairment_amount"])

    async def test_change_date_preserves_expense_and_transfers_unsold_reserve(self):
        await self.save([7])
        old = await self.report_row(7, date(2026, 9, 14))
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            response = await self.save([8], mode="from_change_date")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r["effective_date"] for r in response.json()["impairment_store_history"]],
                         ["2026-09-01", "2026-09-15"])
        self.assertEqual(await self.report_row(7, date(2026, 9, 14)), old)
        a, b, total = await self.report_row(7), await self.report_row(8), await self.report_row(None)
        self.assertEqual((a["monthly_total_impairment_amount"], a["monthly_writeoff_amount"],
                          a["monthly_transfer_out_amount"], a["impairment_amount"]), (98, 7, 91, 0))
        self.assertEqual((b["monthly_total_impairment_amount"], b["monthly_transfer_in_amount"],
                          b["impairment_amount"]), (112, 91, 203))
        for row in (a, b, total): self.assert_rollforward(row)
        for key in ("monthly_total_impairment_amount", "monthly_writeoff_amount", "impairment_amount",
                    "monthly_transfer_in_amount", "monthly_transfer_out_amount"):
            self.assertEqual(a[key] + b[key], total[key])
        for owner, expense in ((7, 98), (8, 112)):
            values = await _store_provision_financial_values(InventoryReadSession(self.session), owner,
                                                            _month_periods(date(2026, 9, 1)))
            self.assertEqual(values["inventory_impairment"]["current"], expense)

    async def test_change_day_sale_accrues_and_writes_off_for_new_owner(self):
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            await self.save([8], mode="from_change_date")
        self.add_sale(99, 1, 10, datetime(2026, 9, 15), 7)  # 归属不限制跨店销售。
        self.session.get(InventoryBatch, 1).remaining_quantity = 60
        self.session.commit()
        old, new = await self.report_row(7), await self.report_row(8)
        self.assertEqual((old["monthly_writeoff_amount"], new["monthly_writeoff_amount"]), (7, 14))
        self.assertEqual((new["monthly_transfer_in_amount"], new["monthly_total_impairment_amount"],
                          new["impairment_amount"]), (91, 97, 174))
        self.assert_rollforward(old)
        self.assert_rollforward(new)

    async def test_same_day_replacement_and_unchanged_save_do_not_duplicate_transfer(self):
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            await self.save([8], mode="from_change_date")
            await self.save([7, 8], mode="from_change_date")
            row = (await self.save([7, 8], mode="from_change_date")).json()
        self.assertEqual(len(row["impairment_store_history"]), 2)
        self.assertEqual(self.session.query(BatchImpairmentRule).filter_by(batch_id=1).count(), 2)
        a, b = await self.report_row(7), await self.report_row(8)
        self.assertEqual((a["monthly_transfer_out_amount"], b["monthly_transfer_in_amount"]),
                         (Decimal("45.5"), Decimal("45.5")))
        self.assert_rollforward(a)
        self.assert_rollforward(b)

    async def test_arrival_mode_replaces_timeline_and_old_client_defaults_to_it(self):
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            await self.save([8], mode="from_change_date")
        # 旧客户端改变归属仍保持历史更正契约。
        response = await self.save([7])
        self.assertEqual(len(response.json()["impairment_store_history"]), 1)
        self.assertIsNone(await self.report_row(8))
        row = await self.report_row(7)
        self.assertEqual((row["monthly_total_impairment_amount"], row["monthly_transfer_in_amount"]), (210, 0))
        self.assert_rollforward(row)

    async def test_old_client_unchanged_selection_keeps_existing_dated_history(self):
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            await self.save([8], mode="from_change_date")
        before = await self.batch()
        after = (await self.save([8])).json()
        self.assertEqual(after["impairment_store_history"], before["impairment_store_history"])
        self.assertEqual(after["edit_version"], before["edit_version"])

    async def test_future_arrival_dated_change_supersedes_pending_baseline(self):
        today, arrival, cutoff = date(2026, 10, 29), date(2026, 11, 1), date(2026, 11, 2)
        for selected in ([7], [7, 8]):
            with self.subTest(selected=selected):
                created = await self.client.post("/batches", json={"product_id": 1, "quantity": 100,
                    "total_amount": "1000", "arrived_at": arrival.isoformat() + "T00:00:00",
                    "impairment_store_ids": [7]})
                self.assertEqual(created.status_code, 200, created.text)
                batch_id = created.json()["id"]
                pending = await self.save([8], mode="from_arrival", batch_id=batch_id)
                self.assertEqual(pending.status_code, 200, pending.text)
                self.assertEqual(pending.json()["impairment_store_history"][0]["effective_date"], arrival.isoformat())
                with patch("services.batch_impairment_history.business_today", return_value=today):
                    changed = await self.save(selected, mode="from_change_date", batch_id=batch_id)
                self.assertEqual(changed.status_code, 200, changed.text)
                current = await self.batch(batch_id)
                self.assertEqual(current["impairment_store_ids"], selected)
                self.assertEqual(current["impairment_store_history"], [{"effective_date": today.isoformat(),
                    "store_ids": selected, "store_names": [f"Store{owner}" for owner in selected], "is_explicit": True}])
                for day, reserve in ((arrival, Decimal(0)), (cutoff, Decimal(10))):
                    for owner in (None, 7, 8):
                        rows = await _load_inventory_impairment_report_rows(InventoryReadSession(self.session), day, owner)
                        row = next((row for row in rows if row["batch_id"] == batch_id), None)
                        if owner is not None and owner not in selected:
                            self.assertIsNone(row)
                            continue
                        share = len(selected) if owner is not None else 1
                        self.assertEqual(row["quantity"], Decimal(100) / share)
                        self.assertEqual(row["impairment_amount"], reserve / share)
                        self.assertEqual(row["monthly_total_impairment_amount"], reserve / share)
                        self.assertEqual((row["monthly_transfer_in_amount"], row["monthly_transfer_out_amount"]), (0, 0))
                        self.assert_rollforward(row)

    async def test_implicit_cross_store_writeoff_is_pending_in_store_rows_and_excel(self):
        from routers.reports import _inventory_status, _summarize_inventory_impairment_rows
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            changed = await self.save([8], mode="from_change_date")
        self.assertEqual(changed.status_code, 200, changed.text)
        for owner in (7, 8):
            with self.subTest(owner=owner):
                row = await self.report_row(owner)
                self.assertIsNone(row["monthly_writeoff_amount"])
                for key in ("monthly_total_impairment_amount", "impairment_amount",
                            "monthly_transfer_in_amount", "monthly_transfer_out_amount"):
                    self.assertIsNotNone(row[key], key)
                self.assertEqual(_inventory_status(row), "历史流水待核对")
                self.assertIsNone(_summarize_inventory_impairment_rows([row])[0]["monthly_writeoff_amount"])
                book, cache = _build_inventory_impairment_workbook([row], date(2026, 9, 30))
                sheet = load_workbook(_workbook_bytes_with_formula_cache(book, cache), data_only=True).active
                self.assertEqual(sheet["S2"].value, "历史流水待核对")
                self.assertIsNone(sheet["W2"].value)
                self.assertIsNone(sheet["W3"].value)

    async def test_known_zero_writeoff_keeps_healthy_inventory_status(self):
        from routers.reports import _inventory_status
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            changed = await self.save([8], mode="from_change_date")
        self.assertEqual(changed.status_code, 200, changed.text)
        old, new = await self.report_row(7), await self.report_row(8)
        self.assertEqual(old["monthly_writeoff_amount"], 7)
        self.assertEqual(new["monthly_writeoff_amount"], 0)
        self.assertEqual(_inventory_status(old), "已售完")
        self.assertEqual(_inventory_status(new), "正常计提")

    async def test_first_dated_change_freezes_creator_binding_and_preserves_ambiguity(self):
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            response = await self.save([8], mode="from_change_date")
        baseline = response.json()["impairment_store_history"][0]
        self.assertEqual((baseline["store_ids"], baseline["is_explicit"]), ([7], False))
        self.session.get(User, 7).store_id = 8
        self.session.commit()
        row = await self.report_row(7, date(2026, 9, 14))
        self.assertEqual(row["impairment_amount"], 91)
        values = await _store_provision_financial_values(InventoryReadSession(self.session), 7,
                                                        _month_periods(date(2026, 9, 1)))
        self.assertIsNone(values["inventory_writeoff"]["current"])  # 原跨店流水不能因新归属被抹掉。

    async def test_first_day_of_month_transfer_keeps_previous_balance_visible(self):
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 10, 1)):
            await self.save([8], mode="from_change_date")
        a, b = await self.report_row(7, date(2026, 10, 1)), await self.report_row(8, date(2026, 10, 1))
        self.assertEqual((a["opening_impairment_amount"], a["monthly_transfer_out_amount"],
                          b["monthly_transfer_in_amount"]), (203, 203, 203))
        self.assert_rollforward(a)
        self.assert_rollforward(b)

    async def test_historical_owner_is_protected_from_deletion(self):
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            await self.save([8], mode="from_change_date")
        response = await self.client.delete("/finance-masters/stores/7")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "store_has_impairment_batches")

    async def test_dated_history_rolls_back_with_failed_audit(self):
        await self.save([7])
        before = await self.batch()
        def fail(*_args): raise RuntimeError("audit failure")
        event.listen(BatchChangeLog, "before_insert", fail)
        try:
            with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
                with self.assertRaisesRegex(RuntimeError, "audit failure"):
                    await self.save([8], mode="from_change_date")
        finally:
            event.remove(BatchChangeLog, "before_insert", fail)
        after = await self.batch()
        self.assertEqual(after["impairment_store_history"], before["impairment_store_history"])
        self.assertEqual(after["edit_version"], before["edit_version"])

    async def test_invalid_mode_is_rejected_without_mutation(self):
        before = await self.batch()
        self.assertEqual((await self.save([8], mode="unknown")).status_code, 422)
        self.assertEqual((await self.batch())["edit_version"], before["edit_version"])

    async def test_history_only_change_invalidates_edit_version(self):
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            await self.save([8], mode="from_change_date")
        before = await self.batch()
        after = (await self.save([8], mode="from_arrival")).json()
        self.assertEqual(before["impairment_store_ids"], after["impairment_store_ids"])
        self.assertNotEqual(before["edit_version"], after["edit_version"])
        self.assertEqual((await self.save([7], version=before["edit_version"], mode="from_change_date")).status_code, 409)
        log = self.session.scalar(select(BatchChangeLog).order_by(BatchChangeLog.id.desc()))
        self.assertEqual(len(log.before_values["impairment_store_history"]), 2)
        self.assertEqual(len(log.after_values["impairment_store_history"]), 1)

    async def test_voided_holding_warning_follows_historical_effective_owners(self):
        from routers.reports import _voided_inventory_holding_counts
        await self.save([7])
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 10, 1)):
            await self.save([8], mode="from_change_date")
        batch = self.session.get(InventoryBatch, 1)
        batch.voided_at = datetime(2026, 10, 14, 16)  # 上海10/15
        self.session.commit()
        periods = {"old": (date(2026, 9, 1), date(2026, 10, 1)),
                   "new": (date(2026, 10, 1), date(2026, 11, 1))}
        for owner, expected in ((7, {"old": 1, "new": 0}), (8, {"old": 0, "new": 1})):
            counts = await _voided_inventory_holding_counts(InventoryReadSession(self.session), owner, periods)
            self.assertEqual(counts, expected)

    async def test_missing_original_owner_marks_transfer_unknown_in_report_and_excel(self):
        from routers.reports import _inventory_status
        self.session.get(User, 7).store_id = None
        self.session.commit()
        with patch("services.batch_impairment_history.business_today", return_value=date(2026, 9, 15)):
            await self.save([8], mode="from_change_date")
        row = await self.report_row(8)
        self.assertIsNone(row["monthly_transfer_in_amount"])
        self.assertIsNone(row["monthly_transfer_out_amount"])
        self.assertIn("准备转移待核对", row["comparison_note"])
        self.assertEqual(_inventory_status(row), "历史流水待核对")
        book, cache = _build_inventory_impairment_workbook([row], date(2026, 9, 30))
        sheet = load_workbook(_workbook_bytes_with_formula_cache(book, cache), data_only=True).active
        for cell in ("X2", "Y2", "X3", "Y3"):
            self.assertIsNone(sheet[cell].value)

    def test_cent_tails_reconcile_months_ytd_and_store_balance(self):
        from datetime import datetime
        stores = [{"id": 7, "name": "A"}, {"id": 8, "name": "B"}]
        batch = AssignedBatch(SimpleNamespace(id=1, unit_cost=Decimal(1)), stores)
        ledger = ProvisionReplay({}, [ProvisionAccrual(1, date(2026, 9, 2), Decimal("0.01")),
            ProvisionAccrual(1, date(2026, 10, 2), Decimal("0.01"))], [ProvisionWriteoff(
                ProvisionSale(1, 1, 1, datetime(2026, 10, 3), 1, 7), Decimal("0.01"))], {})
        for store_id in (7, 8):
            september = allocated_provision_period(ledger, batch, store_id, date(2026, 9, 1), date(2026, 10, 1))
            october = allocated_provision_period(ledger, batch, store_id, date(2026, 10, 1), date(2026, 11, 1))
            ytd = allocated_provision_period(ledger, batch, store_id, date(2026, 1, 1), date(2026, 11, 1))
            self.assertEqual(september + october, ytd)
            written = allocated_provision_period(ledger, batch, store_id, date(2026, 1, 1), date(2026, 11, 1), writeoff=True)
            self.assertEqual(ytd - written, allocated_provision_balance(ledger, batch, store_id, date(2026, 10, 31), Decimal("0.01")))


class ProvisionAllocationBoundaryTests(unittest.TestCase):
    def replay(self, *, cost="1", quantity=1, sold=0, owners=3, arrival=date(2026, 9, 28),
               cutoff=date(2026, 10, 1)):
        stores = [{"id": value, "name": str(value)} for value in range(1, owners + 1)]
        batch = AssignedBatch(SimpleNamespace(id=1, unit_cost=Decimal(cost)), stores)
        layer = InventoryLayer(1, 1, 1, arrival, quantity, "stable", 0)
        sales = [ProvisionSale(1, 1, 1, datetime.combine(cutoff, datetime.min.time()), sold, 1)] if sold else []
        ledger = replay_inventory_provisions([layer], sales, cutoff,
            balance_dates=[cutoff.replace(day=1) - timedelta(days=1)])
        return batch, ledger

    def test_cross_month_accrual_is_nonnegative_and_reconciles_each_store(self):
        batch, ledger = self.replay()
        september_end, october_end = date(2026, 10, 1), date(2026, 10, 2)
        october = []
        for owner in batch.impairment_store_ids:
            opening = allocated_provision_balance(ledger, batch, owner, date(2026, 9, 30), Decimal("0.02"))
            accrued = allocated_provision_period(ledger, batch, owner, september_end, october_end)
            closing = allocated_provision_balance(ledger, batch, owner, date(2026, 10, 1), Decimal("0.03"))
            self.assertGreaterEqual(accrued, 0)
            self.assertEqual(opening + accrued, closing)
            september = allocated_provision_period(ledger, batch, owner, date(2026, 9, 1), september_end)
            ytd = allocated_provision_period(ledger, batch, owner, date(2026, 1, 1), october_end)
            self.assertEqual(september + accrued, ytd)
            october.append(accrued)
        self.assertEqual(sum(october), Decimal("0.01"))

    def test_partial_sale_cannot_leave_a_negative_store_reserve(self):
        batch, ledger = self.replay(quantity=3, sold=2, arrival=date(2026, 9, 1), cutoff=date(2026, 9, 2))
        balances = []
        for owner in batch.impairment_store_ids:
            accrued = allocated_provision_period(ledger, batch, owner, date(2026, 9, 1), date(2026, 9, 3))
            written = allocated_provision_period(ledger, batch, owner, date(2026, 9, 1), date(2026, 9, 3), writeoff=True)
            balance = allocated_provision_balance(ledger, batch, owner, date(2026, 9, 2), Decimal("0.01"))
            self.assertGreaterEqual(balance, 0)
            self.assertGreaterEqual(written, 0)
            self.assertEqual(accrued - written, balance)
            balances.append(balance)
        self.assertEqual(sum(balances), Decimal("0.01"))

    def test_subcent_costs_and_many_owners_keep_conservation_and_nonnegative_book_values(self):
        for owners in (2, 3, 7, 100):
            for cost in ("0.0001", "1", "7.1234"):
                with self.subTest(owners=owners, cost=cost):
                    batch, ledger = self.replay(cost=cost, owners=owners, arrival=date(2026, 7, 1),
                                                cutoff=date(2026, 9, 30))
                    balances, inventory = [], []
                    for owner in batch.impairment_store_ids:
                        balance = allocated_provision_balance(ledger, batch, owner, date(2026, 9, 30), Decimal("0.9"))
                        amount = allocated_batch_amount(batch.unit_cost, batch, owner)
                        self.assertGreaterEqual(balance, 0)
                        self.assertLessEqual(balance, amount)
                        balances.append(balance)
                        inventory.append(amount)
                    self.assertEqual(sum(balances), batch.unit_cost * Decimal("0.9"))
                    self.assertEqual(sum(inventory), batch.unit_cost)

    def test_cross_year_previous_month_and_ytd_reconcile(self):
        batch, ledger = self.replay(arrival=date(2026, 12, 29), cutoff=date(2027, 1, 1))
        for owner in batch.impairment_store_ids:
            previous = allocated_provision_period(ledger, batch, owner, date(2026, 12, 1), date(2027, 1, 1))
            current = allocated_provision_period(ledger, batch, owner, date(2027, 1, 1), date(2027, 2, 1))
            ytd = allocated_provision_period(ledger, batch, owner, date(2027, 1, 1), date(2027, 1, 2))
            combined = allocated_provision_period(ledger, batch, owner, date(2026, 12, 1), date(2027, 2, 1))
            self.assertGreaterEqual(current, 0)
            self.assertEqual(current, ytd)
            self.assertEqual(previous + current, combined)
            self.assertEqual(previous, allocated_provision_balance(ledger, batch, owner, date(2026, 12, 31), Decimal("0.02")))
            self.assertEqual(combined, allocated_provision_balance(ledger, batch, owner, date(2027, 1, 1), Decimal("0.03")))

    def test_unknown_history_does_not_poison_known_later_period_or_unrelated_store(self):
        batch, _ = self.replay()
        ledger = ProvisionReplay({}, [ProvisionAccrual(1, date(2026, 9, 2), None),
            ProvisionAccrual(1, date(2026, 10, 2), Decimal("0.01"))], [], {})
        known = []
        for owner in batch.impairment_store_ids:
            self.assertIsNone(allocated_provision_period(ledger, batch, owner, date(2026, 9, 1), date(2026, 10, 1)))
            self.assertIsNone(allocated_provision_balance(ledger, batch, owner, date(2026, 9, 30), None))
            amount = allocated_provision_period(ledger, batch, owner, date(2026, 10, 1), date(2026, 11, 1))
            self.assertGreaterEqual(amount, 0)
            known.append(amount)
        self.assertEqual(sum(known), Decimal("0.01"))
        self.assertEqual(allocated_provision_balance(ledger, batch, 99, date(2026, 9, 30), None), 0)

    def test_fractional_cent_boundaries_are_monotone_and_preserve_signed_amounts(self):
        stores = [{"id": owner} for owner in (1, 2, 3)]
        previous = [Decimal("-1")] * 3
        for step in range(-400, 401):
            amount = Decimal(step) / 10000
            shares = [allocated_amount(amount, stores, owner) for owner in (1, 2, 3)]
            self.assertEqual(sum(shares), amount)
            for before, after in zip(previous, shares):
                self.assertGreaterEqual(after, before)
            previous = shares

from unittest.mock import patch as _monthly_patch
from tests.report_rate_fixtures import fixed_report_rates
_monthly_rate_patch = _monthly_patch("services.report_exchange_rates.get_cny_rates", new=fixed_report_rates)
def setUpModule():
    _monthly_rate_patch.start()
def tearDownModule():
    _monthly_rate_patch.stop()
