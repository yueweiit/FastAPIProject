"""隔离SQLite验证财务事实查询和跨入口勾稽，不运行应用生命周期。"""

import unittest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from database import Base
from models import (InventoryBatch, Product, Sale, SaleCostDetail, SalesImportBatch,
                    SettlementEntry, SettlementEntryAllocation, Store, StoreProfitLossReport, User)
from routers.reports import (_build_product_line_profit_sheet, _build_store_profit_loss_summary_sheet,
                             _build_store_profit_loss_values, _product_line_profit_data,
                             _store_profit_loss_auto_values, _store_profit_loss_response, STORE_PROFIT_LOSS_AUTO_KEYS,
                             STORE_PROFIT_LOSS_MANUAL_KEYS)
from routers.sales import list_sales, monthly_sales_summary
from services.financial_facts import (derive_manual_periods, manual_period_values,
                                     unlinked_settlement_events, valid_rate)


class AsyncFacade:
    def __init__(self, session):
        self.session = session
        self.info = session.info

    async def execute(self, statement):
        return self.session.execute(statement)

    async def scalar(self, statement):
        return self.session.scalar(statement)


class FinancialFactsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.db = AsyncFacade(self.session)
        self.session.add_all([
            Store(id=i, platform="test", platform_store_id=str(i), name=f"S{i}",
                  country_or_region="CN", settlement_currency="CNY") for i in (7, 8)
        ])
        self.session.flush()
        self.session.add_all([
            User(id=1, username="test", password_hash="test-only", role="operator", store_id=7),
            Product(id=10, sku="P10", name="测试商品"),
        ])
        self.session.flush()
        self.session.add(InventoryBatch(id=1, product_id=10, batch_no="TEST", quantity=10,
                                       remaining_quantity=6, unit_cost=10, purchase_price=10,
                                       user_id=1, arrived_at=datetime(2026, 9, 1)))
        self.session.add(SalesImportBatch(id=1, user_id=1, store_id=7, file_name="test.xlsx", file_type="order_detail"))
        self.session.flush()
        self.session.add(Sale(id=1, product_id=10, user_id=1, store_id=7, order_no="SALE",
                              order_claim="SALE", quantity=4, selling_price=25, total_cost=40,
                              platform_fee=10, profit=50, sold_at=datetime(2026, 9, 10)))
        self.session.flush()
        self.session.add(SaleCostDetail(sale_id=1, batch_id=1, quantity=4, unit_cost=10))
        self.entry(1, net=100, total=90, quantity=4)
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=1, product_id=10,
                                                   sale_id=1, component_name="P10", quantity=4))
        self.entry(2, net=-20, total=-18, quantity=-1, occurred=datetime(2026, 9, 20))
        self.entry(3, net=0, total=5, quantity=0, kind="物流赔付", product=None)
        self.entry(4, net=500, total=400, quantity=2, pending=True)  # 未出库的普通订单不作为调整入账
        self.entry(5, net=-900, total=-800, quantity=-1, store=8)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def entry(self, key, *, net, total, quantity, kind="订单", store=7, product=10,
              occurred=datetime(2026, 9, 12), pending=False, currency="CNY", rate=1):
        row = SettlementEntry(id=key, source_key=f"test-{key}", source_row_number=key,
                              order_id=f"ORDER-{key}", platform_sku_id="P10" if product else "-",
                              product_id=product, store_id=store, import_batch_id=1,
                              net_product_sales=Decimal(net), settlement_total=Decimal(total),
                              quantity=quantity, transaction_type=kind, currency=currency,
                              exchange_rate_to_cny=rate, settlement_date=occurred,
                              order_created_at=datetime(2026, 8, 1),
                              mapping_status="pending_confirmation" if pending else "confirmed", raw_data={})
        self.session.add(row)
        return row

    def zero_provisions(self):
        return {key: {p: Decimal(0) for p in ("current", "previous", "ytd")}
                for key in ("inventory_impairment", "inventory_writeoff")}

    async def auto_values(self):
        with patch("routers.reports._store_provision_financial_values", new=AsyncMock(return_value=self.zero_provisions())), \
             patch("routers.reports.office_space_totals_by_application_date", new=AsyncMock(return_value={})), \
             patch("routers.reports.shared_admin_totals_by_application_date", new=AsyncMock(return_value={})):
            return await _store_profit_loss_auto_values(self.db, 7, date(2026, 9, 1))

    async def test_unlinked_events_exclude_linked_sale_pending_orders_and_other_stores(self):
        events = await unlinked_settlement_events(self.db, {7}, date(2026, 9, 1), date(2026, 10, 1))
        self.assertEqual([e.entry_id for e in events], [3, 2])
        self.assertEqual(sum(e.revenue for e in events), Decimal(-20))
        self.assertEqual(sum(e.expense for e in events), Decimal(3))
        self.assertEqual(await unlinked_settlement_events(self.db, {7}, date(2026, 8, 1), date(2026, 9, 1)), [])
        product_events = await unlinked_settlement_events(self.db, {7}, None, None, product_id=10)
        self.assertEqual([e.entry_id for e in product_events], [2])

    async def test_sales_summary_and_profit_loss_reconcile_refunds_compensation_once(self):
        summary = await monthly_sales_summary(None, None, "2026-09", self.db,
                                             SimpleNamespace(role="operator", store_id=7))
        row = summary[0]
        self.assertEqual(row.sales_count, 1)
        self.assertEqual(row.sold_quantity, 4)
        self.assertEqual(row.settlement_event_count, 2)
        self.assertEqual(row.sales_revenue_cny, Decimal(80))
        self.assertEqual(row.platform_fee_cny, Decimal(13))
        self.assertEqual(row.gross_profit_cny, Decimal(27))
        values = _build_store_profit_loss_values(await self.auto_values(), {})
        self.assertEqual(values["net_profit"]["current"], row.gross_profit_cny)
        # 报表查询没有恢复退款库存、删除订单或重写保存成本。
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 6)
        self.assertEqual(self.session.get(Sale, 1).total_cost, Decimal(40))

    async def test_paged_and_unpaged_list_include_same_events_and_keep_cost_details(self):
        user = SimpleNamespace(role="operator", store_id=7)
        full = await list_sales(None, None, "2026-09", None, 1, self.db, user)
        pages = [await list_sales(None, None, "2026-09", p, 1, self.db, user) for p in (1, 2, 3)]
        self.assertEqual([p.items[0].id for p in pages], [x.id for x in full])
        self.assertTrue(all(p.total == 3 for p in pages))
        original = next(x for x in full if x.id == 1)
        self.assertEqual(original.cost_details[0].batch_no, "TEST")
        self.assertEqual(sum(x.profit_cny for x in full), Decimal(27))

    async def test_missing_fx_masks_income_cost_profit_and_cross_store_excel_totals(self):
        self.entry(6, net=-5, total=-4, quantity=-1, currency="USD", rate=None)
        self.session.commit()
        values = _build_store_profit_loss_values(await self.auto_values(), {})
        for key in ("product_sales_revenue", "other_direct_cost", "operating_revenue",
                    "operating_cost", "gross_profit", "net_profit"):
            self.assertIsNone(values[key]["current"], key)
        self.assertEqual(values["product_purchase_cost"]["current"], Decimal(40))
        self.assertTrue(any("汇率" in text for text in values["_warnings"]))
        summary = await monthly_sales_summary(None, None, "2026-09", self.db,
                                             SimpleNamespace(role="operator", store_id=7))
        self.assertIsNone(summary[0].gross_profit_cny)
        self.assertEqual(summary[0].fx_missing_count, 1)
        wb = Workbook()
        _build_store_profit_loss_summary_sheet(wb, [(self.session.get(Store, 7), values)], date(2026, 9, 1))
        sheet = wb.worksheets[-1]
        profit = next(row for row in sheet.iter_rows() if row[0].value == "三、净利润")
        self.assertIsNone(profit[1].value)
        self.assertIsNone(profit[2].value)

    async def test_product_line_and_excel_keep_unknown_revenue_blank(self):
        self.entry(6, net=-5, total=-4, quantity=-1, currency="USD", rate=None)
        self.session.commit()
        with patch("routers.reports._sale_provision_adjustments", new=AsyncMock(return_value={1: (Decimal(0), "")})):
            rows = await _product_line_profit_data(self.db, {7}, date(2026, 9, 1), date(2026, 9, 30))
        self.assertTrue(any(row["current"]["revenue"] is None for row in rows))
        wb = Workbook()
        _build_product_line_profit_sheet(wb, rows, date(2026, 9, 1))
        total = next(row for row in wb.worksheets[-1].iter_rows() if row[0].value == "合计")
        self.assertIsNone(total[2].value)
        self.assertIsNone(total[4].value)

    async def test_product_line_adjustments_match_store_profit_without_inventory_returns(self):
        with patch("routers.reports._sale_provision_adjustments", new=AsyncMock(return_value={1: (Decimal(0), "")})):
            rows = await _product_line_profit_data(self.db, {7}, date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(sum(r["current"]["revenue"] for r in rows), Decimal(80))
        self.assertEqual(sum(r["current"]["cost"] for r in rows), Decimal(53))

    async def test_actual_month_rows_derive_previous_and_ytd_without_overwriting_legacy(self):
        self.session.add_all([
            StoreProfitLossReport(store_id=7, report_month=date(2026, 1, 1), manual_values={"advertising": {"current": "10"}}),
            StoreProfitLossReport(store_id=7, report_month=date(2026, 8, 1), manual_values={"advertising": {"current": "20"}}),
            StoreProfitLossReport(store_id=7, report_month=date(2026, 9, 1),
                                  manual_values={"advertising": {"current": "100", "ytd": "999"}},
                                  report_metadata={"manual_period_mode": "monthly"}),
        ])
        self.session.commit()
        values = await manual_period_values(self.db, 7, date(2026, 9, 1), STORE_PROFIT_LOSS_MANUAL_KEYS)
        self.assertEqual(values["advertising"]["previous"], "20")
        self.assertEqual(values["advertising"]["ytd"], Decimal(130))
        raw = self.session.query(StoreProfitLossReport).filter_by(store_id=7, report_month=date(2026, 9, 1)).one()
        self.assertEqual(raw.manual_values["advertising"]["ytd"], "999")
        with patch("routers.reports._store_profit_loss_auto_values", new=AsyncMock(return_value=await self.auto_values())):
            response = await _store_profit_loss_response(
                self.db, SimpleNamespace(role="admin"), self.session.get(Store, 7), date(2026, 9, 1), raw,
            )
        advertising = next(row for row in response.rows if row.key == "advertising")
        self.assertEqual(advertising.value.current, Decimal(100))
        self.assertEqual(advertising.value.ytd, Decimal(130))
        self.assertEqual(advertising.manual_value.ytd, Decimal(999))


class ManualPeriodTests(unittest.TestCase):
    def row(self, month, values, monthly=True):
        return SimpleNamespace(report_month=month, manual_values=values,
                               report_metadata={"manual_period_mode": "monthly" if monthly else "legacy"})

    def test_current_expense_reduces_both_current_and_ytd_profit(self):
        month = date(2026, 9, 1)
        manual = derive_manual_periods([self.row(month, {"advertising": {"current": "100"}})], month, {"advertising"})
        auto = {k: {p: Decimal(0) for p in ("current", "previous", "ytd")} for k in STORE_PROFIT_LOSS_AUTO_KEYS}
        auto["product_sales_revenue"] = {"current": Decimal(1000), "previous": Decimal(0), "ytd": Decimal(3000)}
        values = _build_store_profit_loss_values(auto, manual)
        self.assertEqual(values["net_profit"]["current"], Decimal(900))
        self.assertEqual(values["net_profit"]["ytd"], Decimal(2900))

    def test_january_comparison_is_december_but_ytd_does_not_include_prior_year(self):
        month = date(2026, 1, 1)
        rows = [self.row(date(2025, 12, 1), {"salary": {"current": "500"}}),
                self.row(month, {"salary": {"current": "100"}})]
        result = derive_manual_periods(rows, month, {"salary"})
        self.assertEqual(result["salary"], {"current": "100", "previous": "500", "ytd": Decimal(100)})

    def test_oa_months_use_saved_override_only_for_that_month(self):
        month = date(2026, 9, 1)
        rows = [self.row(month, {"salary": {"current": "120"}})]
        result = derive_manual_periods(rows, month, {"salary"},
                                      {"salary": {"2026-01-01": Decimal(80), "2026-09-01": Decimal(100)}})
        self.assertEqual(result["salary"]["ytd"], Decimal(200))

    def test_legacy_all_mode_keeps_explicit_comparison_overrides_and_zero_is_valid(self):
        month = date(2026, 9, 1)
        rows = [self.row(month, {"advertising": {"current": "0", "previous": "20", "ytd": "999"}}, False)]
        self.assertEqual(derive_manual_periods(rows, month, {"advertising"})["advertising"]["ytd"], "999")
        rows[0].report_metadata["manual_period_mode"] = "monthly"
        self.assertEqual(derive_manual_periods(rows, month, {"advertising"})["advertising"]["ytd"], Decimal(0))

    def test_invalid_foreign_rates_are_unknown(self):
        for value in (None, 0, -1, Decimal("NaN")):
            self.assertIsNone(valid_rate("USD", value))
        self.assertEqual(valid_rate("CNY", None), Decimal(1))


if __name__ == "__main__":
    unittest.main()
