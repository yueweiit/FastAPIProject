"""批次跌价归属：手工选择、整体更正、按金额均分；不参与FIFO扣库。"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import delete, select

from models import BatchImpairmentStore, Store
from services.batch_impairment_history import rule_at, rule_segments


async def load_batch_store_allocations(db, batch_ids, *, lock=False):
    if not batch_ids:
        return {}
    if lock:
        # 只锁关联行；若连Store一起锁，互换两个批次的店铺时会产生反向锁序。
        pairs = (await db.execute(select(BatchImpairmentStore.batch_id, BatchImpairmentStore.store_id)
            .where(BatchImpairmentStore.batch_id.in_(batch_ids))
            .order_by(BatchImpairmentStore.batch_id, BatchImpairmentStore.store_id).with_for_update())).all()
        ids = {store_id for _batch, store_id in pairs}
        stores = {store.id: store for store in (await db.execute(select(Store).where(Store.id.in_(ids)))).scalars().all()} if ids else {}
        result = defaultdict(list)
        for batch_id, store_id in pairs:
            store = stores[store_id]
            result[batch_id].append({"id": store.id, "name": store.name, "is_active": store.is_active})
        return dict(result)
    stmt = select(BatchImpairmentStore.batch_id, Store.id, Store.name, Store.is_active).join(
        Store, Store.id == BatchImpairmentStore.store_id).where(
        BatchImpairmentStore.batch_id.in_(batch_ids)).order_by(BatchImpairmentStore.batch_id, Store.id)
    result = defaultdict(list)
    for batch_id, store_id, name, active in (await db.execute(stmt)).all():
        result[batch_id].append({"id": store_id, "name": name, "is_active": active})
    return dict(result)


def require_store_selection(stores):
    if not stores:
        raise HTTPException(422, detail={"code": "batch_impairment_store_required",
            "message": "请至少选择一家跌价归属店铺后再保存。"})


async def validate_stores(db, ids, *, existing=(), required=True):
    ids = sorted(set(ids))
    if required:
        require_store_selection(ids)
    stores = (await db.execute(select(Store).where(Store.id.in_(ids)).order_by(Store.id).with_for_update(read=True)
        .execution_options(populate_existing=True))).scalars().all() if ids else []
    if len(stores) != len(ids) or any(not store.is_active and store.id not in existing for store in stores):
        raise HTTPException(422, detail={"code": "batch_impairment_store_invalid", "message": "请选择存在且启用的跌价归属店铺。"})
    return [{"id": store.id, "name": store.name, "is_active": store.is_active} for store in stores]


async def replace_stores(db, batch_id, stores):
    # 调用方已锁定批次；与批次资料、日志同事务提交。遗漏字段不调用本函数。
    await db.execute(delete(BatchImpairmentStore).where(BatchImpairmentStore.batch_id == batch_id))
    for store in stores:
        db.add(BatchImpairmentStore(batch_id=batch_id, store_id=store["id"]))
    await db.flush()
    from services.cost_layers import invalidate_finance_cache
    if isinstance(getattr(db, "info", None), dict):
        invalidate_finance_cache(db)


class AssignedBatch:
    def __init__(self, batch, stores=(), rules=(), default_stores=()):
        self.original = batch
        self.impairment_stores = list(stores)
        self.impairment_store_ids = [store["id"] for store in stores]
        self.impairment_store_names = [store["name"] for store in stores]
        self.allocation_rules = list(rules)
        self.default_stores = list(default_stores)

    @property
    def impairment_store_history(self):
        return [{"effective_date": r.effective_date, "store_ids": [s["id"] for s in r.stores],
                 "store_names": [s["name"] for s in r.stores], "is_explicit": r.is_explicit}
                for r in self.allocation_rules]

    @property
    def edit_version(self):
        base = self.original.edit_version
        if self.impairment_store_ids:
            base = sha256(f"{base}:impairment:{','.join(map(str, self.impairment_store_ids))}".encode()).hexdigest()
        if self.allocation_rules:
            value = [(r.effective_date.isoformat(), r.is_explicit, [s["id"] for s in r.stores])
                     for r in self.allocation_rules]
            base = sha256(f"{base}:history:{json.dumps(value)}".encode()).hexdigest()
        return base

    def __getattr__(self, key):
        return getattr(self.original, key)


def allocated_amount(amount, stores, store_id, *, quantum=Decimal("0.01")):
    """按固定精度均分，余下最小金额单位从末ID依次分配；未知不补0。"""
    ids = sorted({store["id"] for store in stores})
    if store_id not in ids:
        return Decimal(0)
    if amount is None:
        return None
    if len(ids) == 1:
        return amount
    # 每增加一个最小单位，只增加一家份额，不重新集中尾差导致其他份额下降。
    # 批次调用使用覆盖成本/日比例的固定精度，金额均为该精度的整数倍。
    with localcontext() as context:
        parts = amount.as_tuple()
        context.prec = max(context.prec, len(parts.digits)
                           + max(0, parts.exponent - quantum.as_tuple().exponent) + 3)
        units, remainder = divmod(amount.copy_abs(), quantum)
        average, extra = divmod(int(units), len(ids))
        rank = ids.index(store_id)
        share = (average + (rank >= len(ids) - extra)) * quantum
        # 不足一个单位的部分留给下一家，跨过精度边界时原有份额也不减少。
        if rank == len(ids) - extra - 1:
            share += remainder
        return share.copy_sign(amount)


def allocated_batch_amount(amount, batch, store_id, *, stores=None):
    # 当前比例按整数数量层递增1%或0.5%，成本小数位再留3位即可完整表示。
    # 库存成本和准备共用此精度，避免小额成本先截至分位而准备仍有小数。
    quantum = Decimal(1).scaleb(min(-2, batch.unit_cost.as_tuple().exponent - 3))
    return allocated_amount(amount, batch.impairment_stores if stores is None else stores, store_id, quantum=quantum)


def _segment_start(batch, rule):
    return rule.effective_date if batch.allocation_rules and rule != batch.allocation_rules[0] else date.min


def _opening_units(replay, batch, rule):
    start = _segment_start(batch, rule)
    if start == date.min:
        return Decimal(0)
    balance = replay.balances.get(start - timedelta(days=1), {}).get(batch.id)
    if balance is not None:
        return balance.impairment_units
    accrued = replay.accrued_units(batch.id, date.min, start)
    written = replay.written_off_units(batch.id, date.min, start)
    return None if accrued is None or written is None else accrued - written


def _ambiguous_writeoff(replay, batch, store_id, start, end):
    if all(rule.is_explicit for _a, _b, rule in rule_segments(batch, start, end)):
        return False
    for item in replay.writeoffs_by_batch.get(batch.id, []):
        sale = item.sale
        if sale.batch_id != batch.id or not start <= sale.sold_at.date() < end:
            continue
        rule = rule_at(batch, sale.sold_at.date())
        if rule.is_explicit or item.units == 0:
            continue
        owner = rule.stores[0]["id"] if rule.stores else None
        if owner != sale.store_id and store_id in (owner, sale.store_id):
            return True
    return False


def allocated_provision_period(replay, batch, store_id, start, end, *, writeoff=False, explicit_only=False):
    """分摊累计值后取期间差，避免本月/上月/YTD各自舍入造成尾差不勾稽。"""
    totals = replay.written_off_units if writeoff else replay.accrued_units
    if writeoff and not explicit_only and _ambiguous_writeoff(replay, batch, store_id, start, end):
        return None
    values = []
    for left, right, rule in rule_segments(batch, start, end):
        if explicit_only and not rule.is_explicit:
            continue
        share = lambda value: allocated_batch_amount(value, batch, store_id, stores=rule.stores)
        units = totals(batch.id, left, right)
        if units is None:
            values.append(share(None))
            continue
        baseline = _segment_start(batch, rule)
        before, after = totals(batch.id, baseline, left), totals(batch.id, baseline, right)
        opening = Decimal(0) if writeoff else _opening_units(replay, batch, rule)
        if before is None or after is None or opening is None:
            values.append(share(batch.unit_cost * units))
        else:
            values.append(share(batch.unit_cost * (opening + after))
                          - share(batch.unit_cost * (opening + before)))
    return None if None in values else sum(values, Decimal(0))


def has_unassigned_provision(replay, batch, start, end):
    """按实际金额判断待分配影响；已知零成本不污染损益，未知数量仍待核对。"""
    return any(rule.is_explicit and not rule.stores and any(
        units is None or batch.unit_cost * units != 0
        for units in (replay.accrued_units(batch.id, left, right),
                      replay.written_off_units(batch.id, left, right)))
        for left, right, rule in rule_segments(batch, start, end))


def allocated_provision_balance(replay, batch, store_id, day, units):
    rule = rule_at(batch, day)
    share = lambda value: allocated_batch_amount(value, batch, store_id, stores=rule.stores)
    if units is None:
        return share(None)
    end = day + timedelta(days=1)
    start = _segment_start(batch, rule)
    accrued = replay.accrued_units(batch.id, start, end)
    written = replay.written_off_units(batch.id, start, end)
    opening = _opening_units(replay, batch, rule)
    if accrued is None or written is None or opening is None:
        return share(batch.unit_cost * units)
    return share(batch.unit_cost * (opening + accrued)) - share(batch.unit_cost * written)


def allocated_provision_transfers(replay, batch, store_id, start, end):
    """变更日开始前转移尚未转销的准备，不进入计提费用或销售转销。"""
    incoming, outgoing = [], []
    for rule in batch.allocation_rules[1:]:
        day = rule.effective_date
        if not start <= day < end:
            continue
        previous = rule_at(batch, day - timedelta(days=1))
        ids = sorted({s["id"] for s in previous.stores + rule.stores})
        if store_id is not None and store_id not in ids:
            continue
        units = _opening_units(replay, batch, rule)
        if units is None or (units != 0 and (not previous.stores or not rule.stores)):
            return None, None
        for owner in ids if store_id is None else [store_id]:
            before = allocated_provision_balance(replay, batch, owner, day - timedelta(days=1), units)
            after = allocated_batch_amount(batch.unit_cost * units, batch, owner, stores=rule.stores)
            delta = after - before
            incoming.append(max(delta, Decimal(0)))
            outgoing.append(max(-delta, Decimal(0)))
    return sum(incoming, Decimal(0)), sum(outgoing, Decimal(0))
