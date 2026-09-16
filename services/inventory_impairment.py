"""Shared inventory-impairment rules for reports and accounting snapshots."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


IMPAIRMENT_RULE_EFFECTIVE_DATE = date(2026, 9, 1)
PRODUCT_TYPE_STABLE = "stable"
PRODUCT_TYPE_NEW = "new"
VALID_PRODUCT_TYPES = {PRODUCT_TYPE_STABLE, PRODUCT_TYPE_NEW}
MAX_IMPAIRMENT_RATE = Decimal("0.9")


@dataclass(frozen=True)
class InventoryLayer:
    batch_id: int
    product_id: int
    store_id: int | None
    arrived_at: date
    quantity: int
    product_type: str
    safe_stock_quantity: int


@dataclass(frozen=True)
class BatchImpairment:
    provision_quantity: int
    rate: Decimal


def normalized_product_type(value: str | None) -> str:
    return value if value in VALID_PRODUCT_TYPES else PRODUCT_TYPE_STABLE


def impairment_rate(as_of: date, arrived_at: date, product_type: str) -> Decimal:
    """Return the cumulative rate for one batch as of a reporting date."""
    age_days = max((as_of - arrived_at).days, 0)
    return min(
        Decimal(age_days) * daily_impairment_rate(as_of, product_type),
        MAX_IMPAIRMENT_RATE,
    )


def daily_impairment_rate(as_of: date, product_type: str) -> Decimal:
    if (
        as_of >= IMPAIRMENT_RULE_EFFECTIVE_DATE
        and normalized_product_type(product_type) == PRODUCT_TYPE_NEW
    ):
        return Decimal("0.005")
    return Decimal("0.01")


def batch_impairments(
    layers: list[InventoryLayer], as_of: date
) -> dict[int, BatchImpairment]:
    """Allocate safe stock and return each batch's provision quantity and rate.

    From September 2026, stable products exempt the newest remaining units up to
    their product-level safe-stock quantity. Older units are the excess inventory.
    """
    grouped: defaultdict[int, list[InventoryLayer]] = defaultdict(list)
    for layer in layers:
        if layer.quantity > 0:
            grouped[layer.product_id].append(layer)

    results: dict[int, BatchImpairment] = {}
    for group_layers in grouped.values():
        product_type = normalized_product_type(group_layers[0].product_type)
        safe_stock_remaining = (
            max(0, int(group_layers[0].safe_stock_quantity))
            if as_of >= IMPAIRMENT_RULE_EFFECTIVE_DATE
            and product_type == PRODUCT_TYPE_STABLE
            else 0
        )
        for layer in sorted(
            group_layers,
            key=lambda item: (item.arrived_at, item.batch_id),
            reverse=True,
        ):
            protected_quantity = min(layer.quantity, safe_stock_remaining)
            safe_stock_remaining -= protected_quantity
            results[layer.batch_id] = BatchImpairment(
                provision_quantity=layer.quantity - protected_quantity,
                rate=impairment_rate(as_of, layer.arrived_at, product_type),
            )
    return results
