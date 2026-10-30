"""三种商品类型、独立安全库存及旧safe_stock历史兼容；不连接业务库。"""

import unittest
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from openpyxl import load_workbook
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from auth import get_current_user
from database import Base, get_db
from models import Product, ProductImpairmentRule
from routers.products import _impairment_settings, router
from routers.reports import (
    _build_inventory_impairment_workbook, _load_inventory_impairment_report_rows,
    _store_provision_financial_values, _summarize_inventory_impairment_rows,
    _workbook_bytes_with_formula_cache, inventory_impairment_report,
)
from services.inventory_impairment import (
    PRODUCT_TYPE_NAMES, ImpairmentRule, InventoryLayer, batch_impairments,
)
from services.inventory_provision_movements import ProvisionSale, replay_inventory_provisions
from services.product_import import (
    build_product_import_template, confirm_product_import, issue_product_import_token,
    parse_product_import, preview_product_import,
)
from tests.test_product_import import FakeSession, parsed
from tests.test_batch_edit import AsyncFacade


class ProductSession(AsyncFacade):
    def add_all(self, rows): self.session.add_all(rows)
    async def rollback(self): self.session.rollback()


class ProductTypeRuleTests(unittest.TestCase):
    arrival = date(2026, 8, 31)
    cutoff = date(2026, 9, 10)

    def layer(self, kind, safe=0, rules=(), batch_id=1, arrival=None, quantity=100):
        return InventoryLayer(batch_id, 1, 7, arrival or self.arrival, quantity, kind, safe, rules)

    def test_four_rules_in_replay_and_snapshot_algorithm(self):
        for kind, safe, units in (("stable", 0, "10"), ("new", 0, "5"),
                                  ("safe_stock", 40, "6"), ("no_impairment", 0, "0")):
            with self.subTest(kind=kind):
                layer = self.layer(kind, safe)
                replay = replay_inventory_provisions([layer], [], self.cutoff,
                                                     balance_dates=[self.arrival])
                self.assertEqual(replay.balances[self.arrival][1].impairment_units, 0)
                self.assertEqual(replay.balances[self.cutoff][1].impairment_units, Decimal(units))
                self.assertEqual(batch_impairments([layer], self.cutoff)[1].impairment_units, Decimal(units))

    def test_only_stable_deducts_safety_and_legacy_alias_is_identical(self):
        self.assertEqual(set(PRODUCT_TYPE_NAMES), {"stable", "new", "no_impairment"})
        for kind, expected in (("stable", "6"), ("safe_stock", "6"), ("new", "5"), ("no_impairment", "0")):
            layer = self.layer(kind, 40)
            self.assertEqual(replay_inventory_provisions([layer], [], self.cutoff).balances[self.cutoff][1].impairment_units, Decimal(expected))
            self.assertEqual(batch_impairments([layer], self.cutoff)[1].impairment_units, Decimal(expected))
        legacy = parsed([["OLD1", "旧商品", "安全库存商品", 40], ["OLD2", "旧商品", "safe_stock", 25]])
        self.assertTrue(all(not row.errors for row in legacy.rows))
        self.assertEqual([(row.product_type, row.safe_stock_quantity) for row in legacy.rows], [("stable",40),("stable",25)])

    def test_safe_stock_zero_above_stock_and_newest_batch_protection(self):
        for safe, units in ((0, "10"), (100, "0"), (120, "0")):
            layer = self.layer("safe_stock", safe)
            replay = replay_inventory_provisions([layer], [], self.cutoff)
            self.assertEqual(replay.balances[self.cutoff][1].impairment_units, Decimal(units))
        layers = [self.layer("safe_stock", 80), self.layer("safe_stock", 80, batch_id=2,
                                                         arrival=date(2026, 9, 5), quantity=50)]
        current = replay_inventory_provisions(layers, [], self.cutoff).balances[self.cutoff]
        self.assertEqual(current[1].impairment_units, Decimal("5"))
        self.assertEqual(current[2].impairment_units, 0)

    def test_legacy_stable_safety_is_identical_to_explicit_safety(self):
        sale = ProvisionSale(1, 1, 1, datetime(2026, 9, 5), 30, 7)
        legacy = replay_inventory_provisions([self.layer("stable", 40)], [sale], self.cutoff)
        explicit = replay_inventory_provisions([self.layer("safe_stock", 40)], [sale], self.cutoff)
        self.assertEqual(legacy.balances, explicit.balances)
        self.assertEqual(legacy.accruals, explicit.accruals)
        self.assertEqual(legacy.writeoffs, explicit.writeoffs)
        self.assertEqual(batch_impairments([self.layer("stable", 40)], self.cutoff),
                         batch_impairments([self.layer("safe_stock", 40)], self.cutoff))

    def test_switch_to_no_impairment_preserves_prior_provision_and_writeoff(self):
        rules = (ImpairmentRule("new", 0, self.arrival),
                 ImpairmentRule("no_impairment", 0, date(2026, 9, 6)))
        layer = self.layer("no_impairment", rules=rules)
        sale = ProvisionSale(1, 1, 1, datetime(2026, 9, 8), 40, 7)
        replay = replay_inventory_provisions([layer], [sale], self.cutoff)
        self.assertEqual(replay.accrued_units(1, date(2026, 9, 1), date(2026, 9, 11)), Decimal("2.5"))
        self.assertEqual(replay.writeoffs[0].units, Decimal("1"))
        self.assertEqual(replay.balances[self.cutoff][1].impairment_units, Decimal("1.5"))
        self.assertEqual(batch_impairments([layer], self.cutoff)[1].impairment_units, Decimal("2.5"))

    def test_resuming_accrual_does_not_catch_up_exempt_days(self):
        rules = (ImpairmentRule("no_impairment", 0, self.arrival),
                 ImpairmentRule("stable", 0, date(2026, 9, 6)))
        layer = self.layer("stable", rules=rules)
        self.assertEqual(replay_inventory_provisions([layer], [], self.cutoff)
                         .balances[self.cutoff][1].impairment_units, Decimal("5"))
        self.assertEqual(batch_impairments([layer], self.cutoff)[1].impairment_units, Decimal("5"))

    def test_no_impairment_does_not_mask_invalid_sales(self):
        layer = self.layer("no_impairment")
        sale = ProvisionSale(1, 1, 1, datetime(2026, 8, 30), 10, 7)
        replay = replay_inventory_provisions([layer], [sale], self.cutoff)
        self.assertIsNone(replay.balances[self.cutoff][1].impairment_units)
        self.assertIsNone(replay.writeoffs[0].units)
        self.assertIn("销售日期早于", replay.notes[1])

    def test_cap_is_shared_across_type_changes(self):
        for kind in PRODUCT_TYPE_NAMES:
            rules = (ImpairmentRule("stable", 0, date(2026, 5, 1)),
                     ImpairmentRule(kind, 0, date(2026, 9, 1)))
            layer = self.layer(kind, rules=rules, arrival=date(2026, 5, 1))
            current = replay_inventory_provisions([layer], [], self.cutoff).balances[self.cutoff][1]
            self.assertEqual(current.impairment_units, Decimal("90"))
            self.assertEqual(current.period_impairment_units, 0)
            self.assertEqual(batch_impairments([layer], self.cutoff)[1].impairment_units, Decimal("90"))

    def test_rule_validation_and_legacy_client_normalization(self):
        for kind in PRODUCT_TYPE_NAMES:
            self.assertEqual(_impairment_settings(kind, 0), (kind, 0))
        self.assertEqual(_impairment_settings("stable", 25), ("stable", 25))
        self.assertEqual(_impairment_settings("safe_stock", 25), ("stable", 25))
        self.assertEqual(_impairment_settings("no_impairment", 25), ("no_impairment", 0))
        for kind, safe in (("invalid", 0), ("safe_stock", -1), ("no_impairment", -1)):
            with self.assertRaises(HTTPException):
                _impairment_settings(kind, safe)

    def test_import_four_codes_labels_and_legacy_file(self):
        rows = []
        for kind, label in PRODUCT_TYPE_NAMES.items():
            for value in (kind, label):
                rows.append([f"SKU-{len(rows)}", "商品", value, 40 if kind == "stable" else 0])
        rows.append(["LEGACY", "旧文件", "稳健商品", 25])
        data = parsed(rows)
        self.assertTrue(all(not row.errors for row in data.rows))
        self.assertEqual([row.product_type for row in data.rows],
                         [kind for kind in PRODUCT_TYPE_NAMES for _ in range(2)] + ["stable"])
        invalid = parsed([["INVALID", "商品", "不计跌价商品", 20]])
        self.assertEqual(invalid.rows[0].status, "error")
        template = load_workbook(build_product_import_template())
        options = template.active.data_validations.dataValidation[0].formula1
        self.assertTrue(all(label in options for label in PRODUCT_TYPE_NAMES.values()))


class ProductTypeApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://")
        event.listen(self.engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.db = ProductSession(self.session)
        app = FastAPI()
        app.include_router(router)

        async def db():
            yield self.db

        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, role="viewer")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.session.close()
        self.engine.dispose()

    async def test_create_edit_history_and_export_round_trip(self):
        ids = {}
        for kind in PRODUCT_TYPE_NAMES:
            response = await self.client.post("/products", data={"sku": kind, "name": kind,
                "product_type": kind, "safe_stock_quantity": 40 if kind == "stable" else 0})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["product_type"], kind)
            ids[kind] = response.json()["id"]
        exported = await self.client.get("/products/export")
        self.assertEqual(exported.status_code, 200)
        sheet = load_workbook(BytesIO(exported.content)).active
        self.assertEqual({sheet.cell(row, 5).value for row in range(2, sheet.max_row + 1)}, set(PRODUCT_TYPE_NAMES.values()))
        imported = parse_product_import("export.xlsx", exported.content)
        self.assertTrue(all(not row.errors for row in imported.rows))
        original = self.session.scalar(select(ProductImpairmentRule).where(
            ProductImpairmentRule.product_id == ids["stable"]))
        original.effective_date = date(2026, 1, 1)
        self.session.commit()
        updated = await self.client.put(f"/products/{ids['stable']}", data={
            "sku": "safe_stock", "name": "safe_stock", "product_type": "no_impairment"})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["safe_stock_quantity"], 0)
        rules = self.session.scalars(select(ProductImpairmentRule).where(
            ProductImpairmentRule.product_id == ids["stable"])
            .order_by(ProductImpairmentRule.effective_date)).all()
        self.assertEqual([(r.product_type, r.safe_stock_quantity) for r in rules],
                         [("stable", 40), ("no_impairment", 0)])
        self.assertEqual((await self.client.post("/products", data={
            "sku": "bad", "name": "bad", "product_type": "unknown"})).status_code, 422)

    async def test_three_types_import_to_product_and_rule_together(self):
        data = parsed([[kind, kind, kind, 40 if kind == "stable" else 0] for kind in PRODUCT_TYPE_NAMES])
        # 使用真实SQLite提交；仅查重排序规则由既有模拟器提供。
        duplicate_check = FakeSession(data.rows)
        with patch.object(self.db, "execute", side_effect=duplicate_check.execute):
            preview = await preview_product_import(self.db, data)
            token = issue_product_import_token(data, preview, 1, "unit-test-key")
            result = await confirm_product_import(self.db, data, token, 1, "unit-test-key")
            self.assertEqual(result.created_count, 3)
        products = self.session.scalars(select(Product)).all()
        rules = self.session.scalars(select(ProductImpairmentRule)).all()
        self.assertEqual({p.product_type for p in products}, set(PRODUCT_TYPE_NAMES))
        self.assertEqual({r.product_type for r in rules}, set(PRODUCT_TYPE_NAMES))


class ProductTypeReportingTests(unittest.IsolatedAsyncioTestCase):
    def db(self, future_exemption=False):
        def result(rows):
            return SimpleNamespace(all=lambda: rows, scalars=lambda: SimpleNamespace(all=lambda: rows))
        batches, rules = [], []
        for number, kind in enumerate(("stable", "new", "safe_stock", "no_impairment"), 1):
            safe = 40 if kind == "safe_stock" else 0
            batch = SimpleNamespace(id=number, product_id=number, quantity=100, unit_cost=Decimal(10),
                                    arrived_at=datetime(2026, 8, 31), batch_no=f"B{number}")
            batches.append((batch, kind, kind, "店铺", kind, safe, 7))
            rules.append(SimpleNamespace(product_id=number, product_type=kind, safe_stock_quantity=safe,
                                         effective_date=date(2026, 8, 31)))
        if future_exemption:
            rules[-1].product_type = "new"
            rules.append(SimpleNamespace(product_id=4, product_type="no_impairment", safe_stock_quantity=0,
                                         effective_date=date(2026, 9, 15)))
        return SimpleNamespace(info={}, execute=AsyncMock(side_effect=[
            result(batches), result(rules), result([]), result([]), result([]), result([])]), commit=AsyncMock())

    async def test_mixed_types_api_excel_sku_and_profit_use_same_total(self):
        cutoff = date(2026, 9, 10)
        db = self.db()
        rows = await _load_inventory_impairment_report_rows(db, cutoff)
        expected = [Decimal(100), Decimal(50), Decimal(60), Decimal(0)]
        self.assertEqual([r["impairment_amount"] for r in rows], expected)
        self.assertEqual(sum(r["monthly_total_impairment_amount"] for r in rows), Decimal(210))
        self.assertEqual(sum(r["impairment_amount"] for r in _summarize_inventory_impairment_rows(rows)), Decimal(210))
        detail = await inventory_impairment_report(cutoff, db, None)
        self.assertEqual(detail[-1]["status"], "不计跌价")
        workbook, cache = _build_inventory_impairment_workbook(rows, cutoff)
        sheet = load_workbook(_workbook_bytes_with_formula_cache(workbook, cache), data_only=True).active
        self.assertEqual([sheet[f"O{row}"].value for row in range(2, 6)], expected)
        self.assertEqual(sheet["O6"].value, 210)
        self.assertEqual(sheet["S5"].value, "不计跌价")
        periods = {"current": (date(2026, 9, 1), date(2026, 9, 11))}
        profit = await _store_provision_financial_values(db, 7, periods)
        self.assertEqual(profit["inventory_impairment"]["current"], Decimal(210))
        db.commit.assert_not_awaited()

    async def test_report_status_uses_rule_at_cutoff_and_preserves_history(self):
        before = await inventory_impairment_report(date(2026, 9, 10), self.db(True), None)
        self.assertEqual(before[-1]["impairment_amount"], Decimal(50))
        self.assertEqual(before[-1]["status"], "正常计提")
        after = await inventory_impairment_report(date(2026, 9, 20), self.db(True), None)
        self.assertEqual(after[-1]["impairment_amount"], Decimal(70))
        self.assertEqual(after[-1]["status"], "停止新增计提（保留历史准备）")
