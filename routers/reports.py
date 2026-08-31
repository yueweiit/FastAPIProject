from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Sale, InventoryBatch, Product, User
from schemas import MonthlyReportItem
from auth import RequireAnyRole

router = APIRouter(prefix="/reports", tags=["报表"])


@router.get("/monthly", response_model=list[MonthlyReportItem])
async def monthly_report(
    start_date: date = Query(..., description="开始日期"),
    end_date: date = Query(..., description="结束日期"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    """月度报表 - 所有角色可查看，operator只看自己的数据"""
    month_expr = func.DATE_FORMAT(Sale.sold_at, "%Y-%m").label("month")
    stmt = (
        select(
            month_expr,
            Sale.product_id,
            func.sum(Sale.quantity).label("sold_quantity"),
        )
        .where(
            cast(Sale.sold_at, Date) >= start_date,
            cast(Sale.sold_at, Date) <= end_date,
        )
        .group_by(month_expr, Sale.product_id)
        .order_by(month_expr, Sale.product_id)
    )
    if user.role == "operator":
        stmt = stmt.where(Sale.user_id == user.id)

    result = await db.execute(stmt)
    sold_rows = result.all()

    # 库存信息
    inventory_stmt = select(
        InventoryBatch.product_id,
        func.sum(InventoryBatch.remaining_quantity).label("inventory_quantity"),
        func.sum(InventoryBatch.remaining_quantity * InventoryBatch.unit_cost).label("inventory_value"),
    ).group_by(InventoryBatch.product_id)
    inv_result = await db.execute(inventory_stmt)
    inventory_map: dict[int, tuple[int, Decimal]] = {}
    for row in inv_result.all():
        inventory_map[row.product_id] = (
            row.inventory_quantity or 0,
            row.inventory_value or Decimal("0"),
        )

    products_result = await db.execute(select(Product))
    product_map = {p.id: p for p in products_result.scalars().all()}

    reports = []
    for row in sold_rows:
        product = product_map.get(row.product_id)
        if not product:
            continue
        inv_qty, inv_val = inventory_map.get(row.product_id, (0, Decimal("0")))
        reports.append(MonthlyReportItem(
            month=row.month,
            product_id=row.product_id,
            sku=product.sku,
            product_name=product.name,
            sold_quantity=row.sold_quantity or 0,
            inventory_quantity=inv_qty,
            inventory_value=inv_val,
        ))

    return reports
