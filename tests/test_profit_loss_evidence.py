"""09/10依据与实际SQLite事实核对；不启动main、不访问业务库或OA。"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO
import json
import os
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from openpyxl import Workbook, load_workbook
from sqlalchemy import event, select

from auth import RequireAnyRole
from database import get_db
from models import (FinancialExpense, FinancialExpenseSource, InventoryBatch, Product, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry,
                    SettlementEntryAllocation, Store, StoreProfitLossReport, User)
from routers import reports as reports_api
from services.financial_facts import manual_period_values
from services.financial_versions import collect_payload, version_workbook
from services.profit_loss_evidence import CACHE, EvidenceCache, evidence_page
from services.profit_loss_evidence_export import append_profit_loss_evidence, CALC, DETAIL, GUIDE
from tests import test_financial_facts as facts


class Facade(facts.AsyncFacade):
    async def get(self, model, key):
        return self.session.get(model, key)


class ProfitLossEvidenceTests(unittest.IsolatedAsyncioTestCase):
    entry = facts.FinancialFactsTests.entry
    tearDown = facts.FinancialFactsTests.tearDown

    def setUp(self):
        facts.FinancialFactsTests.setUp(self)
        self.db = Facade(self.session)
        flag = patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "0"})
        flag.start(); self.addCleanup(flag.stop)
        self.oa = {"office": {}, "salaries": {}, "shared_admin_by_store": {}, "shared_admin_unresolved": {}}
        self.user = self.session.get(User, 1)
        self.store = self.session.get(Store, 7)
        self.month = date(2026, 9, 1)
        self.app = FastAPI()
        self.app.include_router(reports_api.router)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[RequireAnyRole] = lambda: self.user
        preparation = patch("routers.reports._prepare_profit_loss_oa_values", new=AsyncMock(side_effect=lambda *a: (2, self.oa)))
        preparation.start(); self.addCleanup(preparation.stop)
        CACHE.items.clear()

    async def report(self):
        saved = self.session.query(StoreProfitLossReport).filter_by(store_id=7, report_month=self.month).one_or_none()
        return await reports_api._store_profit_loss_response(self.db, self.user, self.store, self.month, saved,
            oa_values=self.oa, active_store_count=2)

    async def request(self, path):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://fixture") as client:
            return await client.get(path)

    def cell(self, report, key, period="current"):
        return report.evidence["fields"][key]["cells"][period]

    def business_state(self):
        return {model.__tablename__: [tuple(r) for r in self.session.execute(select(model.__table__).order_by(model.id))]
                for model in (Sale, SaleCostDetail, InventoryBatch, SalesImportBatch, SettlementEntry, SettlementEntryAllocation)}

    async def export_book(self):
        response = await self.request("/reports/financial-attachments/export?report_date=2026-09-30&report_month=2026-09-01")
        self.assertEqual(response.status_code, 200)
        return load_workbook(BytesIO(response.content), data_only=True)

    async def assert_restricted_source(self):
        before = self.business_state()
        queries = []
        def track(_conn, _cursor, statement, *_args):
            queries.append(statement.lstrip().split()[0].upper())
        event.listen(self.engine, "before_cursor_execute", track)
        try:
            response = await self.request("/reports/store-profit-loss?store_id=7&report_month=2026-09-01")
            self.assertEqual(response.status_code, 200)
            report = response.json()
            self.assertTrue(any("来源归属" in w for w in report["warnings"]))
            for key in ("product_sales_revenue", "compensation_revenue", "other_direct_cost"):
                cell = report["evidence"]["fields"][key]["cells"]["current"]
                self.assertEqual(cell["status"], "partial")
                detail = await self.request(f"/reports/store-profit-loss/evidence/{report['evidence']['token']}?field={key}")
                self.assertEqual(detail.status_code, 200)
                row = next(r for r in detail.json()["items"] if r["reference"] == "S1")
                self.assertEqual(row["details"]["原始结算行"], [])
                self.assertEqual(row["scope_status"], "restricted")
                self.assertIn("来源归属", row["details"]["核对提示"])
                self.assertNotIn("PRIVATE_", detail.text)
            self.assertEqual(D(report["evidence"]["fields"]["product_sales_revenue"]["cells"]["current"]["value"]), 80)
            book = await self.export_book()
            text = "\n".join(str(c.value) for sheet in book for row in sheet for c in row)
            self.assertNotIn("PRIVATE_", text)
            self.assertIn("来源归属", text)
            self.assertEqual(book["09-店铺损益表"]["B3"].value, 80)
            self.assertEqual(self.business_state(), before)
            self.assertTrue(set(queries) <= {"SELECT"}, queries)
        finally:
            event.remove(self.engine, "before_cursor_execute", track)

    async def test_cross_store_allocated_sources_are_hidden_in_page_and_excel(self):
        self.session.add(User(id=2, username="source-importer", password_hash="test-only", role="operator", store_id=7))
        entry, batch = self.session.get(SettlementEntry, 1), self.session.get(SalesImportBatch, 1)
        batch.user_id = 2
        entry.source_file, entry.source_sheet, entry.order_id = "PRIVATE_FILE.xlsx", "PRIVATE_SHEET", "PRIVATE_ORDER"
        entry.platform_sku_id = "PRIVATE_SKU"
        # 显式结算归属优先；历史记录依次回退到批次、导入人；缺失也不能推测为本店。
        for owner, batch_owner, importer_owner, role in ((8, 7, 7, "operator"), (None, 8, 7, "viewer"),
                                                        (None, None, 8, "operator"), (None, None, None, "viewer")):
            with self.subTest(owner=owner, batch_owner=batch_owner, importer_owner=importer_owner, role=role):
                entry.store_id, batch.store_id = owner, batch_owner
                self.session.get(User, 2).store_id = importer_owner
                self.user.role = role
                self.session.commit()
                await self.assert_restricted_source()

    async def test_cross_store_legacy_direct_source_is_hidden(self):
        self.session.query(SettlementEntryAllocation).filter_by(sale_id=1).delete()
        entry = self.session.get(SettlementEntry, 1)
        entry.sale_id, entry.store_id, entry.source_file = 1, 8, "PRIVATE_DIRECT.xlsx"
        self.session.commit()
        await self.assert_restricted_source()

    async def test_same_store_source_owner_fallbacks_and_mixed_links(self):
        self.session.add(User(id=2, username="source-importer", password_hash="test-only", role="operator", store_id=8))
        entry, batch = self.session.get(SettlementEntry, 1), self.session.get(SalesImportBatch, 1)
        batch.user_id = 2
        entry.source_file = "allowed.xlsx"
        for direct in (False, True):
            if direct:
                self.session.query(SettlementEntryAllocation).filter_by(sale_id=1).delete()
                entry.sale_id = 1
            for owner, batch_owner, importer_owner in ((7, 8, 8), (None, 7, 8), (None, None, 7)):
                with self.subTest(direct=direct, owner=owner, batch_owner=batch_owner, importer_owner=importer_owner):
                    entry.store_id, batch.store_id = owner, batch_owner
                    self.session.get(User, 2).store_id = importer_owner
                    self.session.commit()
                    report = await self.report()
                    source = next(r for r in report.evidence["sources"]["product_sales_revenue"] if r["reference"] == "S1")
                    self.assertEqual(source["details"]["原始结算行"][0]["文件"], "allowed.xlsx")
                    self.assertEqual(self.cell(report, "product_sales_revenue")["status"], "complete")
        # 同条销售同时有本店和越店来源，只隐藏越店部分，不能丢失可见依据或回退到另一条直接关联。
        entry.sale_id, entry.store_id = None, 7
        self.entry(6, net=20, total=18, quantity=1, store=8).source_file = "PRIVATE_MIXED.xlsx"
        self.session.flush()
        self.session.add_all([SettlementEntryAllocation(settlement_entry_id=i, sale_id=1, product_id=10, component_name="P10", quantity=1) for i in (1,6)])
        self.session.commit()
        self.user.role = "admin"  # 管理员单店依据也遵守该报表的来源范围。
        report = await self.report()
        source = next(r for r in report.evidence["sources"]["product_sales_revenue"] if r["reference"] == "S1")
        self.assertEqual([r["结算行ID"] for r in source["details"]["原始结算行"]], [1])
        self.assertEqual(source["scope_status"], "restricted")
        self.assertNotIn("PRIVATE_MIXED", json.dumps(report.evidence))
        self.assertEqual(D(self.cell(report, "product_sales_revenue")["value"]), 100)

    async def test_amounts_reconcile_to_sources_and_queries_do_not_write(self):
        original = [(s.id, s.quantity, s.total_cost) for s in self.session.query(Sale)]
        remaining = self.session.get(InventoryBatch, 1).remaining_quantity
        report = await self.report()
        for key, field in report.evidence["fields"].items():
            for period, cell in field["cells"].items():
                if cell["status"] == "complete" and field["kind"] != "formula":
                    self.assertAlmostEqual(D(cell["value"]), D(cell["source_total"]), places=8, msg=f"{key}/{period}")
                self.assertNotEqual(cell["status"], "partial", f"{key}/{period}")
        self.assertEqual(D(self.cell(report, "product_sales_revenue")["value"]), 80)
        self.assertEqual(self.cell(report, "product_purchase_cost")["source_total"], "40.0000")
        sources = report.evidence["sources"]["product_sales_revenue"]
        sale = next(r for r in sources if r["source"] == "sale")
        self.assertEqual(sale["details"]["原始结算行"][0]["原文件行号"], 1)
        self.assertFalse(any(r["reference"] == "E5" for r in sources))
        self.assertEqual([(s.id, s.quantity, s.total_cost) for s in self.session.query(Sale)], original)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, remaining)
        self.assertFalse(self.session.new or self.session.dirty or self.session.deleted)

    async def test_bundle_references_preserve_original_line_without_double_income(self):
        self.session.add(Product(id=11, sku="PART", name="组合组件")); self.session.flush()
        self.session.add(Sale(id=2, product_id=11, user_id=1, store_id=7, order_no="PART", quantity=1,
            selling_price=100, platform_fee=0, total_cost=0, profit=100, sold_at=datetime(2026, 9, 10)))
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=1, sale_id=2, product_id=11, component_name="part", quantity=1))
        self.session.commit()
        report = await self.report()
        parts = [r for r in report.evidence["sources"]["product_sales_revenue"] if r["source"] == "sale"]
        self.assertEqual(len(parts), 2)
        self.assertEqual(sum(D(r["amount"]) for r in parts), D(100))
        self.assertEqual({r["details"]["原始结算行"][0]["结算行ID"] for r in parts}, {1})
        fulfillment = evidence_page(report.evidence, "fulfillment_cost", "current", 1, 50)
        self.assertEqual(len({r["reference"] for r in fulfillment["items"]}), fulfillment["total"])

    async def test_monthly_fx_and_manual_zero_override_ytd_oa_not_double_counted(self):
        self.session.get(SettlementEntry, 1).currency = "USD"
        self.session.get(SettlementEntry, 1).exchange_rate_to_cny = D("999")
        self.session.add(StoreProfitLossReport(store_id=7, report_month=self.month,
            manual_values={"salary": {"current": "0", "ytd": "99999"}}, report_metadata={}, remarks={"salary": "显式零"}))
        self.session.commit()
        self.oa["salaries"] = {"S7": {date(2026, 8, 1): D(10), date(2026, 9, 1): D(20)}}
        report = await self.report()
        self.assertEqual(D(self.cell(report, "salary")["value"]), 0)
        self.assertEqual(D(self.cell(report, "salary", "ytd")["value"]), 10)
        self.assertEqual(D(self.cell(report, "salary", "ytd")["source_total"]), 10)
        september = [r for r in report.evidence["sources"]["salary"] if r["day"].startswith("2026-09")]
        self.assertEqual([r["source"] for r in september], ["manual"])
        self.assertEqual(D(september[0]["details"]["OA参考值"]), 20)
        sales = next(r for r in report.evidence["sources"]["product_sales_revenue"] if r["source"] == "sale")
        self.assertNotEqual(D(sales["details"]["报表汇率"]["rate_to_cny"]), 999)
        self.assertNotIn("exchange_rate_to_cny", json.dumps(report.evidence))

    async def test_missing_rate_keeps_unknown_amount_and_explains_failure(self):
        self.unavailable_monthly_rates = True
        self.session.get(SettlementEntry, 1).currency = "USD"; self.session.commit()
        report = await self.report()
        self.assertIsNone(self.cell(report, 'product_sales_revenue')['value'])
        self.assertEqual(self.cell(report, 'product_sales_revenue')['status'], 'unknown')
        source = next(r for r in report.evidence['sources']['product_sales_revenue'] if r['source'] == 'sale')
        self.assertIsNone(source['amount'])
        self.assertTrue(any('USD' in w for w in report.evidence['warnings']))
        self.assertIsNone(self.cell(report, 'operating_profit')['value'])

    async def test_january_previous_year_sources_are_not_counted_in_ytd(self):
        self.month = date(2026,1,1)
        self.oa['salaries'] = {'S7': {date(2025,12,1): D(20), self.month: D(10)}}
        report = await self.report()
        self.assertEqual(D(self.cell(report,'salary','previous')['source_total']),20)
        self.assertEqual(D(self.cell(report,'salary','ytd')['source_total']),10)
        previous = evidence_page(report.evidence,'salary','previous',1,50)['items']
        self.assertEqual({r['day'][:7] for r in previous},{'2025-12'})
        self.assertFalse(any('ytd' in r['periods'] for r in previous))

    async def test_draft_ledger_unknown_and_posted_expenses_replace_manual_and_oa(self):
        self.oa["salaries"] = {"S7": {self.month: D(999)}}
        self.session.add(StoreProfitLossReport(store_id=7, report_month=self.month, manual_values={"salary": {"current": "88"}}))
        self.session.add(FinancialExpenseSource(store_id=7, report_month=self.month, category="salary", mode="ledger", updated_by=1))
        self.session.add(FinancialExpense(id=1, request_key="fixture", store_id=7, expense_date=self.month,
            category="salary", source_ref="PAY", amount=10, currency="CNY", exchange_rate=1,
            amount_cny=10, status="draft", note="", created_by=1))
        self.session.commit()
        with patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "1"}):
            report = await self.report()
            self.assertIsNone(self.cell(report, "salary")["value"])
            self.assertEqual(self.cell(report, "salary")["status"], "unknown")
            self.session.get(FinancialExpense, 1).status = "posted"; self.session.commit()
            report = await self.report()
        self.assertEqual(D(self.cell(report, "salary")["value"]), 10)
        rows = evidence_page(report.evidence, "salary", "current", 1, 50)["items"]
        self.assertEqual([r["source"] for r in rows], ["ledger"])
        self.assertEqual(D(rows[0]["amount"]), 10)

    async def test_snapshot_pagination_matches_loaded_amount_and_rechecks_scope(self):
        response = await self.request("/reports/store-profit-loss?store_id=7&report_month=2026-09-01")
        self.assertEqual(response.status_code, 200)
        data = response.json()["evidence"]
        self.assertNotIn("sources", data)
        token = data["token"]
        path = f"/reports/store-profit-loss/evidence/{token}?field=product_sales_revenue&period=current&page=1&page_size=1"
        first = (await self.request(path)).json()
        self.assertEqual(first["calculation_id"], data["calculation_id"])
        self.assertEqual(len(first["items"]), 1)
        self.session.get(SettlementEntry, 1).net_product_sales = D(888); self.session.commit()
        self.assertEqual((await self.request(path)).json(), first)
        self.user.store_id = 8
        self.assertEqual((await self.request(path)).status_code, 403)
        self.user.store_id = 7
        self.user.id = 99
        self.assertEqual((await self.request(path)).status_code, 403)
        self.user.id = 1
        self.assertEqual((await self.request(path.replace('page=1', 'page=0'))).status_code, 422)
        CACHE.items.clear()
        self.assertEqual((await self.request(path)).status_code, 410)

    async def test_summary_permission_and_totals_equal_individual_reports(self):
        path = "/reports/store-profit-loss-summary?report_month=2026-09-01"
        self.assertEqual((await self.request(path)).status_code, 403)
        self.user.role = "admin"
        result = await self.request(path)
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual([s["id"] for s in data["stores"]], [7, 8])
        row = next(r for r in data["rows"] if r["key"] == "product_sales_revenue")
        self.assertEqual(sum(D(v) for v in row["values"]), D(row["total"]))
        report = await self.report()
        self.assertEqual(D(row["values"][0]), D(self.cell(report, "product_sales_revenue")["value"]))

    async def test_real_export_scope_and_summary_links_follow_store_column_order(self):
        path = "/reports/financial-attachments/export?report_date=2026-09-30&report_month=2026-09-01"
        restricted = await self.request(path)
        self.assertEqual(restricted.status_code, 200)
        book = load_workbook(BytesIO(restricted.content), data_only=True)
        self.assertNotIn("10-各店铺损益汇总表", book.sheetnames)
        self.assertFalse(any(row[1].value == 'S8' for row in book[DETAIL].iter_rows(min_row=2)))
        self.user.role = 'admin'
        result = await self.request(path + '&store_id=8')
        self.assertEqual(result.status_code, 200)
        book = load_workbook(BytesIO(result.content), data_only=True)
        self.assertEqual(book.sheetnames[-3:], [GUIDE, CALC, DETAIL])
        summary = book['10-各店铺损益汇总表']
        # 09先放S8依据，10列依然按S7/S8；验证不能按依据创建顺序错连。
        self.assertEqual([summary.cell(1, c).value for c in (2,3)], ['S7','S8'])
        for c in (2,3):
            target = summary.cell(3,c).hyperlink.target
            address = target.split("'!")[1]
            basis_row = book[CALC][address].row
            self.assertEqual(book[CALC].cell(basis_row,2).value, summary.cell(1,c).value)
            self.assertAlmostEqual(book[CALC].cell(basis_row,6).value, summary.cell(3,c).value)
        self.assertEqual(book['09-店铺损益表'].cell(3,2).value, summary.cell(3,3).value)

    async def test_summary_ratio_evidence_uses_same_operands_as_page(self):
        self.user.role = "admin"
        self.session.get(SettlementEntry, 5).net_product_sales = D(100)
        self.session.get(SettlementEntry, 5).settlement_total = D(90)
        self.session.commit()
        response = await self.request("/reports/store-profit-loss-summary?report_month=2026-09-01")
        self.assertEqual(response.status_code, 200)
        rows = {r["key"]: r for r in response.json()["rows"]}
        self.assertEqual(D(rows["gross_profit"]["total"]), 111)
        self.assertEqual(D(rows["operating_revenue"]["total"]), 185)
        self.assertEqual(D(rows["gross_margin"]["total"]), D("0.6"))
        book = await self.export_book()
        summary = book["10-各店铺损益汇总表"]
        for number, row in enumerate(response.json()["rows"], 2):
            for column, expected in enumerate([*row["values"], row["total"], row["ratio"]], 2):
                actual = summary.cell(number, column).value
                if expected is None: self.assertIsNone(actual)
                else: self.assertAlmostEqual(actual, float(D(expected)), places=10)
        calculation = {r[0].value: r for r in book[CALC].iter_rows(min_row=2)}
        for key, mode, numerator in (("gross_margin", "合计", "gross_profit"), ("product_purchase_cost", "占比", "product_purchase_cost")):
            result = calculation[f"ALL:{key}:{mode}"]
            expression = result[7].value
            self.assertIn(f"{rows[numerator]['label'].strip()}({rows[numerator]['total']})", expression)
            self.assertIn(f"{rows['operating_revenue']['label'].strip()}({rows['operating_revenue']['total']})", expression)
            self.assertIn(" ÷ ", expression)
            self.assertIn(" = ", expression)
            expected = D(rows[key]["total" if mode == "合计" else "ratio"])
            self.assertAlmostEqual(result[5].value, float(expected), places=12)
            self.assertIn(f"{expected:.2%}", expression)
            # 离线明细也能追到同一组分子/分母的各店输入。
            details = [r for r in book[DETAIL].iter_rows(min_row=2) if r[0].value == f"ALL:{key}:{mode}"]
            for dep in (numerator, "operating_revenue"):
                terms = [r for r in details if r[2].value == dep]
                self.assertAlmostEqual(sum(r[7].value or 0 for r in terms), float(D(rows[dep]["total"])), places=10)
                self.assertTrue(all(r[6].hyperlink for r in terms))
        no_ratio = calculation["ALL:gross_margin:占比"]
        self.assertIsNone(no_ratio[5].value)
        self.assertEqual(no_ratio[7].value, "毛利率不再计算占比")

    async def test_summary_percentage_text_rounds_like_page_for_positive_and_negative_ties(self):
        self.user.role = "admin"
        self.session.get(SettlementEntry, 5).net_product_sales = D(100)
        self.session.get(SettlementEntry, 5).settlement_total = D(90)
        manual = StoreProfitLossReport(store_id=7, report_month=self.month)
        self.session.add(manual)
        for expense, expected in (("3.25", "1.63%"), ("-3.25", "-1.63%")):
            with self.subTest(expense=expense):
                manual.manual_values = {"other_revenue": {"current":"15"}, "advertising": {"current":expense}}
                self.session.commit()
                book = await self.export_book()
                row = next(r for r in book[CALC].iter_rows(min_row=2) if r[0].value == "ALL:advertising:占比")
                self.assertAlmostEqual(row[5].value, float(D(expense) / 200), places=12)
                self.assertTrue(row[7].value.endswith(" = " + expected), row[7].value)

    async def test_summary_ratio_evidence_preserves_zero_and_unknown_denominators(self):
        self.user.role = "admin"
        for missing in (False, True):
            with self.subTest(missing=missing):
                for entry in self.session.query(SettlementEntry):
                    entry.net_product_sales = entry.settlement_total = D(0)
                entry = self.session.get(SettlementEntry, 1)
                entry.currency = "USD" if missing else "CNY"
                entry.net_product_sales = entry.settlement_total = D(100) if missing else D(0)
                self.unavailable_monthly_rates = missing
                self.session.commit()
                rows = {r["key"]: r for r in (await self.request("/reports/store-profit-loss-summary?report_month=2026-09-01")).json()["rows"]}
                revenue = rows["operating_revenue"]["total"]
                if missing: self.assertIsNone(revenue)
                else: self.assertEqual(D(revenue), 0)
                book = await self.export_book()
                result = next(r for r in book[CALC].iter_rows(min_row=2) if r[0].value == "ALL:product_purchase_cost:占比")
                self.assertIsNone(result[5].value)
                self.assertIn(f"{rows['operating_revenue']['label'].strip()}({'待核对/未提供' if missing else revenue})", result[7].value)
                self.assertIn("= 待核对/未提供", result[7].value)
                self.assertNotIn("已核对", result[8].value)

    async def test_export_contains_same_evidence_internal_links_and_safe_special_text(self):
        hostile = '=HYPERLINK("https://invalid/") $& <img>\x01'
        self.session.get(SettlementEntry, 1).source_file = hostile
        self.session.add(StoreProfitLossReport(store_id=7, report_month=self.month,
            manual_values={"advertising": {"current": "5"}}, remarks={"advertising": hostile}))
        self.session.commit()
        report = await self.report()
        values = {r.key: r.value.model_dump() for r in report.rows}
        values.update(_evidence=report.evidence, _calculation_policy=report.calculation_policy)
        book = Workbook(); book.remove(book.active)
        reports_api._build_store_profit_loss_sheet(book, self.store, self.month, values, {})
        append_profit_loss_evidence(book, [(self.store, values)], 7, row_definitions=reports_api.STORE_PROFIT_LOSS_ROWS)
        output = BytesIO(); book.save(output); output.seek(0)
        saved = load_workbook(output, data_only=False)
        self.assertEqual(saved.sheetnames, ["09-店铺损益表", GUIDE, CALC, DETAIL])
        for sheet in saved:
            for row in sheet:
                for cell in row:
                    self.assertNotEqual(cell.data_type, "f")
                    if cell.hyperlink:
                        target = cell.hyperlink.target
                        self.assertTrue(target.startswith("#'"))
                        name, address = target[2:].split("'!")
                        self.assertIn(name, saved.sheetnames)
                        self.assertIsNotNone(saved[name][address].value)
        text = "\n".join(str(c.value) for row in saved[DETAIL] for c in row)
        self.assertIn('$& <img>[U+0001]', text)
        self.assertIn('HYPERLINK', text)
        for i, row in enumerate(report.rows, 2):
            for j, p in enumerate(('current','previous','ytd'), 2):
                expected = getattr(row.value, p)
                actual = saved.worksheets[0].cell(i, j).value
                if expected is None: self.assertIsNone(actual)
                else: self.assertAlmostEqual(actual, float(expected), places=10)

    async def test_old_version_preserves_blanks_rows_and_labels_with_explicit_unavailable(self):
        report = await self.report()
        old = report.model_dump(mode="json"); old.pop("evidence")
        old["calculation_policy"] = "month_end_fx_mxn8_v1"
        old["rows"] = [r for r in old["rows"] if r["key"] != "compensation_revenue"]
        old["rows"][0]["label"] = "历史营业收入"
        payload = {"store": {"id": 7, "name": "旧店", "platform": "test"}, "report_date": "2026-09-30",
            "profit_loss": old, "inventory": [], "product_lines": [], "warnings": []}
        frozen = deepcopy(payload)
        book = load_workbook(version_workbook(NS(payload=payload, report_month=self.month, version=1, status="confirmed")), data_only=True)
        self.assertEqual(payload, frozen)
        sheet = book["09-店铺损益表"]
        self.assertEqual(sheet.cell(2, 1).value, "历史营业收入")
        for i, row in enumerate(old["rows"], 2):
            for j, p in enumerate(('current','previous','ytd'), 2):
                value = row["value"][p]
                actual = sheet.cell(i, j).value
                if value is None: self.assertIsNone(actual)
                else: self.assertAlmostEqual(actual, float(value), places=10)
        self.assertTrue(any("该版本未保存" in str(c.value) for row in book[CALC] for c in row))

    async def test_version_links_follow_saved_rows_after_json_key_reordering(self):
        report = (await self.report()).model_dump(mode="json")
        # 历史版本可缺少后来新增的行，顺序也只能取其保存数组；JSON对象键顺序不可靠。
        report["rows"] = [r for r in report["rows"] if r["key"] != "compensation_revenue"]
        report["rows"] = report["rows"][2:] + report["rows"][:2]
        for schema in ("profit_loss_evidence_v1", "future_schema", None):
            with self.subTest(schema=schema):
                saved = deepcopy(report)
                if schema is None: saved.pop("evidence")
                else: saved["evidence"]["schema_version"] = schema
                payload = json.loads(json.dumps({"store": {"id":7, "name":"旧店", "platform":"test"},
                    "report_date":"2026-09-30", "profit_loss":saved, "inventory":[], "product_lines":[], "warnings":[]}, sort_keys=True))
                before = deepcopy(payload)
                book = load_workbook(version_workbook(NS(payload=payload, report_month=self.month, version=2, status="confirmed")), data_only=True)
                sheet = book["09-店铺损益表"]
                def target(cell):
                    name, address = cell.hyperlink.target[2:].split("'!")
                    return book[name], book[name][address].row
                for number, row in enumerate(saved["rows"], 2):
                    self.assertEqual(sheet.cell(number, 1).value, row["label"])
                    dest, line = target(sheet.cell(number, 1))
                    self.assertEqual(dest.cell(line, 1).value, f"S7:{row['key']}:current")
                    for column, period in enumerate(("current", "previous", "ytd"), 2):
                        cell = sheet.cell(number, column)
                        expected = row["value"][period]
                        if expected is None:
                            self.assertIsNone(cell.value)
                            self.assertIsNone(cell.hyperlink)
                            expected_row = next(r[0].row for r in book[CALC].iter_rows(min_row=2)
                                                if r[0].value == f"S7:{row['key']}:{period}")
                            self.assertIn(f"第{expected_row}行", cell.comment.text)
                            continue
                        self.assertAlmostEqual(cell.value, float(expected), places=10)
                        dest, line = target(cell)
                        self.assertEqual(dest.cell(line, 1).value, f"S7:{row['key']}:{period}")
                        self.assertAlmostEqual(dest.cell(line, 6).value, cell.value, places=10)
                        back_sheet, back_line = target(dest.cell(line, 12))
                        self.assertEqual((back_sheet.title, back_line), (sheet.title, number))
                self.assertEqual(payload, before)
                self.assertFalse(any(c.value == "其他收入（赔付）" for row in sheet for c in row))

    async def test_new_version_keeps_saved_sources_and_digest_stable(self):
        with patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "1"}):
            first = await collect_payload(self.db, self.user, self.store, self.month, oa_values=self.oa, active_store_count=2)
            again = await collect_payload(self.db, self.user, self.store, self.month, oa_values=self.oa, active_store_count=2)
        self.assertEqual(first, again)
        self.session.get(SettlementEntry, 1).source_file = "later.xlsx"; self.session.commit()
        book = load_workbook(version_workbook(NS(payload=first, report_month=self.month, version=2, status="confirmed")), data_only=True)
        self.assertFalse(any("later.xlsx" in str(c.value) for row in book[DETAIL] for c in row))

    async def test_database_row_order_does_not_change_evidence_identity(self):
        auto = await reports_api._store_profit_loss_auto_values(self.db, 7, self.month, 'S7', oa_values=self.oa, active_store_count=2)
        manual = await manual_period_values(self.db, 7, self.month, reports_api.STORE_PROFIT_LOSS_MANUAL_KEYS,
            auto.get('_monthly_defaults'), force_monthly=True)
        first = reports_api._build_store_profit_loss_values(auto, manual)['_evidence']
        auto['_source_rows'] = {key: list(reversed(rows)) for key, rows in auto['_source_rows'].items()}
        second = reports_api._build_store_profit_loss_values(auto, manual)['_evidence']
        self.assertEqual(first, second)


class EvidenceCacheTests(unittest.TestCase):
    def test_ttl_capacity_user_isolation_and_no_mutable_alias(self):
        cache = EvidenceCache(max_bytes=32, ttl=10)
        with patch('services.profit_loss_evidence.monotonic', return_value=1):
            value = {"a": "x" * 20}
            first = cache.put(value, 1); value['a'] = 'changed'
            self.assertNotEqual(cache.get(first, 1), value)
            with self.assertRaises(HTTPException) as denied: cache.get(first, 2)
            self.assertEqual(denied.exception.status_code, 403)
            second = cache.put({"a": "y" * 30}, 1)
            self.assertNotIn(first, cache.items)
            self.assertIsNone(cache.put({"a": ''.join(chr(i) for i in range(32, 126))}, 1))
        with patch('services.profit_loss_evidence.monotonic', return_value=12):
            with self.assertRaises(HTTPException) as expired: cache.get(second, 1)
            self.assertEqual(expired.exception.status_code, 410)
