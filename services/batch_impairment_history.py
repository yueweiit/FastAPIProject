"""批次归属生效历史；写入方持有批次锁，与现有修改日志在同一事务。"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from models import BatchImpairmentRule, BatchImpairmentRuleStore, Store, User


@dataclass(frozen=True)
class StoreRule:
    effective_date: date
    stores: list[dict]
    is_explicit: bool = True


def business_today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


async def load_rules(db, batch_ids, *, lock=False):
    if not batch_ids:
        return {}
    if lock:
        headers = (await db.execute(select(BatchImpairmentRule.id, BatchImpairmentRule.batch_id,
            BatchImpairmentRule.effective_date, BatchImpairmentRule.is_explicit)
            .where(BatchImpairmentRule.batch_id.in_(batch_ids))
            .order_by(BatchImpairmentRule.batch_id, BatchImpairmentRule.effective_date)
            .with_for_update())).all()
        ids = [row.id for row in headers]
        pairs = (await db.execute(select(BatchImpairmentRuleStore.rule_id, BatchImpairmentRuleStore.store_id)
            .where(BatchImpairmentRuleStore.rule_id.in_(ids))
            .order_by(BatchImpairmentRuleStore.rule_id, BatchImpairmentRuleStore.store_id)
            .with_for_update())).all() if ids else []
        store_ids = {row.store_id for row in pairs}
        stores = {s.id: {"id": s.id, "name": s.name, "is_active": s.is_active}
                  for s in (await db.execute(select(Store).where(Store.id.in_(store_ids)))).scalars().all()} if store_ids else {}
        grouped = defaultdict(list)
        for row in pairs:
            grouped[row.rule_id].append(stores[row.store_id])
        result = defaultdict(list)
        for row in headers:
            result[row.batch_id].append(StoreRule(row.effective_date, grouped[row.id], row.is_explicit))
        return dict(result)
    rows = (await db.execute(select(BatchImpairmentRule.batch_id, BatchImpairmentRule.id,
        BatchImpairmentRule.effective_date, BatchImpairmentRule.is_explicit,
        Store.id.label("store_id"), Store.name, Store.is_active)
        .outerjoin(BatchImpairmentRuleStore, BatchImpairmentRuleStore.rule_id == BatchImpairmentRule.id)
        .outerjoin(Store, Store.id == BatchImpairmentRuleStore.store_id)
        .where(BatchImpairmentRule.batch_id.in_(batch_ids))
        .order_by(BatchImpairmentRule.batch_id, BatchImpairmentRule.effective_date, Store.id))).all()
    grouped = {}
    for row in rows:
        rule = grouped.setdefault((row.batch_id, row.id), StoreRule(row.effective_date, [], row.is_explicit))
        if row.store_id is not None:
            rule.stores.append({"id": row.store_id, "name": row.name, "is_active": row.is_active})
    result = defaultdict(list)
    for (batch_id, _id), rule in grouped.items():
        result[batch_id].append(rule)
    return dict(result)


async def save_change(db, batch, old_stores, new_stores, history, mode, *, arrived_at):
    """历史更正替换整段规则；按日变更保留基线，当日再次更正合并为一条。"""
    old_ids, new_ids = [s["id"] for s in old_stores], [s["id"] for s in new_stores]
    if old_ids == new_ids and (mode == "from_change_date" or not history):
        return history
    owner = await db.get(User, batch.user_id) if batch.user_id is not None else None
    store = await db.get(Store, owner.store_id) if owner is not None and owner.store_id is not None else None
    fallback = [{"id": store.id, "name": store.name, "is_active": store.is_active}] if store else []
    today = business_today()
    if mode == "from_arrival":
        rules = [StoreRule(arrived_at.date(), new_stores or fallback, bool(new_stores))]
    else:
        rules = list(history) or [StoreRule(min(batch.arrived_at.date(), arrived_at.date(), today),
                                           old_stores or fallback, bool(old_stores))]
        # 新选择覆盖当天及尚未生效的旧基线，避免未来到货时自动切回旧归属。
        rules = [rule for rule in rules if rule.effective_date < today]
        rules.append(StoreRule(today, new_stores or fallback, bool(new_stores)))
        rules.sort(key=lambda rule: rule.effective_date)
    ids = select(BatchImpairmentRule.id).where(BatchImpairmentRule.batch_id == batch.id)
    await db.execute(delete(BatchImpairmentRuleStore).where(BatchImpairmentRuleStore.rule_id.in_(ids)))
    await db.execute(delete(BatchImpairmentRule).where(BatchImpairmentRule.batch_id == batch.id))
    for rule in rules:
        header = BatchImpairmentRule(batch_id=batch.id, effective_date=rule.effective_date,
                                     is_explicit=rule.is_explicit)
        db.add(header)
        await db.flush()
        for selected in rule.stores:
            db.add(BatchImpairmentRuleStore(rule_id=header.id, store_id=selected["id"]))
    await db.flush()
    from services.cost_layers import invalidate_finance_cache
    if isinstance(getattr(db, "info", None), dict):
        invalidate_finance_cache(db)
    return rules


def rule_at(batch, day):
    history = batch.allocation_rules
    if history:
        eligible = [rule for rule in history if rule.effective_date <= day]
        return eligible[-1] if eligible else history[0]
    return StoreRule(date.min, batch.impairment_stores or batch.default_stores, bool(batch.impairment_stores))


def rule_segments(batch, start, end):
    boundaries = sorted({start, end, *(r.effective_date for r in batch.allocation_rules
                                     if start < r.effective_date < end)})
    return [(left, right, rule_at(batch, left)) for left, right in zip(boundaries, boundaries[1:])]
