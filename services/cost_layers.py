"""不修改原批次/出库成本；已确认补差作为追溯成本层参与查询。"""
from decimal import Decimal

from sqlalchemy import Numeric, cast, func, select

from models import InventoryBatch, InventoryCostAdjustment, InventoryCostAdjustmentLine
from services.finance_config import financial_controls_enabled

ZERO = Decimal(0)
COST_CATEGORIES = ("purchase", "head_shipping", "last_mile", "other")


async def load_cost_layers(db, batch_ids, *, lock=False):
    if not financial_controls_enabled() or not batch_ids:
        return {}
    info = getattr(db, "info", {})
    cache = info.setdefault("financial_cost_layers", {})
    missing = set(batch_ids) if lock else set(batch_ids) - set(cache)
    if missing:
        stmt = select(InventoryCostAdjustmentLine, InventoryCostAdjustment).join(
            InventoryCostAdjustment, InventoryCostAdjustment.id == InventoryCostAdjustmentLine.adjustment_id
        ).where(InventoryCostAdjustmentLine.batch_id.in_(missing)).order_by(
            InventoryCostAdjustmentLine.batch_id, InventoryCostAdjustmentLine.id)
        if lock:
            # 已持有批次锁；用当前读避开InnoDB旧快照和会话缓存，保证负向补差校验准确。
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        rows = (await db.execute(stmt)).all()
        for key in missing:
            cache[key] = []
        for line, adjustment in rows:
            cache[line.batch_id].append((line, adjustment))
    return {key: cache[key] for key in batch_ids}


def unit_adjustment(layers):
    return sum((line.amount_cny / Decimal(line.quantity) for line, _header in layers), ZERO)


def category_adjustments(layers):
    result = {key: ZERO for key in COST_CATEGORIES}
    for line, header in layers:
        result[header.category] += line.amount_cny / Decimal(line.quantity)
    return result


def cost_state(layers):
    reversed_ids = {header.reverses_id for _line, header in layers if header.reverses_id}
    latest = {}
    for _line, header in layers:
        if header.id not in reversed_ids and not header.reverses_id:
            if header.category not in latest or header.id > latest[header.category].id:
                latest[header.category] = header
    final = {key for key, header in latest.items() if header.is_final}
    return "actual" if final == set(COST_CATEGORIES) else "partial" if final else "unverified"


def invalidate_finance_cache(db):
    for key in list(db.info):
        if key == "financial_cost_layers" or (isinstance(key, tuple) and key[0] == "holding_inventory_provisions"):
            db.info.pop(key, None)


class AdjustedBatch:
    def __init__(self, original, layers):
        self.original = original
        self.layers = layers
        self.base_unit_cost = original.unit_cost
        self.cost_adjustment_per_unit = unit_adjustment(layers)
        self.unit_cost = original.unit_cost + self.cost_adjustment_per_unit
        self.cost_status = cost_state(layers)

    def __getattr__(self, key):
        return getattr(self.original, key)


def adjusted_cost_expression():
    if not financial_controls_enabled():
        return InventoryBatch.unit_cost
    # MySQL除法的结果小数位由被除数决定；先扩展金额，避免小额补差/大数量被舍为0。
    delta = select(func.sum(cast(InventoryCostAdjustmentLine.amount_cny, Numeric(40, 20)) /
                           cast(InventoryCostAdjustmentLine.quantity, Numeric(20, 0)))).where(
        InventoryCostAdjustmentLine.batch_id == InventoryBatch.id
    ).correlate(InventoryBatch).scalar_subquery()
    return InventoryBatch.unit_cost + func.coalesce(delta, 0)


async def ensure_batch_cost_editable(db, batch_id):
    if not financial_controls_enabled():
        return
    from fastapi import HTTPException
    if await db.scalar(select(InventoryCostAdjustmentLine.id).where(
        InventoryCostAdjustmentLine.batch_id == batch_id
    ).limit(1)):
        raise HTTPException(409, detail={"code": "batch_has_cost_adjustments",
                                       "message": "批次已有成本调整，请追加调整或反向单，不能改写原批次。"})


async def sale_cost_context(db, sales):
    if not financial_controls_enabled() or not sales:
        return {}
    from models import SaleCostDetail
    details = (await db.execute(select(SaleCostDetail).where(
        SaleCostDetail.sale_id.in_([sale.id for sale in sales])
    ))).scalars().all()
    layers = await load_cost_layers(db, {detail.batch_id for detail in details})
    result = {}
    for detail in details:
        context = result.setdefault(detail.sale_id, {"adjustment": ZERO, "batch_deltas": {}, "states": []})
        delta = unit_adjustment(layers.get(detail.batch_id, []))
        context["adjustment"] += delta * Decimal(detail.quantity)
        context["batch_deltas"][detail.batch_id] = delta
        context["states"].append(cost_state(layers.get(detail.batch_id, [])))
    for context in result.values():
        context["cost_status"] = "actual" if all(s == "actual" for s in context["states"]) else "unverified"
    return result
