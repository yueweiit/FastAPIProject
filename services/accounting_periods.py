"""会计期间月末库存快照及自动确认。"""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    AccountingPeriod,
    InventoryBatch,
    InventoryPeriodSnapshot,
    Sale,
    SaleCostDetail,
    User,
)


def previous_month_end(value: date) -> date:
    return value.replace(day=1) - timedelta(days=1)


def period_confirmation_deadline(period: AccountingPeriod) -> datetime:
    """月末后的完整一天结束时，仍未操作的期间自动确认。"""
    return datetime.combine(period.period_end + timedelta(days=1), time.max)


def _local_now(period: AccountingPeriod, now: datetime | None = None) -> datetime:
    if now is not None:
        if now.tzinfo is not None:
            try:
                return now.astimezone(ZoneInfo(period.timezone)).replace(tzinfo=None)
            except ZoneInfoNotFoundError:
                return now.replace(tzinfo=None)
        return now
    try:
        return datetime.now(ZoneInfo(period.timezone)).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        return datetime.now()


async def _snapshot_rows(
    db: AsyncSession, period: AccountingPeriod
) -> list[InventoryPeriodSnapshot]:
    cutoff = datetime.combine(period.period_end + timedelta(days=1), time.min)
    batch_rows = (
        await db.execute(
            select(InventoryBatch, User.store_id)
            .outerjoin(User, User.id == InventoryBatch.user_id)
            .where(InventoryBatch.arrived_at < cutoff)
            .order_by(InventoryBatch.arrived_at, InventoryBatch.id)
        )
    ).all()
    if not batch_rows:
        return []

    batch_ids = [batch.id for batch, _ in batch_rows]
    deductions = (
        await db.execute(
            select(
                SaleCostDetail.batch_id,
                func.coalesce(
                    func.sum(
                        case(
                            (Sale.sold_at < cutoff, SaleCostDetail.quantity),
                            else_=0,
                        )
                    ),
                    0,
                ).label("deducted_quantity"),
            )
            .join(Sale, Sale.id == SaleCostDetail.sale_id)
            .where(SaleCostDetail.batch_id.in_(batch_ids))
            .group_by(SaleCostDetail.batch_id)
        )
    ).all()
    deducted_by_batch = {row.batch_id: int(row.deducted_quantity) for row in deductions}

    snapshots = []
    for batch, store_id in batch_rows:
        quantity = max(0, int(batch.quantity) - deducted_by_batch.get(batch.id, 0))
        unit_cost = Decimal(batch.unit_cost or 0)
        age_days = max((period.period_end - batch.arrived_at.date()).days, 0)
        impairment_rate = min(Decimal(age_days) * Decimal("0.01"), Decimal("0.9"))
        inventory_amount = unit_cost * quantity
        impairment_amount = inventory_amount * impairment_rate
        snapshots.append(
            InventoryPeriodSnapshot(
                accounting_period_id=period.id,
                batch_id=batch.id,
                product_id=batch.product_id,
                store_id=store_id,
                quantity=quantity,
                unit_cost=unit_cost,
                inventory_amount=inventory_amount,
                impairment_rate=impairment_rate,
                impairment_amount=impairment_amount,
                book_value=inventory_amount - impairment_amount,
            )
        )
    return snapshots


async def create_period_snapshot(
    db: AsyncSession, period: AccountingPeriod
) -> bool:
    """创建一次不可变快照；返回是否新创建。"""
    if period.snapshot_version:
        return False
    snapshots = await _snapshot_rows(db, period)
    db.add_all(snapshots)
    period.snapshot_version = 1
    await db.flush()
    return True


async def prepare_period_snapshot(
    db: AsyncSession, period: AccountingPeriod
) -> bool:
    if period.status != "open":
        return False
    created = await create_period_snapshot(db, period)
    period.status = "pending_confirmation"
    return created


async def auto_confirm_expired_periods(
    db: AsyncSession, now: datetime | None = None
) -> list[int]:
    """准备到期期间，并将超过确认窗口的期间自动确认。"""
    periods = (
        await db.execute(
            select(AccountingPeriod).where(
                AccountingPeriod.status.in_(("open", "pending_confirmation")),
            )
        )
    ).scalars().all()
    changed: list[int] = []
    for period in periods:
        period_now = _local_now(period, now)
        if period.period_end > period_now.date():
            continue
        if period.status == "open":
            await prepare_period_snapshot(db, period)
            changed.append(period.id)
        if period_now >= period_confirmation_deadline(period):
            period.status = "auto_closed"
            period.closed_at = period_now
            period.closed_by_user_id = None
            changed.append(period.id)
    if changed:
        await db.commit()
    return sorted(set(changed))


async def ensure_monthly_period(
    db: AsyncSession, month_end: date, timezone: str = "Asia/Shanghai"
) -> AccountingPeriod:
    """报表需要历史月末数据时，确保该月存在一个期间。"""
    month_start = month_end.replace(day=1)
    period = (
        await db.execute(
            select(AccountingPeriod)
            .where(
                AccountingPeriod.period_start == month_start,
                AccountingPeriod.period_end == month_end,
            )
            .order_by(AccountingPeriod.id.desc())
        )
    ).scalars().first()
    if period:
        return period
    period = AccountingPeriod(
        period_start=month_start,
        period_end=month_end,
        timezone=timezone,
        status="open",
        snapshot_version=0,
    )
    db.add(period)
    await db.flush()
    return period


async def get_confirmed_period(
    db: AsyncSession, period_end: date
) -> AccountingPeriod | None:
    return (
        await db.execute(
            select(AccountingPeriod)
            .where(
                AccountingPeriod.period_end == period_end,
                AccountingPeriod.status.in_(("closed", "auto_closed")),
                AccountingPeriod.snapshot_version > 0,
            )
            .order_by(AccountingPeriod.id.desc())
        )
    ).scalars().first()


async def snapshot_count(db: AsyncSession, period_id: int) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(InventoryPeriodSnapshot)
            .where(InventoryPeriodSnapshot.accounting_period_id == period_id)
        )
        or 0
    )
