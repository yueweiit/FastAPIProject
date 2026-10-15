"""2026-10-15早期静态余额算法，仅保留历史回归。

现行07及损益使用inventory_provision_movements按实际持有期间回放。
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from services.inventory_impairment import BatchImpairment, InventoryLayer, batch_impairments


@dataclass(frozen=True)
class InventoryBalanceImpairment:
    quantity: int
    previous_quantity: int
    current: BatchImpairment
    previous: BatchImpairment


def inventory_balance_impairments(
    original_layers: list[InventoryLayer],
    deductions: list[tuple[int, date, int]],
    as_of: date,
) -> dict[int, InventoryBalanceImpairment]:
    """累计比例只作用一次；本月增提仅计算截止日仍在库的数量。"""
    month_start = as_of.replace(day=1)
    previous_end = month_start - timedelta(days=1)
    layers = [layer for layer in original_layers if layer.arrived_at <= as_of]
    batch_ids = {layer.batch_id for layer in layers}
    sold: defaultdict[int, int] = defaultdict(int)
    previous_sold: defaultdict[int, int] = defaultdict(int)
    for batch_id, sold_on, quantity in deductions:
        if batch_id not in batch_ids or sold_on > as_of:
            continue
        sold[batch_id] += quantity
        if sold_on <= previous_end:
            previous_sold[batch_id] += quantity

    current_layers = [
        replace(layer, quantity=max(0, layer.quantity - sold[layer.batch_id]))
        for layer in layers
    ]
    previous_layers = [
        replace(layer, quantity=max(0, layer.quantity - previous_sold[layer.batch_id]))
        for layer in layers if layer.arrived_at <= previous_end
    ]
    current = batch_impairments(current_layers, as_of, period_start=month_start)
    previous = batch_impairments(previous_layers, previous_end)
    previous_quantities = {layer.batch_id: layer.quantity for layer in previous_layers}
    zero = BatchImpairment(0, Decimal("0"), Decimal("0"))
    return {
        layer.batch_id: InventoryBalanceImpairment(
            quantity=layer.quantity,
            previous_quantity=previous_quantities.get(layer.batch_id, 0),
            current=current.get(layer.batch_id, zero),
            previous=previous.get(layer.batch_id, zero),
        )
        for layer in current_layers
    }
