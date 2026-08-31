from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import Sale, SaleCostDetail, InventoryBatch, User
from schemas import SaleCreate, SaleResponse, CostDetailResponse
from services.fifo import fifo_sell, InsufficientStockError
from auth import get_current_user, RequireOperator, RequireAnyRole

router = APIRouter(prefix="/sales", tags=["销售管理"])


def _build_sale_response(sale: Sale, cost_details: list[dict]) -> SaleResponse:
    return SaleResponse(
        id=sale.id,
        product_id=sale.product_id,
        user_id=sale.user_id,
        order_no=sale.order_no,
        quantity=sale.quantity,
        selling_price=sale.selling_price,
        total_cost=sale.total_cost,
        platform_fee=sale.platform_fee,
        profit=sale.profit,
        sold_at=sale.sold_at,
        created_at=sale.created_at,
        cost_details=[
            CostDetailResponse(
                batch_id=d["batch_id"],
                batch_no=d["batch_no"],
                quantity=d["quantity"],
                unit_cost=d["unit_cost"],
            )
            for d in cost_details
        ],
    )


@router.post("", response_model=SaleResponse)
async def create_sale(
    data: SaleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireOperator),
):
    """记录销售 - admin和operator可操作"""
    try:
        sale, cost_details = await fifo_sell(
            db=db,
            product_id=data.product_id,
            order_no=data.order_no,
            quantity=data.quantity,
            selling_price=data.selling_price,
            platform_fee=data.platform_fee,
            sold_at=data.sold_at,
            user_id=user.id,
        )
    except InsufficientStockError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _build_sale_response(sale, cost_details)


@router.get("", response_model=list[SaleResponse])
async def list_sales(
    product_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    """列出销售 - 所有角色可查看，operator只能看自己的"""
    stmt = select(Sale).options(selectinload(Sale.cost_details))
    if product_id:
        stmt = stmt.where(Sale.product_id == product_id)
    if user.role == "operator":
        stmt = stmt.where(Sale.user_id == user.id)
    stmt = stmt.order_by(Sale.sold_at.desc())
    result = await db.execute(stmt)
    sales = result.scalars().all()

    response = []
    for sale in sales:
        details = []
        for cd in sale.cost_details:
            batch = await db.get(InventoryBatch, cd.batch_id)
            details.append({
                "batch_id": cd.batch_id,
                "batch_no": batch.batch_no if batch else "未知",
                "quantity": cd.quantity,
                "unit_cost": cd.unit_cost,
            })
        response.append(_build_sale_response(sale, details))
    return response


@router.get("/{sale_id}", response_model=SaleResponse)
async def get_sale(
    sale_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    stmt = select(Sale).where(Sale.id == sale_id).options(selectinload(Sale.cost_details))
    result = await db.execute(stmt)
    sale = result.scalar_one_or_none()
    if not sale:
        raise HTTPException(status_code=404, detail="销售记录不存在")
    if user.role == "operator" and sale.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权查看此记录")

    details = []
    for cd in sale.cost_details:
        batch = await db.get(InventoryBatch, cd.batch_id)
        details.append({
            "batch_id": cd.batch_id,
            "batch_no": batch.batch_no if batch else "未知",
            "quantity": cd.quantity,
            "unit_cost": cd.unit_cost,
        })
    return _build_sale_response(sale, details)
