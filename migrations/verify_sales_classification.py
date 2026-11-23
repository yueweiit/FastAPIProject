"""只读核对 2026-11-10 专项分类清单及准备回放，不适用于其他数据集。

直接执行会连接配置中的数据库，须先核实目标；不运行 main/迁移/快照/OA。
分类通过不代表财务金额已核准，回放结果及归属警示仍需人工核对。
"""
import asyncio
import json
import sys
from decimal import Decimal
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import func, select, text
from database import async_session, engine
from models import NonInventorySkuRule, Sale, SettlementEntry, SettlementEntryAllocation
from routers.reports import _month_periods, _store_provision_financial_values
from services.sales_classification import OPENING_DEDUCTION


MAINTENANCE_ID = "fifo_classification_20261110_v1"
STORE_ID = 1
# 来自本次已确认的维护清单，不按行数/合计猜测其他销售属于初始化扣减。
OPENING_QUANTITIES = {1087: 108, 1127: 30, 1129: 34, 1131: 54,
                      1118: 1, 1119: 1, 1120: 6, 1090: 215}
EXPECTED_SETTLEMENTS = {
    21863: ("1736307185018307714", Decimal("34"), Decimal("15.44")),
    22309: ("1736253253365040258", Decimal("39"), Decimal("23.41")),
}


def _known_total(entries, field):
    values = [getattr(row, field) for row in entries]
    if not values or any(value is None for value in values):
        return None
    return sum(values, Decimal("0"))


async def verify():
    try:
        async with async_session() as db:
            try:
                await db.execute(text("START TRANSACTION READ ONLY"))
                opening = (await db.execute(select(Sale.id, Sale.quantity).where(
                    Sale.business_metadata["kind"].as_string() == OPENING_DEDUCTION,
                    Sale.business_metadata["maintenance_id"].as_string() == MAINTENANCE_ID,
                ))).all()
                entries = (await db.execute(select(
                    SettlementEntry.id, SettlementEntry.store_id, SettlementEntry.platform_sku_id,
                    SettlementEntry.mapping_status, SettlementEntry.net_product_sales,
                    SettlementEntry.settlement_total, SettlementEntry.currency,
                    SettlementEntry.sale_id, SettlementEntry.product_id, SettlementEntry.raw_data,
                ).where(SettlementEntry.id.in_(EXPECTED_SETTLEMENTS)))).all()
                rules = await db.scalar(select(func.count()).select_from(NonInventorySkuRule).where(
                    NonInventorySkuRule.store_id == STORE_ID, NonInventorySkuRule.is_active.is_(True),
                    NonInventorySkuRule.platform_sku_id.in_([row[0] for row in EXPECTED_SETTLEMENTS.values()]),
                ))
                allocations = await db.scalar(select(func.count()).select_from(SettlementEntryAllocation).where(
                    SettlementEntryAllocation.settlement_entry_id.in_(EXPECTED_SETTLEMENTS),
                ))
                good = (
                    dict(opening) == OPENING_QUANTITIES and len(opening) == len(OPENING_QUANTITIES)
                    and rules == 2 and allocations == 0 and len(entries) == 2
                    and all(
                        row.store_id == STORE_ID and row.mapping_status == "confirmed"
                        and row.sale_id is None and row.product_id is None and row.currency == "MXN"
                        and (row.platform_sku_id, row.net_product_sales, row.settlement_total)
                            == EXPECTED_SETTLEMENTS[row.id]
                        and isinstance(row.raw_data, dict)
                        and row.raw_data.get("_settlement_only_reason") == "non_inventory"
                        for row in entries
                    )
                )
                print(json.dumps({
                    "check": "classification_records", "maintenance_id": MAINTENANCE_ID, "ok": good,
                    "opening_rows": len(opening), "opening_units": sum(row[1] for row in opening),
                    "active_non_inventory_rules": rules, "non_inventory_allocations": allocations,
                    "non_inventory_original_sales_mxn": _known_total(entries, "net_product_sales"),
                    "non_inventory_original_settlement_mxn": _known_total(entries, "settlement_total"),
                }, default=str, ensure_ascii=False))
            finally:
                await db.rollback()
        for month in (date(2026, 9, 1), date(2026, 10, 1)):
            async with async_session() as db:
                try:
                    await db.execute(text("START TRANSACTION READ ONLY"))
                    values = await _store_provision_financial_values(db, STORE_ID, _month_periods(month))
                    print(json.dumps({"store_id": STORE_ID, "month": month.isoformat(), **values},
                                     default=str, ensure_ascii=False))
                finally:
                    await db.rollback()
    finally:
        await engine.dispose()
    if not good:
        raise SystemExit("CLASSIFICATION_VERIFICATION_FAILED")
    print("CLASSIFICATION_VERIFIED_REPORTS_REQUIRE_REVIEW")


if __name__ == "__main__":
    asyncio.run(verify())
