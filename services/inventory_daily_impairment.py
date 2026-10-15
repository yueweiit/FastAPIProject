"""2026-10-12历史逐日累计算法，仅保留用于复核旧口径。

当前07报表使用inventory_provision_movements；本算法不再接入查询或导出。
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from services.inventory_impairment import BatchImpairment, InventoryLayer, batch_impairments


@dataclass(frozen=True)
class DailyAccumulatedImpairment:
    quantity: int
    previous_quantity: int
    cumulative_units: Decimal
    monthly_units: Decimal
    previous_units: Decimal
    current: BatchImpairment


def daily_accumulated_impairments(
    original_layers: list[InventoryLayer],
    deductions: list[tuple[int, date, int]],
    as_of: date,
) -> dict[int, DailyAccumulatedImpairment]:
    """先扣除当天销售，再按当天累计比例计算金额并逐日相加。

    original_layers.quantity 必须是原始入库数量。入库当天比例为零，
    各日按现有商品规则历史、安全库存分配和 90% 比例上限计算。
    合计金额不封顶；售完后停止新增，已累计金额保留。本函数不写数据库，
    也不改变快照和损益使用的准备余额算法。
    """
    layers = [layer for layer in original_layers if layer.arrived_at <= as_of]
    if not layers:
        return {}
    first_day = min(layer.arrived_at for layer in layers)
    month_start = as_of.replace(day=1)
    layer_ids = {layer.batch_id for layer in layers}
    sold_by_day: defaultdict[date, defaultdict[int, int]] = defaultdict(lambda: defaultdict(int))
    previous_sold: defaultdict[int, int] = defaultdict(int)
    deducted: defaultdict[int, int] = defaultdict(int)
    for batch_id, sold_on, quantity in deductions:
        if batch_id not in layer_ids or sold_on > as_of:
            continue
        sold_by_day[sold_on][batch_id] += quantity
        if sold_on < month_start:
            previous_sold[batch_id] += quantity
        if sold_on < first_day:
            deducted[batch_id] += quantity

    cumulative = {layer.batch_id: Decimal("0") for layer in layers}
    monthly = cumulative.copy()
    current: dict[int, BatchImpairment] = {}
    day = first_day
    while day <= as_of:
        for batch_id, quantity in sold_by_day.get(day, {}).items():
            deducted[batch_id] += quantity
        daily_layers = [
            replace(layer, quantity=max(0, layer.quantity - deducted[layer.batch_id]))
            for layer in layers
            if layer.arrived_at <= day and layer.quantity > deducted[layer.batch_id]
        ]
        current = batch_impairments(daily_layers, day)
        for batch_id, provision in current.items():
            cumulative[batch_id] += provision.impairment_units
            if day >= month_start:
                monthly[batch_id] += provision.impairment_units
        day += timedelta(days=1)

    zero = BatchImpairment(0, Decimal("0"), Decimal("0"))
    return {
        layer.batch_id: DailyAccumulatedImpairment(
            quantity=max(0, layer.quantity - deducted[layer.batch_id]),
            previous_quantity=(
                max(0, layer.quantity - previous_sold[layer.batch_id])
                if layer.arrived_at < month_start else 0
            ),
            cumulative_units=cumulative[layer.batch_id],
            monthly_units=monthly[layer.batch_id],
            previous_units=cumulative[layer.batch_id] - monthly[layer.batch_id],
            current=current.get(layer.batch_id, zero),
        )
        for layer in layers
    }
