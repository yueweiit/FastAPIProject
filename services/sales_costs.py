"""销售出库跌价参考：订单库龄×订单日类型比例×实际FIFO成本，不改写准备转销。"""
from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from models import (InventoryBatch, Product, ProductImpairmentRule, SalesImportBatch,
                    SettlementEntry, SettlementEntryAllocation)
from services.inventory_impairment import daily_impairment_rate, normalized_product_type
from services.financial_facts import SETTLEMENT_ONLY_KEY, is_fulfillment_settlement

ZERO = Decimal(0)


async def _sale_order_dates(db, sales):
    """订单详情用来源创建日；手工/旧CSV保留显式日期，不用结算日填补缺失。"""
    columns = (SettlementEntry.id, SettlementEntry.order_created_at, SalesImportBatch.file_type,
               SettlementEntry.transaction_type, SettlementEntry.quantity, SettlementEntry.net_product_sales,
               SettlementEntry.raw_data[SETTLEMENT_ONLY_KEY].as_string())
    allocated = (await db.execute(select(SettlementEntryAllocation.sale_id, *columns)
        .join(SettlementEntry, SettlementEntry.id == SettlementEntryAllocation.settlement_entry_id)
        .outerjoin(SalesImportBatch, SalesImportBatch.id == SettlementEntry.import_batch_id)
        .where(SettlementEntryAllocation.sale_id.in_(sales)))).all()
    direct = (await db.execute(select(SettlementEntry.sale_id, *columns)
        .outerjoin(SalesImportBatch, SalesImportBatch.id == SettlementEntry.import_batch_id)
        .where(SettlementEntry.sale_id.in_(sales)))).all()
    sources = defaultdict(dict)
    for sale_id, entry_id, created_at, file_type, kind, quantity, net, reason in [*allocated, *direct]:
        if file_type != "legacy_csv" and not is_fulfillment_settlement(kind, quantity or 0, net or ZERO, reason):
            continue
        sources[sale_id][entry_id] = (sales[sale_id].sold_at.date() if file_type == "legacy_csv"
                                     else created_at.date() if created_at is not None else None)
    result = {}
    for sale_id, row in sales.items():
        dates = set(sources[sale_id].values())
        if not dates:
            result[sale_id] = (row.sold_at.date(), None)
        elif None in dates:
            result[sale_id] = (None, "订单创建日期缺失，不能使用结算日期计算出库跌价")
        elif len(dates) > 1:
            result[sale_id] = (None, "同一销售的来源订单创建日期不一致，出库跌价待核对")
        else:
            result[sale_id] = (next(iter(dates)), None)
    return result


async def attach_sale_impairment(db, rows, *, include_details=False):
    sales = {row.id: row for row in rows if row.record_type == "sale"}
    if not sales:
        return
    order_dates = await _sale_order_dates(db, sales)
    batch_ids = {detail.batch_id for row in sales.values() for detail in row.cost_details}
    batch_rows = (await db.execute(select(InventoryBatch, Product.product_type).join(
        Product, Product.id == InventoryBatch.product_id).where(InventoryBatch.id.in_(batch_ids))
    )).all() if batch_ids else []
    by_batch = {batch.id: (batch, kind) for batch, kind in batch_rows}
    product_ids = {batch.product_id for batch, _kind in batch_rows}
    rule_rows = (await db.execute(select(ProductImpairmentRule).where(
        ProductImpairmentRule.product_id.in_(product_ids)
    ).order_by(ProductImpairmentRule.product_id, ProductImpairmentRule.effective_date,
               ProductImpairmentRule.id))).scalars().all() if product_ids else []
    rules = defaultdict(list)
    for rule in rule_rows:
        rules[rule.product_id].append(rule)
    rule_dates = {key: [rule.effective_date for rule in values] for key, values in rules.items()}
    type_cache = {}
    details = {key: [] for key in sales}
    for sale_id, row in sales.items():
        order_date, date_note = order_dates[sale_id]
        amount, quantity, original_cost, effective_cost = ZERO, 0, ZERO, ZERO
        invalid = row.cost_status == "needs_review" or (order_date is None and row.quantity > 0)
        notes = {date_note} if date_note and row.quantity > 0 else set()
        for detail in row.cost_details:
            quantity += detail.quantity
            original_cost += (detail.base_unit_cost or ZERO) * detail.quantity
            total_cost = detail.unit_cost * detail.quantity
            effective_cost += total_cost
            found = by_batch.get(detail.batch_id)
            batch, current_kind = found if found else (None, None)
            arrival_date = batch.arrived_at.date() if batch else None
            days = (order_date - arrival_date).days if arrival_date is not None and order_date is not None else None
            kind, rate = None, None
            if batch and order_date is not None:
                key = (batch.product_id, order_date)
                if key not in type_cache:
                    history = rules.get(batch.product_id, [])
                    index = bisect_right(rule_dates.get(batch.product_id, []), order_date) - 1
                    # 沿用旧数据兼容策略，但明确提示历史类型是推定值。
                    chosen = history[index] if index >= 0 else history[0] if history else None
                    note = ("首条规则晚于订单建立日，按首条已知规则推定，历史待核对" if history and index < 0 else
                            "历史规则缺失，按当前配置推定，历史待核对" if not history else None)
                    type_cache[key] = (normalized_product_type(chosen.product_type if chosen else current_kind), note)
                kind, note = type_cache[key]
                if note:
                    notes.add(note)
                rate = daily_impairment_rate(order_date, kind)
            line_invalid = (batch is None or batch.product_id != row.product_id or batch.voided_at is not None
                            or days is None or days < 0 or detail.quantity <= 0
                            or detail.quantity > batch.quantity or detail.unit_cost < 0)
            units = None if line_invalid else Decimal(days) * rate * detail.quantity
            line_amount = None if units is None else units * detail.unit_cost
            invalid = invalid or line_invalid
            if line_amount is not None:
                amount += line_amount
            if include_details:
                details[sale_id].append({"batch_id": detail.batch_id, "batch_no": detail.batch_no,
                    "quantity": detail.quantity, "unit_cost": detail.unit_cost,
                    "arrival_date": arrival_date, "order_date": order_date, "age_days": days,
                    "product_type": kind, "daily_rate": rate, "total_cost_cny": total_cost,
                    "impairment_units": units, "impairment_cny": line_amount})
        costs_known = all(detail.base_unit_cost is not None for detail in row.cost_details)
        invalid = (invalid or not costs_known or original_cost != row.original_total_cost
                   or (effective_cost - row.total_cost).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) != ZERO
                   or quantity != row.quantity or row.quantity < 0)
        if invalid:
            for line in details[sale_id]:
                line["impairment_units"] = line["impairment_cny"] = None
        row.impairment_cny = None if invalid else amount
        if invalid:
            notes.add("出库明细缺失、作废、日期或成本异常，出库商品跌价待核对")
        row.impairment_review_reason = "；".join(sorted(notes)) or None
    return details if include_details else None
