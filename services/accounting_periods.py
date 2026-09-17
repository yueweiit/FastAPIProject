"""Internal, automatic month-end inventory snapshots."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    AccountingPeriod,
    InventoryBatch,
    InventoryPeriodSnapshot,
    Product,
    ProductImpairmentRule,
    Sale,
    SaleCostDetail,
    User,
)
from services.inventory_impairment import ImpairmentRule, InventoryLayer, batch_impairments


def previous_month_end(value: date) -> date:
    return value.replace(day=1) - timedelta(days=1)


def _shanghai_now(now: datetime | None = None) -> datetime:
    timezone = ZoneInfo("Asia/Shanghai")
    if now is None:
        return datetime.now(timezone).replace(tzinfo=None)
    if now.tzinfo is None:
        return now
    return now.astimezone(timezone).replace(tzinfo=None)


async def _snapshot_rows(
    db: AsyncSession, period: AccountingPeriod
) -> list[InventoryPeriodSnapshot]:
    cutoff = datetime.combine(period.period_end + timedelta(days=1), time.min)
    batch_rows = (
        await db.execute(
            select(
                InventoryBatch,
                User.store_id,
                Product.product_type,
                Product.safe_stock_quantity,
            )
            .join(Product, Product.id == InventoryBatch.product_id)
            .outerjoin(User, User.id == InventoryBatch.user_id)
            .where(InventoryBatch.arrived_at < cutoff)
            .order_by(InventoryBatch.arrived_at, InventoryBatch.id)
        )
    ).all()
    if not batch_rows:
        return []

    batch_ids = [batch.id for batch, *_ in batch_rows]
    product_ids = {batch.product_id for batch, *_ in batch_rows}
    rule_rows = (await db.execute(
        select(ProductImpairmentRule).where(
            ProductImpairmentRule.product_id.in_(product_ids)
        )
    )).scalars().all()
    rules_by_product: dict[int, list[ImpairmentRule]] = {}
    for rule in rule_rows:
        rules_by_product.setdefault(rule.product_id, []).append(ImpairmentRule(
            product_type=rule.product_type,
            safe_stock_quantity=rule.safe_stock_quantity,
            effective_date=rule.effective_date,
        ))

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

    layers = []
    for batch, store_id, product_type, safe_stock_quantity in batch_rows:
        quantity = max(0, int(batch.quantity) - deducted_by_batch.get(batch.id, 0))
        layers.append(InventoryLayer(
            batch_id=batch.id,
            product_id=batch.product_id,
            store_id=store_id,
            arrived_at=batch.arrived_at.date(),
            quantity=quantity,
            product_type=product_type,
            safe_stock_quantity=safe_stock_quantity,
            rules=tuple(rules_by_product.get(batch.product_id, [])),
        ))

    impairments = batch_impairments(layers, period.period_end)
    snapshots = []
    for batch, store_id, _product_type, _safe_stock_quantity in batch_rows:
        quantity = max(0, int(batch.quantity) - deducted_by_batch.get(batch.id, 0))
        unit_cost = Decimal(batch.unit_cost or 0)
        impairment = impairments.get(batch.id)
        impairment_rate = impairment.rate if impairment else Decimal("0")
        inventory_amount = unit_cost * quantity
        impairment_amount = (
            unit_cost * impairment.impairment_units if impairment else Decimal("0")
        )
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
    """Create the snapshot only once, keeping the period immutable afterwards."""
    if period.snapshot_version:
        return False
    db.add_all(await _snapshot_rows(db, period))
    period.snapshot_version = 1
    await db.flush()
    return True


async def ensure_period_snapshot(
    db: AsyncSession, period_end: date, now: datetime | None = None
) -> AccountingPeriod:
    """Automatically create and close a calendar-month snapshot when absent."""
    snapshot_now = _shanghai_now(now)
    if period_end >= snapshot_now.date():
        raise ValueError("月末库存快照只能在该自然月结束后生成")
    period_start = period_end.replace(day=1)
    period = (
        await db.execute(
            select(AccountingPeriod)
            .where(
                AccountingPeriod.period_start == period_start,
                AccountingPeriod.period_end == period_end,
            )
            .order_by(AccountingPeriod.id.desc())
        )
    ).scalars().first()
    if period is None:
        period = AccountingPeriod(
            period_start=period_start,
            period_end=period_end,
            timezone="Asia/Shanghai",
            status="auto_closed",
            snapshot_version=0,
        )
        db.add(period)
        await db.flush()

    if not period.snapshot_version:
        await create_period_snapshot(db, period)
        period.status = "auto_closed"
        period.closed_at = snapshot_now
        period.closed_by_user_id = None
        await db.commit()
    return period
