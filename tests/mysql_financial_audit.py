"""Opt-in MySQL financial audit regression; owns only rolled-back fixtures, no DDL.

Set FINANCE_TEST_HOST/PORT/DATABASE/USER/PASSWORD and pass --run-local-finance-checks.
Only a pre-created fifo_browser_* local database/account on 127.0.0.1:3309 is accepted.
"""
import argparse
import asyncio
import copy
import json
import os
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run(config):
    from sqlalchemy import inspect, select
    from sqlalchemy.engine import URL
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from models import (FinancialAuditEvent, InventoryBatch, Product, Sale, SaleCostDetail,
                        SettlementEntry, Store, User)
    from schemas import (CostAdjustmentRequest, ExpenseRequest, ExpenseSourceRequest,
                         FinanceReversalRequest, FinancialVersionRequest, ReceiptRequest,
                         SettlementClassificationRequest)
    from services import financial_controls as controls
    from services.financial_versions import compare_version, save_version, version_workbook

    engine = create_async_engine(URL.create("mysql+aiomysql", username=config["USER"],
        password=config["PASSWORD"], host=config["HOST"], port=int(config["PORT"]), database=config["DATABASE"]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    run_id = "audit-" + secrets.token_hex(8)
    month = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
    checks = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    async def audited(db, row, timestamp):
        columns = {item.key for item in row.__mapper__.column_attrs}
        check(row.__tablename__ + ": loaded columns", not (inspect(row).unloaded & columns))
        response = controls.record(row)
        check(row.__tablename__ + ": default timestamp", isinstance(response[timestamp], datetime))
        await db.flush()
        event = await db.scalar(select(FinancialAuditEvent).where(
            FinancialAuditEvent.entity_type == row.__tablename__, FinancialAuditEvent.entity_id == row.id
        ).order_by(FinancialAuditEvent.id.desc()).limit(1))
        check(row.__tablename__ + ": persisted audit timestamp", event.after_values[timestamp] == str(response[timestamp]))

    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda conn: set(inspect(conn).get_table_names()))
            check("pre-created financial schema", controls.FinancialAuditEvent.__tablename__ in tables)
        async with factory() as db:
            try:
                store = Store(platform="test", platform_store_id=run_id, name=run_id,
                              country_or_region="CN", settlement_currency="CNY")
                db.add(store)
                await db.flush()
                user = User(username=run_id, password_hash="unusable-test-only", role="admin",
                            is_active=False, store_id=store.id)
                product = Product(sku=run_id, name=run_id, product_type="new", safe_stock_quantity=0)
                db.add_all([user, product])
                await db.flush()
                actor = SimpleNamespace(id=user.id, role="admin", store_id=store.id)
                batch = InventoryBatch(product_id=product.id, user_id=user.id, batch_no=run_id,
                    quantity=100, remaining_quantity=60, purchase_price=10, unit_cost=10, total_amount=1000,
                    arrived_at=datetime.combine(month, datetime.min.time()))
                sale = Sale(product_id=product.id, user_id=user.id, store_id=store.id, order_no=run_id,
                    order_claim=run_id, quantity=40, selling_price=20, total_cost=400, platform_fee=0, profit=400,
                    sold_at=datetime.combine(month + timedelta(days=14), datetime.min.time()))
                entry = SettlementEntry(source_key=run_id, source_row_number=1, order_id=run_id, platform_sku_id=run_id,
                    store_id=store.id, product_id=product.id, payout_id=run_id, currency="CNY",
                    exchange_rate_to_cny=1, transaction_type="调整", quantity=0,
                    net_product_sales=0, settlement_total=-10, settlement_date=sale.sold_at,
                    mapping_status="confirmed", raw_data={})
                db.add_all([batch, sale, entry])
                await db.flush()
                db.add(SaleCostDetail(sale_id=sale.id, batch_id=batch.id, quantity=40, unit_cost=10))
                await db.flush()
                old = await save_version(db, actor, FinancialVersionRequest(
                    request_key=run_id + "-v1", store_id=store.id, report_month=month))
                await audited(db, old, "created_at")
                frozen = copy.deepcopy(old.payload)

                payload = CostAdjustmentRequest(request_key=run_id + "-cost", source_ref=run_id,
                    category="head_shipping", amount="200", rate_date=date.today(), document_date=date.today(),
                    allocations=[{"batch_id": batch.id}])
                _, preview, _ = await controls.prepare_cost_adjustment(db, actor, payload)
                check("late cost split 80/120", (preview[0]["sold_amount_cny"], preview[0]["remaining_amount_cny"]) == (80, 120))
                cost = await controls.post_cost_adjustment(db, actor, payload)
                await audited(db, cost, "created_at")
                check("cost retry idempotent", (await controls.post_cost_adjustment(db, actor, payload)).id == cost.id)

                expense = await controls.create_expense(db, actor, ExpenseRequest(request_key=run_id + "-expense",
                    store_id=store.id, expense_date=month, category="advertising", source_ref=run_id, amount="30"))
                await audited(db, expense, "created_at")
                source = await controls.set_expense_source(db, actor, ExpenseSourceRequest(
                    store_id=store.id, report_month=month, category="advertising", mode="ledger"))
                await audited(db, source, "updated_at")
                source.updated_at = datetime(2000, 1, 1)
                await db.flush()
                source = await controls.set_expense_source(db, actor, ExpenseSourceRequest(
                    store_id=store.id, report_month=month, category="advertising", mode="manual"))
                await audited(db, source, "updated_at")
                check("source onupdate timestamp", source.updated_at != datetime(2000, 1, 1))
                await controls.post_expense(db, actor, expense.id)
                check("expense posted", expense.status == "posted")

                receipt = await controls.create_receipt(db, actor, ReceiptRequest(request_key=run_id + "-receipt",
                    store_id=store.id, payout_id=run_id, receipt_date=date.today(), source_ref=run_id, amount="100"))
                await audited(db, receipt, "created_at")
                classification = await controls.classify_settlement(db, actor, entry.id,
                    SettlementClassificationRequest(breakdown={"commission": Decimal(10)}))
                await audited(db, classification, "confirmed_at")

                new = await save_version(db, actor, FinancialVersionRequest(
                    request_key=run_id + "-v2", store_id=store.id, report_month=month))
                await audited(db, new, "created_at")
                comparison = await compare_version(db, actor, old)
                check("original sale month restated 80", any(row["key"] == "head_logistics_cost" and
                    row["period"] == "current" and row["difference"] == 80 for row in comparison["changes"]))
                check("immutable versions", old.payload == frozen and (old.version, new.version) == (1, 2))
                check("frozen version export", len(version_workbook(old).getvalue()) > 0)
                for name, row, operation in (("cost", cost, controls.reverse_cost_adjustment),
                                            ("expense", expense, controls.reverse_expense),
                                            ("receipt", receipt, controls.reverse_receipt)):
                    reverse = await operation(db, actor, row.id, FinanceReversalRequest(
                        request_key=run_id + "-reverse-" + name, note="local regression"))
                    await audited(db, reverse, "created_at")
                    check(name + " reversal", reverse.amount == -row.amount)
                check("original FIFO unchanged", batch.unit_cost == 10 and batch.remaining_quantity == 60 and sale.total_cost == 400)
            finally:
                await db.rollback()
            check("fixtures rolled back", await db.scalar(select(Store.id).where(Store.platform_store_id == run_id)) is None)
            await db.rollback()
        print(json.dumps({"checks": len(checks), "passed": len(checks), "cases": checks}, ensure_ascii=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-local-finance-checks", action="store_true")
    args = parser.parse_args()
    config = {key: os.getenv("FINANCE_TEST_" + key, "") for key in ("HOST", "PORT", "DATABASE", "USER", "PASSWORD")}
    if not args.run_local_finance_checks or config["HOST"] != "127.0.0.1" or config["PORT"] != "3309" or not (
        config["DATABASE"].startswith("fifo_browser_") and config["USER"].startswith("fifo_browser_") and config["PASSWORD"]
    ):
        raise SystemExit("Refusing: explicit opt-in and pre-created local fifo_browser_* database/account required")
    # Dedicated service tests must not call external OA while collecting report payloads.
    os.environ.update(FIFO_FINANCIAL_CONTROLS_ENABLED="1", OA_DB_HOST="", OA_DB_PASSWORD="")
    asyncio.run(run(config))
