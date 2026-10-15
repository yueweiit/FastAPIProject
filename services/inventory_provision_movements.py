"""按实际持有期间重建新增准备、销售转销及截止日余额；不读写数据库。"""

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from services.inventory_impairment import (
    ImpairmentRule, InventoryLayer, MAX_IMPAIRMENT_RATE, PRODUCT_TYPE_STABLE,
    daily_impairment_rate, normalized_product_type,
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class ProvisionSale:
    detail_id: int
    sale_id: int
    batch_id: int
    sold_at: datetime
    quantity: int
    store_id: int | None = None


@dataclass(frozen=True)
class ProvisionBalance:
    quantity: int
    provision_quantity: int | None
    rate: Decimal | None
    impairment_units: Decimal | None
    period_impairment_units: Decimal | None = ZERO


@dataclass(frozen=True)
class ProvisionAccrual:
    batch_id: int
    day: date
    units: Decimal | None


@dataclass(frozen=True)
class ProvisionWriteoff:
    sale: ProvisionSale
    units: Decimal | None


@dataclass(frozen=True)
class _PeriodTotals:
    days: tuple[date, ...]
    totals: tuple[Decimal, ...]
    unknown_counts: tuple[int, ...]

    def total(self, start: date, end: date) -> Decimal | None:
        if start >= end:
            return ZERO
        left, right = bisect_left(self.days, start), bisect_left(self.days, end)
        if self.unknown_counts[right] != self.unknown_counts[left]:
            return None
        return self.totals[right] - self.totals[left]


def _build_period_totals(events: Iterable[tuple[int, date, Decimal | None]]) -> dict[int, _PeriodTotals]:
    """按批次/日期汇总并建立前缀和；未知次数独立累计，不能把未知金额当0。"""
    grouped: defaultdict[int, dict[date, Decimal | None]] = defaultdict(dict)
    for batch_id, day, units in events:
        previous = grouped[batch_id].get(day, ZERO)
        grouped[batch_id][day] = None if previous is None or units is None else previous + units
    indexes = {}
    for batch_id, daily in grouped.items():
        days = tuple(sorted(daily))
        totals, unknown_counts = [ZERO], [0]
        for day in days:
            units = daily[day]
            totals.append(totals[-1] + (units if units is not None else ZERO))
            unknown_counts.append(unknown_counts[-1] + (units is None))
        indexes[batch_id] = _PeriodTotals(days, tuple(totals), tuple(unknown_counts))
    return indexes


@dataclass
class ProvisionReplay:
    balances: dict[date, dict[int, ProvisionBalance]]
    accruals: list[ProvisionAccrual]
    writeoffs: list[ProvisionWriteoff]
    notes: dict[int, str]
    _accrual_totals: dict[int, _PeriodTotals] = field(init=False, repr=False, compare=False)
    _writeoff_totals: dict[int, _PeriodTotals] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        # 回放完成后供报表只读复用，逐批期间查询不再遍历全局流水。
        self._accrual_totals = _build_period_totals(
            (item.batch_id, item.day, item.units) for item in self.accruals
        )
        self._writeoff_totals = _build_period_totals(
            (item.sale.batch_id, item.sale.sold_at.date(), item.units) for item in self.writeoffs
        )

    def accrued_units(self, batch_id: int, start: date, end: date) -> Decimal | None:
        writeoffs = self._writeoff_totals.get(batch_id)
        if writeoffs is not None and writeoffs.total(start, end) is None:
            return None
        accruals = self._accrual_totals.get(batch_id)
        return accruals.total(start, end) if accruals is not None else ZERO

    def written_off_units(self, batch_id: int, start: date, end: date) -> Decimal | None:
        writeoffs = self._writeoff_totals.get(batch_id)
        return writeoffs.total(start, end) if writeoffs is not None else ZERO


@dataclass(frozen=True)
class _Part:
    quantity: int
    rate: Decimal = ZERO
    period_rate: Decimal = ZERO


def _merge(parts: list[_Part]) -> list[_Part]:
    merged: list[_Part] = []
    for part in parts:
        if not part.quantity:
            continue
        if merged and (merged[-1].rate, merged[-1].period_rate) == (part.rate, part.period_rate):
            previous = merged.pop()
            merged.append(_Part(previous.quantity + part.quantity, part.rate, part.period_rate))
        else:
            merged.append(part)
    return merged


def replay_inventory_provisions(
    original_layers: list[InventoryLayer], sales: list[ProvisionSale], as_of: date,
    *, balance_dates: list[date] | None = None,
) -> ProvisionReplay:
    """入库日0天；按日先增提再出库，批内按整数数量层FIFO转销。

    安全库存保护最新批次、批内靠后的数量层。各层保留已经形成的比例，
    只有当天未保护部分增加比例。日期/数量异常传播未知金额，不修改事实。
    """
    requested = {day for day in (balance_dates or []) if day <= as_of} | {as_of}
    month_start = as_of.replace(day=1)
    layers = {layer.batch_id: layer for layer in original_layers}
    notes: defaultdict[int, set[str]] = defaultdict(set)
    invalid_from: dict[int, date] = {}
    invalid_sale_days: set[tuple[int, date]] = set()
    by_product: defaultdict[int, list[InventoryLayer]] = defaultdict(list)
    for layer in original_layers:
        by_product[layer.product_id].append(layer)
    eligible_sales = sorted(
        (sale for sale in sales if sale.sold_at.date() <= as_of and sale.batch_id in layers),
        key=lambda sale: (sale.sold_at, sale.sale_id, sale.detail_id),
    )
    totals: defaultdict[int, int] = defaultdict(int)
    for sale in eligible_sales:
        layer = layers[sale.batch_id]
        totals[sale.batch_id] += sale.quantity
        reason = (
            "销售日期早于批次入库日期，准备金额待核对" if sale.sold_at.date() < layer.arrived_at
            else "FIFO出库数量异常，准备金额待核对"
            if sale.quantity <= 0 or totals[sale.batch_id] > layer.quantity else ""
        )
        if reason:
            invalid_sale_days.add((layer.batch_id, sale.sold_at.date()))
            invalid_from[layer.batch_id] = min(
                invalid_from.get(layer.batch_id, sale.sold_at.date()), sale.sold_at.date()
            )
            notes[layer.batch_id].add(reason)
    # 直接异常影响数量可信度；被共享安全库存影响的准备未知，不是新的数量异常源。
    source_invalid_from = invalid_from.copy()

    rules_by_product: dict[int, list[ImpairmentRule]] = {}
    for product_id, group in by_product.items():
        earliest = min(layer.arrived_at for layer in group)
        rules = sorted(
            {rule.effective_date: rule for layer in group for rule in layer.rules
             if rule.effective_date <= as_of}.values(), key=lambda rule: rule.effective_date,
        )
        if not rules:
            rules = [ImpairmentRule(group[0].product_type, group[0].safe_stock_quantity, earliest)]
            message = "历史规则缺失，按当前配置推定，历史待核对"
        elif rules[0].effective_date > earliest:
            rules.insert(0, ImpairmentRule(rules[0].product_type, rules[0].safe_stock_quantity, earliest))
            message = "首条规则晚于入库，按首条已知规则向前推定，历史待核对"
        else:
            message = ""
        rules_by_product[product_id] = rules
        if message:
            for layer in group:
                notes[layer.batch_id].add(message)

    arrivals: defaultdict[date, list[InventoryLayer]] = defaultdict(list)
    by_day: defaultdict[date, list[ProvisionSale]] = defaultdict(list)
    for layer in original_layers:
        if layer.arrived_at <= as_of:
            arrivals[layer.arrived_at].append(layer)
    for sale in eligible_sales:
        by_day[sale.sold_at.date()].append(sale)
    parts: dict[int, list[_Part]] = {batch_id: [] for batch_id in layers}
    deducted: defaultdict[int, int] = defaultdict(int)
    accruals: list[ProvisionAccrual] = []
    writeoffs: list[ProvisionWriteoff] = []
    balances: dict[date, dict[int, ProvisionBalance]] = {}
    first_day = min([as_of, *requested, *arrivals, *by_day])
    day = first_day
    while day <= as_of:
        for layer in arrivals.get(day, []):
            parts[layer.batch_id] = [_Part(max(0, layer.quantity - deducted[layer.batch_id]))]
        for product_id, group in by_product.items():
            active_rules = [rule for rule in rules_by_product[product_id] if rule.effective_date <= day]
            if not active_rules:
                continue
            rule = active_rules[-1]
            safe = max(0, rule.safe_stock_quantity) if normalized_product_type(rule.product_type) == PRODUCT_TYPE_STABLE else 0
            daily_rate = daily_impairment_rate(day, rule.product_type)
            quantities = {layer.batch_id: sum(part.quantity for part in parts[layer.batch_id]) for layer in group}
            if safe and any(
                layer.batch_id in source_invalid_from and day >= source_invalid_from[layer.batch_id]
                and (day < layer.arrived_at or quantities[layer.batch_id] > 0
                     or (layer.batch_id, day) in invalid_sale_days)
                for layer in group
            ):
                # 只影响规则生效后当日已在库的批次；不倒改旧日，也不预先污染后续入库。
                for related in group:
                    if day <= related.arrived_at or not quantities[related.batch_id]:
                        continue
                    invalid_from[related.batch_id] = min(invalid_from.get(related.batch_id, day), day)
                    if not (related.batch_id in source_invalid_from and day >= source_invalid_from[related.batch_id]):
                        notes[related.batch_id].add("同商品异常批次影响共享安全库存，准备金额待核对")
            for layer in sorted(group, key=lambda item: (item.arrived_at, item.batch_id), reverse=True):
                batch_parts = parts[layer.batch_id]
                quantity = quantities[layer.batch_id]
                protected = min(quantity, safe)
                safe -= protected
                if not quantity or day <= layer.arrived_at:
                    continue
                if layer.batch_id in invalid_from and day >= invalid_from[layer.batch_id]:
                    accruals.append(ProvisionAccrual(layer.batch_id, day, None))
                    continue
                excess = quantity - protected
                updated: list[_Part] = []
                added = ZERO
                for part in batch_parts:
                    eligible = min(part.quantity, excess)
                    excess -= eligible
                    if eligible:
                        increment = min(daily_rate, MAX_IMPAIRMENT_RATE - part.rate)
                        updated.append(_Part(eligible, part.rate + increment,
                                             part.period_rate + (increment if day >= month_start else ZERO)))
                        added += Decimal(eligible) * increment
                    if part.quantity > eligible:
                        updated.append(_Part(part.quantity - eligible, part.rate, part.period_rate))
                parts[layer.batch_id] = _merge(updated)
                if added:
                    accruals.append(ProvisionAccrual(layer.batch_id, day, added))
        for sale in by_day.get(day, []):
            batch_parts = parts[sale.batch_id]
            remaining = max(0, sale.quantity)
            transferred = ZERO
            updated = []
            for part in batch_parts:
                taken = min(remaining, part.quantity)
                remaining -= taken
                transferred += Decimal(taken) * part.rate
                if part.quantity > taken:
                    updated.append(_Part(part.quantity - taken, part.rate, part.period_rate))
            parts[sale.batch_id] = updated
            deducted[sale.batch_id] += sale.quantity
            unknown = sale.batch_id in invalid_from and day >= invalid_from[sale.batch_id]
            writeoffs.append(ProvisionWriteoff(sale, None if unknown else transferred))
        if day in requested:
            snapshot = {}
            for batch_id, layer in layers.items():
                batch_parts = parts[batch_id]
                quantity = sum(part.quantity for part in batch_parts)
                unknown = quantity > 0 and batch_id in invalid_from and day >= invalid_from[batch_id]
                units = sum((Decimal(part.quantity) * part.rate for part in batch_parts), ZERO)
                provision_quantity = sum(part.quantity for part in batch_parts if part.rate)
                snapshot[batch_id] = ProvisionBalance(
                    quantity=quantity,
                    provision_quantity=None if unknown else provision_quantity,
                    rate=None if unknown else units / provision_quantity if provision_quantity else ZERO,
                    impairment_units=None if unknown else units,
                    period_impairment_units=None if unknown else sum(
                        (Decimal(part.quantity) * part.period_rate for part in batch_parts), ZERO
                    ),
                )
            balances[day] = snapshot
        day += timedelta(days=1)
    return ProvisionReplay(balances, accruals, writeoffs,
                           {batch_id: "；".join(sorted(messages)) for batch_id, messages in notes.items()})
