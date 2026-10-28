"""批次跌价归属：手工选择、整体更正、按金额均分；不参与FIFO扣库。"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy import delete, select

from models import BatchImpairmentStore, Store


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


async def validate_stores(db, ids, *, existing=()):
    ids = sorted(set(ids))
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
    def __init__(self, batch, stores=()):
        self.original = batch
        self.impairment_stores = list(stores)
        self.impairment_store_ids = [store["id"] for store in stores]
        self.impairment_store_names = [store["name"] for store in stores]

    @property
    def edit_version(self):
        base = self.original.edit_version
        if not self.impairment_store_ids:
            return base
        return sha256(f"{base}:impairment:{','.join(map(str, self.impairment_store_ids))}".encode()).hexdigest()

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


def allocated_batch_amount(amount, batch, store_id):
    # 当前比例按整数数量层递增1%或0.5%，成本小数位再留3位即可完整表示。
    # 库存成本和准备共用此精度，避免小额成本先截至分位而准备仍有小数。
    quantum = Decimal(1).scaleb(min(-2, batch.unit_cost.as_tuple().exponent - 3))
    return allocated_amount(amount, batch.impairment_stores, store_id, quantum=quantum)


def allocated_provision_period(replay, batch, store_id, start, end, *, writeoff=False):
    """分摊累计值后取期间差，避免本月/上月/YTD各自舍入造成尾差不勾稽。"""
    totals = replay.written_off_units if writeoff else replay.accrued_units
    units = totals(batch.id, start, end)
    if units is None:
        return allocated_batch_amount(None, batch, store_id)
    before, after = totals(batch.id, date.min, start), totals(batch.id, date.min, end)
    if before is None or after is None:
        # 旧未知不把健康期间补成0，也不把过去的未知扩大到当前健康期间。
        return allocated_batch_amount(batch.unit_cost * units, batch, store_id)
    return (allocated_batch_amount(batch.unit_cost * after, batch, store_id)
            - allocated_batch_amount(batch.unit_cost * before, batch, store_id))


def allocated_provision_balance(replay, batch, store_id, day, units):
    if units is None:
        return allocated_batch_amount(None, batch, store_id)
    end = day + timedelta(days=1)
    accrued = replay.accrued_units(batch.id, date.min, end)
    written = replay.written_off_units(batch.id, date.min, end)
    if accrued is None or written is None:
        return allocated_batch_amount(batch.unit_cost * units, batch, store_id)
    return (allocated_batch_amount(batch.unit_cost * accrued, batch, store_id)
            - allocated_batch_amount(batch.unit_cost * written, batch, store_id))
