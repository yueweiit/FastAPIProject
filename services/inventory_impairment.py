"""Shared inventory-impairment rules for reports and accounting snapshots."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


IMPAIRMENT_RULE_EFFECTIVE_DATE = date(2026, 9, 1)
PRODUCT_TYPE_STABLE = "stable"
PRODUCT_TYPE_NEW = "new"
VALID_PRODUCT_TYPES = {PRODUCT_TYPE_STABLE, PRODUCT_TYPE_NEW}
MAX_IMPAIRMENT_RATE = Decimal("0.9")


@dataclass(frozen=True)
class ImpairmentRule:
    product_type: str
    safe_stock_quantity: int
    effective_date: date


@dataclass(frozen=True)
class InventoryLayer:
    batch_id: int
    product_id: int
    store_id: int | None
    arrived_at: date
    quantity: int
    product_type: str
    safe_stock_quantity: int
    rules: tuple[ImpairmentRule, ...] = ()


@dataclass(frozen=True)
class BatchImpairment:
    provision_quantity: int
    rate: Decimal
    impairment_units: Decimal


def normalized_product_type(value: str | None) -> str:
    return value if value in VALID_PRODUCT_TYPES else PRODUCT_TYPE_STABLE


def impairment_rate(as_of: date, arrived_at: date, product_type: str) -> Decimal:
    """Return the legacy cumulative rate for one batch as of a reporting date."""
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


def _safe_stock_protection(layers: list[InventoryLayer], quantity: int) -> dict[int, int]:
    """Allocate a product's safe stock to newest batches first."""
    protected: dict[int, int] = {}
    for layer in sorted(
        layers, key=lambda item: (item.arrived_at, item.batch_id), reverse=True
    ):
        protected_quantity = min(layer.quantity, quantity)
        protected[layer.batch_id] = protected_quantity
        quantity -= protected_quantity
    return protected


def _legacy_batch_impairments(
    grouped: defaultdict[int, list[InventoryLayer]], as_of: date
) -> dict[int, BatchImpairment]:
    """Keep the existing calculation for products without rule history."""
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
            provision_quantity = layer.quantity - protected_quantity
            rate = impairment_rate(as_of, layer.arrived_at, product_type)
            results[layer.batch_id] = BatchImpairment(
                provision_quantity=provision_quantity,
                rate=rate,
                impairment_units=Decimal(provision_quantity) * rate,
            )
    return results


def _historical_batch_impairments(
    group_layers: list[InventoryLayer], as_of: date
) -> dict[int, BatchImpairment]:
    rules = sorted(
        {
            (rule.effective_date, normalized_product_type(rule.product_type),
             max(0, int(rule.safe_stock_quantity))): rule
            for rule in group_layers[0].rules
            if rule.effective_date <= as_of
        }.values(),
        key=lambda rule: rule.effective_date,
    )
    if not rules:
        return _legacy_batch_impairments(defaultdict(list, {0: group_layers}), as_of)

    components = {
        layer.batch_id: [(layer.quantity, Decimal("0"))]
        for layer in group_layers
    }
    for index, rule in enumerate(rules):
        segment_end = (
            rules[index + 1].effective_date
            if index + 1 < len(rules)
            else as_of + timedelta(days=1)
        )
        protected = (
            _safe_stock_protection(group_layers, max(0, int(rule.safe_stock_quantity)))
            if normalized_product_type(rule.product_type) == PRODUCT_TYPE_STABLE
            else {}
        )
        for layer in group_layers:
            days = max(
                (min(as_of + timedelta(days=1), segment_end)
                 - max(layer.arrived_at + timedelta(days=1), rule.effective_date)).days,
                0,
            )
            if not days:
                continue
            rate = Decimal(days) * daily_impairment_rate(
                segment_end - timedelta(days=1), rule.product_type
            )
            protected_quantity = (
                protected.get(layer.batch_id, 0)
                if normalized_product_type(rule.product_type) == PRODUCT_TYPE_STABLE
                else 0
            )
            updated_components = []
            for quantity, accumulated_rate in components[layer.batch_id]:
                protected_part = min(quantity, protected_quantity)
                protected_quantity -= protected_part
                if protected_part:
                    updated_components.append((protected_part, accumulated_rate))
                excess_part = quantity - protected_part
                if excess_part:
                    updated_components.append((
                        excess_part,
                        min(accumulated_rate + rate, MAX_IMPAIRMENT_RATE),
                    ))
            components[layer.batch_id] = updated_components

    results: dict[int, BatchImpairment] = {}
    for layer in group_layers:
        provision_quantity = sum(
            quantity for quantity, rate in components[layer.batch_id] if rate
        )
        units = sum(
            Decimal(quantity) * rate for quantity, rate in components[layer.batch_id]
        )
        results[layer.batch_id] = BatchImpairment(
            provision_quantity=provision_quantity,
            rate=(units / Decimal(provision_quantity) if provision_quantity else Decimal("0")),
            impairment_units=units,
        )
    return results


def batch_impairments(
    layers: list[InventoryLayer], as_of: date
) -> dict[int, BatchImpairment]:
    """Calculate cumulative provisions, retaining amounts accrued before rule changes.

    A new-product period accrues against all remaining stock. On a later stable
    period, that accrued amount stays in place while only stock outside the safe
    quantity continues to accrue at the stable-product rate.
    """
    grouped: defaultdict[int, list[InventoryLayer]] = defaultdict(list)
    for layer in layers:
        if layer.quantity > 0:
            grouped[layer.product_id].append(layer)

    results: dict[int, BatchImpairment] = {}
    for group_layers in grouped.values():
        if group_layers[0].rules:
            results.update(_historical_batch_impairments(group_layers, as_of))
        else:
            results.update(_legacy_batch_impairments(
                defaultdict(list, {0: group_layers}), as_of
            ))
    return results
