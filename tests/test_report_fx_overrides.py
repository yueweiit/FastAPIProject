"""Manual report FX: real routes and SQLite, with external rates/OA isolated."""
import copy
import os
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from openpyxl import Workbook, load_workbook
from sqlalchemy import event, select

from auth import get_current_user
from database import get_db
from models import BusinessOperationLog, Store, StoreProfitLossReport, Sale, SaleCostDetail, SettlementEntry, SettlementEntryAllocation
from routers import reports
from services.exchange_rates import ExchangeRateError
from services.report_exchange_rates import monthly_report_rates, monthly_store_report_rates, rate_evidence_text, FX_OVERRIDE_KEY
from tests import test_monthly_profit_policy as policy
from tests.test_sales_import_store import Facade


class ReportFxOverridesTests(unittest.IsolatedAsyncioTestCase):
    entry = policy.MonthlyProfitPolicyTests.entry
    tearDown = policy.MonthlyProfitPolicyTests.tearDown
    auto = policy.MonthlyProfitPolicyTests.auto
    product_lines = policy.MonthlyProfitPolicyTests.product_lines

    def setUp(self):
        policy.MonthlyProfitPolicyTests.setUp(self)
        self.db = Facade(self.session)
        self.actor = NS(id=1, role="admin", store_id=7, username="test")
        for item in (patch.dict(os.environ, {"FIFO_OPERATION_AUDIT_ENABLED": "0"}),
                     patch("services.report_exchange_rates.report_today", return_value=date(2026, 11, 25))):
            item.start(); self.addCleanup(item.stop)
        self.app = FastAPI()
        self.app.include_router(reports.router)
        self.app.dependency_overrides[get_current_user] = lambda: self.actor
        self.app.dependency_overrides[get_db] = lambda: self.db

    async def request(self, method, url, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test") as client:
            return await client.request(method, url, **kwargs)

    async def put_rate(self, **changes):
        body = {"store_id": 7, "report_month": "2026-09-01", "currency": "USD",
                "mode": "manual", "manual_rate": "0.3711", "note": "按结算单", "expected_revision": 0}
        body.update(changes)
        return await self.request("PUT", "/reports/store-exchange-rate", json=body)

    async def saved(self, **changes):
        response = await self.put_rate(**changes)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["setting"]

    async def test_manual_rates_drive_report_without_fetching_and_preserve_saved_sales(self):
        await self.saved()
        await self.saved(currency="MXN", manual_rate="0.4")
        self.db.info.clear()
        with patch("services.report_exchange_rates.get_cny_rates", new=AsyncMock(side_effect=AssertionError("manual rates need no network"))):
            values = await self.auto()
        self.assertEqual(values["product_sales_revenue"]["current"], D("37.11"))
        self.assertEqual(values["other_direct_cost"]["current"], D("3.711"))
        self.assertEqual(values["fulfillment_cost"]["current"], D("3.2"))
        self.assertEqual(values["product_purchase_cost"]["current"], D(40))
        self.assertEqual(self.session.get(SettlementEntry, 1).exchange_rate_to_cny, D(".01"))

    async def test_store_and_currency_isolation_in_one_request_cache(self):
        await self.saved(manual_rate=".37")
        await self.saved(store_id=8, manual_rate=".51")
        rates = await monthly_store_report_rates(self.db, {(7, "USD", date(2026, 9, 1)),
            (8, "USD", date(2026, 9, 1)), (7, "MXN", date(2026, 9, 1))})
        self.assertEqual(rates[7, "USD", date(2026, 9, 1)]["rate_to_cny"], D(".37"))
        self.assertEqual(rates[8, "USD", date(2026, 9, 1)]["rate_to_cny"], D(".51"))
        self.assertEqual(rates[7, "MXN", date(2026, 9, 1)]["rate_to_cny"], D(".3"))

    async def test_ytd_uses_each_months_own_rate(self):
        self.entry(6, net=50, total=40, quantity=0, currency="USD", occurred=datetime(2026, 8, 20))
        self.session.commit()
        await self.saved(report_month="2026-08-01", manual_rate=".2")
        await self.saved(manual_rate=".4")
        values = await self.auto()
        self.assertEqual(values["product_sales_revenue"], {"current": D(40), "previous": D(10), "ytd": D(50)})

    async def test_product_profit_aggregates_store_specific_sales_and_fulfillment(self):
        self.session.add(Sale(id=2, product_id=10, user_id=1, store_id=8, order_no="S8", order_claim="S8",
            quantity=1, selling_price=50, total_cost=10, platform_fee=5, profit=35, sold_at=datetime(2026, 9, 12)))
        self.session.flush()
        self.session.add(SaleCostDetail(sale_id=2, batch_id=1, quantity=1, unit_cost=10))
        entry = self.entry(6, net=50, total=45, quantity=1, currency="USD", store=8)
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=entry.id, product_id=10, sale_id=2, component_name="P10", quantity=1))
        self.session.commit()
        await self.saved(manual_rate=".4")
        await self.saved(store_id=8, manual_rate=".6")
        await self.saved(currency="MXN", manual_rate=".35")
        await self.saved(store_id=8, currency="MXN", manual_rate=".5")
        with patch("routers.reports._sale_provision_adjustments", new=AsyncMock(return_value={1:(D(0),""),2:(D(0),"")})), \
             patch("routers.reports._load_inventory_provision_data", new=AsyncMock(return_value=([],[],None))):
            rows = await reports._product_line_profit_data(self.db, {7,8}, date(2026,9,1), date(2026,9,30))
        self.assertEqual(sum(row["current"]["revenue"] for row in rows), D(70))
        self.assertEqual(sum(row["current"]["cost"] for row in rows), D("63.8"))  # 50 FIFO + 7 platform + 6.8 fulfillment

    async def test_restore_auto_keeps_revision_and_clears_effective_cache(self):
        await self.saved()
        rates = await monthly_report_rates(self.db, {("USD", date(2026,9,1))}, store_id=7)
        self.assertEqual(rates["USD",date(2026,9,1)]["mode"], "manual")
        setting = await self.saved(mode="auto", manual_rate=None, expected_revision=1)
        self.assertEqual(setting["revision"], 2)
        self.assertIsNone(setting["manual_rate"])
        rates = await monthly_report_rates(self.db, {("USD", date(2026,9,1))}, store_id=7)
        self.assertEqual(rates["USD",date(2026,9,1)]["rate_to_cny"], D(".5"))
        self.assertEqual(rates["USD",date(2026,9,1)]["mode"], "auto")

    async def test_offline_auto_restore_becomes_pending_and_does_not_reuse_manual(self):
        await self.saved()
        await self.saved(mode="auto", manual_rate=None, expected_revision=1)
        with patch("services.report_exchange_rates.get_cny_rates", new=AsyncMock(side_effect=ExchangeRateError("offline"))):
            rates = await monthly_report_rates(self.db, {("USD", date(2026,9,1))}, store_id=7)
        self.assertIsNone(rates["USD",date(2026,9,1)]["rate_to_cny"])

    async def test_stale_revision_is_rejected_without_overwriting_saved_value(self):
        first = await self.saved()
        for changes in ({"manual_rate":".9"}, {"mode":"auto", "manual_rate":None}):
            response = await self.put_rate(**changes)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "report_fx_conflict")
        row = self.session.scalar(select(StoreProfitLossReport))
        self.assertEqual(row.report_metadata[FX_OVERRIDE_KEY]["USD"]["manual_rate"], first["manual_rate"])

    async def test_rate_updates_and_expense_updates_preserve_each_others_fields(self):
        row = StoreProfitLossReport(store_id=7, report_month=date(2026,9,1),
            manual_values={"salary":{"current":"123"}}, remarks={"salary":"草稿外的已保存说明"},
            report_metadata={"other":"keep", "manual_period_mode":"monthly"})
        self.session.add(row); self.session.commit()
        await self.saved()
        self.assertEqual(row.manual_values["salary"]["current"], "123")
        self.assertEqual(row.remarks["salary"], "草稿外的已保存说明")
        response = await self.request("PUT", "/reports/store-profit-loss", json={"store_id":7,
            "report_month":"2026-09-01", "update_scope":"current", "manual_values":{"salary":{"current":"456"}}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(row.report_metadata[FX_OVERRIDE_KEY]["USD"]["revision"], 1)
        self.assertEqual(row.report_metadata["other"], "keep")

    async def test_month_and_amount_validation(self):
        for rate in ("0", "-1", "NaN", "Infinity", ".123456789", "1000000000000000000", True):
            with self.subTest(rate=rate): self.assertEqual((await self.put_rate(manual_rate=rate)).status_code, 422)
        for changes in ({"currency":"CNY"}, {"currency":"mxn"}, {"mode":"manual","manual_rate":None}, {"mode":"auto"}):
            with self.subTest(changes=changes): self.assertEqual((await self.put_rate(**changes)).status_code, 422)
        for month in ("2026-09-02", "2026-12-01"):
            self.assertEqual((await self.put_rate(report_month=month)).status_code, 400)
            response = await self.request("GET", f"/reports/store-exchange-rate/reference?store_id=7&report_month={month}&currency=USD")
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.session.scalars(select(StoreProfitLossReport)).all(), [])

    async def test_smallest_eight_decimal_rate_is_saved_as_editable_decimal_text(self):
        setting = await self.saved(manual_rate="0.00000001")
        self.assertEqual(setting["manual_rate"], "0.00000001")

    async def test_accepted_decimal_rates_round_trip_as_editable_text(self):
        cases = [("0.371100000", "0.3711"), ("0.000000010000", "0.00000001"),
                 ("1000.000000000", "1000"), ("0.12345678", "0.12345678")]
        for revision, (value, expected) in enumerate(cases):
            with self.subTest(value=value):
                setting = await self.saved(manual_rate=value, expected_revision=revision)
                self.assertEqual(setting["manual_rate"], expected)
                self.assertEqual(D(setting["manual_rate"]), D(value))
                row = self.session.scalar(select(StoreProfitLossReport))
                self.assertEqual(row.report_metadata[FX_OVERRIDE_KEY]["USD"]["manual_rate"], expected)
                response = await self.request("GET", "/reports/store-profit-loss?store_id=7&report_month=2026-09-01")
                echoed = next(item for item in response.json()["exchange_rate_settings"] if item["currency"] == "USD")
                self.assertEqual(echoed["manual_rate"], expected)
                self.assertRegex(echoed["manual_rate"], r"^\d+(?:\.\d{1,8})?$")

    async def test_old_trailing_zero_setting_is_readable_without_rewriting_stored_metadata(self):
        row = StoreProfitLossReport(store_id=7, report_month=date(2026, 9, 1), manual_values={}, remarks={},
            report_metadata={FX_OVERRIDE_KEY: {"USD": {"mode": "manual", "manual_rate": "0.371100000", "revision": 1}}})
        self.session.add(row); self.session.commit()
        response = await self.request("GET", "/reports/store-profit-loss?store_id=7&report_month=2026-09-01")
        setting = next(item for item in response.json()["exchange_rate_settings"] if item["currency"] == "USD")
        self.assertEqual(setting["manual_rate"], "0.3711")
        self.session.refresh(row)
        self.assertEqual(row.report_metadata[FX_OVERRIDE_KEY]["USD"]["manual_rate"], "0.371100000")
        updated = await self.saved(manual_rate=setting["manual_rate"], expected_revision=1, note="仅更正说明")
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["manual_rate"], "0.3711")
        self.assertEqual(updated["note"], "仅更正说明")

    async def test_permissions_and_cross_store_reference(self):
        self.actor.role = "viewer"
        self.assertEqual((await self.put_rate()).status_code, 403)
        self.actor.role = "operator"
        self.assertEqual((await self.put_rate(store_id=8)).status_code, 403)
        response = await self.request("GET", "/reports/store-exchange-rate/reference?store_id=8&report_month=2026-09-01&currency=USD")
        self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.put_rate()).status_code, 200)
        self.actor.store_id = None
        self.assertEqual((await self.put_rate(expected_revision=1)).status_code, 403)

    async def test_current_month_is_provisional_and_reference_remains_automatic(self):
        await self.saved(report_month="2026-11-01", manual_rate=".4")
        rates = await monthly_report_rates(self.db, {("USD",date(2026,11,1))}, store_id=7)
        rate = rates["USD",date(2026,11,1)]
        self.assertTrue(rate["is_provisional"])
        self.assertIsNone(rate["rate_date"])
        response = await self.request("GET", "/reports/store-exchange-rate/reference?store_id=7&report_month=2026-11-01&currency=USD")
        self.assertEqual(response.json()["status"], "month_not_closed")

    async def test_manual_rate_stays_provisional_through_month_end_and_changes_next_month(self):
        for month, final_day, next_day in ((date(2026, 9, 1), date(2026, 9, 30), date(2026, 10, 1)),
                                          (date(2024, 2, 1), date(2024, 2, 29), date(2024, 3, 1)),
                                          (date(2025, 12, 1), date(2025, 12, 31), date(2026, 1, 1))):
            await self.saved(report_month=month.isoformat(), manual_rate=".4")
            for today, provisional in ((final_day, True), (next_day, False)):
                with self.subTest(month=month, today=today), \
                     patch("services.report_exchange_rates.report_today", return_value=today):
                    rates = await monthly_report_rates(self.db, {("USD", month)}, store_id=7)
                    rate = rates["USD", month]
                    self.assertEqual(rate["is_provisional"], provisional)
                    self.assertEqual(rate["rate_to_cny"], D(".4"))
                    self.assertEqual("暂估" in rate_evidence_text(rate), provisional)
                    if month == date(2026, 9, 1):
                        response = await self.request("GET", "/reports/store-profit-loss?store_id=7&report_month=2026-09-01")
                        current = next(item for item in response.json()["exchange_rates"] if item["currency"] == "USD")
                        self.assertEqual(current["is_provisional"], provisional)
                        values = reports._build_store_profit_loss_values(await self.auto(), {})
                        book = Workbook()
                        reports._build_store_profit_loss_sheet(book, self.session.get(Store, 7), month, values, {})
                        evidence = " ".join(str(cell.value or "") for row in book.worksheets[-1] for cell in row)
                        self.assertEqual("手动（暂估）" in evidence, provisional)

    async def test_response_and_excel_expose_manual_source_and_actor(self):
        await self.saved()
        response = await self.request("GET", "/reports/store-profit-loss?store_id=7&report_month=2026-09-01")
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["supports_manual_fx"])
        saved = next(item for item in data["exchange_rate_settings"] if item["currency"]=="USD")
        self.assertEqual(saved["updated_by"], "test")
        self.assertEqual(saved["revision"], 1)
        values = reports._build_store_profit_loss_values(await self.auto(), {})
        book = Workbook()
        reports._build_store_profit_loss_sheet(book, self.session.get(Store,7), date(2026,9,1), values, {})
        evidence = " ".join(str(cell.value or "") for row in book.worksheets[-1] for cell in row)
        self.assertIn("手动", evidence)
        self.assertIn("0.3711", evidence)
        self.assertIn("按结算单", evidence)

    async def test_rate_change_creates_new_version_without_changing_old_payload_or_excel(self):
        from schemas import FinancialVersionRequest
        from services.financial_versions import save_version, version_workbook
        self.db.get_bind = self.session.get_bind
        with patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "1"}), \
             patch("services.financial_versions.today", return_value=date(2026, 11, 25)), \
             patch("routers.reports._load_inventory_impairment_report_rows", new=AsyncMock(return_value=[])), \
             patch("routers.reports._product_line_profit_data", new=AsyncMock(return_value=[])):
            await self.saved(manual_rate=".37")
            first = await save_version(self.db, self.actor, FinancialVersionRequest(
                request_key="fx-version-before", store_id=7, report_month=date(2026, 9, 1)))
            await self.db.commit()
            original = copy.deepcopy(first.payload)
            await self.saved(manual_rate=".4", expected_revision=1)
            second = await save_version(self.db, self.actor, FinancialVersionRequest(
                request_key="fx-version-after", store_id=7, report_month=date(2026, 9, 1)))
            await self.db.commit()
        self.assertEqual((first.version, second.version), (1, 2))
        self.assertNotEqual(first.source_digest, second.source_digest)
        self.session.refresh(first)
        self.assertEqual(first.payload, original)
        for version, expected in ((first, D(37)), (second, D(40))):
            revenue = next(row for row in version.payload["profit_loss"]["rows"] if row["key"] == "product_sales_revenue")
            self.assertEqual(D(revenue["value"]["current"]), expected)
            workbook = load_workbook(version_workbook(version), data_only=True)
            cells = [cell.value for sheet in workbook for row in sheet for cell in row]
            self.assertIn(expected, cells)
            self.assertTrue(any("手动" in str(value) and "按结算单" in str(value) for value in cells))

    async def test_enabled_audit_records_rate_and_restore_and_failure_rolls_back(self):
        with patch.dict(os.environ, {"FIFO_OPERATION_AUDIT_ENABLED": "1"}):
            await self.saved()
            await self.saved(mode="auto", manual_rate=None, expected_revision=1)
            logs = self.session.scalars(select(BusinessOperationLog).order_by(BusinessOperationLog.id)).all()
            self.assertEqual(len(logs), 2)
            self.assertEqual(logs[-1].before_values["report_metadata"][FX_OVERRIDE_KEY]["USD"]["mode"], "manual")
            self.assertEqual(logs[-1].after_values["report_metadata"][FX_OVERRIDE_KEY]["USD"]["mode"], "auto")
            self.assertEqual(logs[-1].actor_username, "test")
            def fail_audit(_connection, _cursor, statement, *_args):
                if statement.startswith("INSERT INTO business_operation_logs"):
                    raise RuntimeError("fixture audit failure")
            event.listen(self.engine, "before_cursor_execute", fail_audit)
            try:
                response = await self.put_rate(manual_rate=".9", expected_revision=2)
                self.assertEqual(response.status_code, 503)
            finally:
                event.remove(self.engine, "before_cursor_execute", fail_audit)
            row = self.session.scalar(select(StoreProfitLossReport))
            self.assertEqual(row.report_metadata[FX_OVERRIDE_KEY]["USD"]["mode"], "auto")
            self.assertEqual(row.report_metadata[FX_OVERRIDE_KEY]["USD"]["revision"], 2)
            self.assertEqual(len(self.session.scalars(select(BusinessOperationLog)).all()), 2)
