"""FIFO（先进先出）库存扣减核心逻辑"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import InventoryBatch, Sale, SaleCostDetail


class InsufficientStockError(Exception):
    pass


async def fifo_sell(
    db: AsyncSession,
    product_id: int,
    order_no: str,
    quantity: int,
    selling_price: Decimal,
    platform_fee: Decimal = Decimal("0"),
    sold_at: datetime | None = None,
    user_id: int | None = None,
) -> tuple[Sale, list[dict]]:
    if sold_at is None:
        sold_at = datetime.now()

    # 1. 查询有库存的批次，按到货时间排序
    stmt = (
        select(InventoryBatch)
        .where(
            InventoryBatch.product_id == product_id,
            InventoryBatch.remaining_quantity > 0,
        )
        .order_by(InventoryBatch.arrived_at.asc())
    )
    result = await db.execute(stmt)
    batches = list(result.scalars().all())

    # 2. 检查库存
    total_available = sum(b.remaining_quantity for b in batches)
    if total_available < quantity:
        raise InsufficientStockError(
            f"库存不足: 需要 {quantity} 件, 仅有 {total_available} 件"
        )

    # 3. FIFO扣减
    remaining_to_sell = quantity
    cost_details: list[dict] = []
    total_cost = Decimal("0")

    for batch in batches:
        if remaining_to_sell <= 0:
            break

        take = min(batch.remaining_quantity, remaining_to_sell)
        cost_details.append({
            "batch_id": batch.id,
            "batch_no": batch.batch_no,
            "quantity": take,
            "unit_cost": batch.unit_cost,
        })
        total_cost += batch.unit_cost * take
        batch.remaining_quantity -= take
        remaining_to_sell -= take

    # 4. 计算利润
    profit = selling_price * quantity - total_cost - platform_fee

    # 5. 创建销售记录
    sale = Sale(
        product_id=product_id,
        user_id=user_id,
        order_no=order_no,
        quantity=quantity,
        selling_price=selling_price,
        total_cost=total_cost,
        platform_fee=platform_fee,
        profit=profit,
        sold_at=sold_at,
    )
    db.add(sale)
    await db.flush()

    # 6. 创建成本明细
    for detail in cost_details:
        db.add(SaleCostDetail(
            sale_id=sale.id,
            batch_id=detail["batch_id"],
            quantity=detail["quantity"],
            unit_cost=detail["unit_cost"],
        ))

    await db.commit()

    # commit后属性过期，refresh重新加载标量字段（不加载关系）
    await db.refresh(sale)

    return sale, cost_details
