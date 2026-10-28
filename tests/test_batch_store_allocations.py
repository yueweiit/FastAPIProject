"""手工跌价归属：隔离SQLite/ASGI，真实批次路由与报表；不运行main。"""
import unittest
from io import BytesIO
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import event, select
from openpyxl import load_workbook

from models import BatchChangeLog, BatchImpairmentStore, InventoryBatch, Product, Sale, Store
from routers.batches import router as batch_router
from routers.finance_masters import router as master_router
from routers.reports import (_load_inventory_impairment_report_rows, _month_periods,
                             _product_line_profit_data, _store_provision_financial_values,
                             _build_inventory_impairment_workbook, _workbook_bytes_with_formula_cache)
from services.batch_store_allocations import (AssignedBatch, allocated_amount, allocated_batch_amount, allocated_provision_period,
                                             allocated_provision_balance)
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

    async def batch(self):
        response = await self.client.get("/batches")
        self.assertEqual(response.status_code, 200, response.text)
        return next(row for row in response.json() if row["id"] == 1)

    async def save(self, ids=None, *, omit=False, version=None):
        row = await self.batch()
        payload = {key: row[key] for key in ("product_id", "quantity", "total_amount", "shipping_cost",
                    "last_mile_cost", "other_cost", "arrived_at", "shipping_date")}
        payload["edit_version"] = version or row["edit_version"]
        if not omit:
            payload["impairment_store_ids"] = ids
        return await self.client.put("/batches/1", json=payload)

    async def test_create_many_stores_and_preserve_single_stock_quantity(self):
        response = await self.client.post("/batches", json={"product_id": 1, "quantity": 100,
            "total_amount": "1000", "impairment_store_ids": [8, 7, 8]})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        self.assertEqual(row["impairment_store_ids"], [7, 8])
        self.assertEqual(row["impairment_store_names"], ["Store7", "Store8"])
        self.assertEqual((row["quantity"], row["remaining_quantity"]), (100, 100))

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
        self.assertEqual((await self.save([])).json()["impairment_store_ids"], [])

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
        for owner, expected in ((7, Decimal("196.5")), (8, Decimal("96.5"))):
            rows = await _product_line_profit_data(db, {owner}, date(2026, 9, 1), date(2026, 9, 30))
            self.assertEqual(sum(row["current"]["cost"] for row in rows), expected)

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
