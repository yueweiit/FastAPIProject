"""库存盘点对照及截止日成本：派生查询，不生成库存调整。"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError

from models import InventoryBatch, InventoryStockCount, Sale, SaleCostDetail
from services.inventory_activity import load_activity_sources, build_activity, activity_rows, local_audit_time
from services.inventory_provision_data import load_inventory_provision_data
from services.batch_impairment_history import rule_at
from services.batch_store_allocations import allocated_provision_balance

ZERO = Decimal(0)


def known_sum(values):
    values = list(values)
    return None if None in values else sum(values, ZERO)


def count_value(row):
    return {"id": row.id, "count_date": row.count_date, "revision": row.revision,
            "system_quantity": row.system_quantity, "actual_quantity": row.actual_quantity,
            "difference": row.actual_quantity - row.system_quantity, "note": row.note,
            "created_by": row.created_by, "created_at": row.created_at}


async def latest_counts(db, product_ids, count_date):
    latest = select(func.max(InventoryStockCount.id)).where(
        InventoryStockCount.product_id.in_(product_ids), InventoryStockCount.count_date == count_date
    ).group_by(InventoryStockCount.product_id)
    try:
        rows = (await db.execute(select(InventoryStockCount).where(InventoryStockCount.id.in_(latest)))).scalars().all()
    except (OperationalError, ProgrammingError) as error:
        if getattr(error.orig, "args", [None])[0] == 1146 or "no such table: inventory_stock_counts" in str(error.orig):
            raise HTTPException(503, detail={"code": "inventory_count_schema_required",
                "message": "盘点记录表尚未准备，请先完成数据库结构准备。"}) from error
        raise
    return {row.product_id: row for row in rows}


async def quantity_basis(db, products, as_of, *, batch_balances=None, include_voided_receipts=True):
    start = as_of.replace(day=1)
    sources = await load_activity_sources(db, [p.id for p in products], start, as_of)
    activity, events = build_activity(*sources, start, as_of, full_scope=True, batch_balances=batch_balances)
    rows = activity_rows(products, activity, start, as_of, full_scope=True)
    visible_batches = {batch.id for batch in sources[0] if include_voided_receipts or not batch.voided_at}
    receipts = defaultdict(list)
    for event in events:
        if event["kind"] == "inbound" and event["batch_id"] in visible_batches:
            receipts[event["product_id"]].append(event["quantity"])
    totals = {total.batch_id: total for total in sources[3]}
    voided_products = set()
    for batch in sources[0]:
        if not batch.voided_at or batch.arrived_at.date() > as_of:
            continue
        void_day = local_audit_time(batch.voided_at).date()
        total = totals.get(batch.id)
        sold_before = bool(total and total.quantity >= batch.quantity and total.last_sale.date() < start)
        if void_day >= start and void_day > batch.arrived_at.date() and not sold_before:
            voided_products.add(batch.product_id)
    for row in rows:
        row["voided_holding_history"] = row["product_id"] in voided_products
        row["system_quantity"] = row["days"][-1]["closing_quantity"]
        # 只固定截至盘点日的数量事实；以后正常销售不能使旧盘点虚假失效。
        basis = {"product_id": row["product_id"], "as_of": as_of, "days": row["days"],
                 "warnings": sorted(row["warnings"])}
        row["ledger_version"] = sha256(json.dumps(basis, default=str, sort_keys=True).encode()).hexdigest()
        # 展示入库按角色过滤；内部账面/版本仍来自完整事实，不能因查看权限而变更。
        row["month_inbound"] = known_sum(receipts[row["product_id"]])
    return rows


async def assessment_rows(db, products, as_of, *, sale_scope=None, store_id=None, full_scope=True, include_voided_receipts=True):
    batch_balances = {}
    rows = await quantity_basis(db, products, as_of, batch_balances=batch_balances,
                                include_voided_receipts=include_voided_receipts)
    ids = [p.id for p in products]
    source_count = await db.scalar(select(func.count()).select_from(SaleCostDetail)
        .join(InventoryBatch, InventoryBatch.id == SaleCostDetail.batch_id).join(Sale, Sale.id == SaleCostDetail.sale_id)
        .where(InventoryBatch.product_id.in_(ids), Sale.sold_at < datetime.combine(as_of + timedelta(days=1), time.min)))
    if source_count > 10000:
        raise HTTPException(422, detail={"code": "inventory_sources_too_large", "message": "历史出库较多，请缩小商品范围后查询。"})
    counts = await latest_counts(db, ids, as_of)
    batch_rows, _sales, replay = await load_inventory_provision_data(db, as_of, product_ids=ids)
    details = defaultdict(list)
    for batch, _sku, _name, _store, _kind, _safe, _owner in batch_rows:
        balance = replay.balances[as_of][batch.id]
        amount = None if balance.impairment_units is None else balance.impairment_units * batch.unit_cost
        accrued = replay.accrued_units(batch.id, as_of.replace(day=1), as_of + timedelta(days=1))
        owners = rule_at(batch, as_of).stores
        details[batch.product_id].append({
            "batch_id": batch.id, "batch_no": batch.batch_no, "quantity": balance.quantity,
            "arrived_at": batch.arrived_at.date(), "age_days": max(0, (as_of - batch.arrived_at.date()).days),
            "unit_cost": batch.unit_cost, "inventory_amount": balance.quantity * batch.unit_cost,
            "impairment_amount": amount, "monthly_total_impairment_amount": None if accrued is None else accrued * batch.unit_cost,
            "store_names": [owner["name"] for owner in owners],
            "store_allocations": [{"store_id": owner["id"], "store_name": owner["name"],
                "impairment_amount": allocated_provision_balance(replay, batch, owner["id"], as_of, balance.impairment_units)}
                for owner in owners if full_scope or owner["id"] == store_id],
            "note": replay.notes.get(batch.id, ""),
        })
        if not owners:
            details[batch.product_id][-1]["note"] += "；跌价归属待分配，店铺分摊待核对"
            if full_scope:
                details[batch.product_id][-1]["store_allocations"] = [
                    {"store_id": None, "store_name": "待分配", "impairment_amount": None}]
    own_outbound = {}
    if not full_scope:
        own_outbound = dict((await db.execute(select(Sale.product_id, func.sum(SaleCostDetail.quantity))
            .join(SaleCostDetail, SaleCostDetail.sale_id == Sale.id)
            .where(Sale.product_id.in_(ids), sale_scope, Sale.sold_at >= datetime.combine(as_of.replace(day=1), time.min),
                   Sale.sold_at < datetime.combine(as_of + timedelta(days=1), time.min))
            .group_by(Sale.product_id))).all())
    by_id = {p.id: p for p in products}
    for row in rows:
        product_id = row["product_id"]
        batches = details[product_id]
        quantity = row["system_quantity"]
        stock_quantities = batch_balances.get(product_id, {})
        replay_quantities = {batch["batch_id"]: batch["quantity"] for batch in batches}
        batch_mismatch = any(value is None or value != replay_quantities.get(batch_id, 0)
                             for batch_id, value in stock_quantities.items()) or bool(replay_quantities.keys() - stock_quantities.keys())
        inconsistent = quantity is None or quantity != sum(replay_quantities.values()) or batch_mismatch
        if inconsistent:
            row["warnings"] = [*row["warnings"], "inventory_assessment_quantity_mismatch"]
            for batch in batches:
                batch["quantity"] = None
                batch["inventory_amount"] = batch["impairment_amount"] = batch["monthly_total_impairment_amount"] = None
                for owner in batch["store_allocations"]:
                    owner["impairment_amount"] = None
                batch["note"] += "；逐批数量日结与准备回放不一致，金额待核对"
        row.update({"as_of": as_of, "batches": batches,
            "month_outbound": known_sum(day["outbound"] for day in row["days"]) if full_scope else own_outbound.get(product_id, 0),
            "inventory_amount": None if inconsistent else known_sum(b["inventory_amount"] for b in batches),
            "impairment_amount": None if inconsistent else known_sum(b["impairment_amount"] for b in batches),
            "monthly_total_impairment_amount": None if inconsistent else known_sum(b["monthly_total_impairment_amount"] for b in batches),
            "safe_stock_quantity": by_id[product_id].safe_stock_quantity,
            "product_type": by_id[product_id].product_type,
        })
        row["average_unit_cost"] = (row["inventory_amount"] / quantity if quantity else ZERO) if row["inventory_amount"] is not None else None
        row["book_value"] = None if row["inventory_amount"] is None or row["impairment_amount"] is None else row["inventory_amount"] - row["impairment_amount"]
        count = counts.get(product_id)
        row["count"] = count_value(count) if count else None
        row["count_basis_changed"] = bool(count and count.ledger_version != row["ledger_version"])
        if row.pop("voided_holding_history"):
            row["monthly_total_impairment_amount"] = None
            row["warnings"] = [*row["warnings"], "inventory_assessment_voided_history"]
        row.pop("days", None)  # 运营仅可看今日余额，不泄露全局历史日结。
    return rows
