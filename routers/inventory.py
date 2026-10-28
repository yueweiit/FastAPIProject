"""库存工作台只读接口；共享当前库存与按店铺受限的销售明细分开。"""

from datetime import date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import RequireAnyRole
from database import get_db
from models import InventoryBatch, Product, Sale, SaleCostDetail, User
from routers.products import _product_summary_from_row, _product_summary_stmt, list_products
from routers.sales import _sale_store_scope
from services.inventory_activity import activity_rows, build_activity, business_today, load_activity_sources, month_bounds
from services.inventory_export import EXPORT_BATCH_SIZE, MAX_EXPORT_PRODUCTS, MIME, build_inventory_workbook
from services.inventory_impairment import effective_product_type
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/inventory", tags=["库存查看"])


def product_conditions(keyword, product_id=None):
    conditions = []
    if keyword and keyword.strip():
        pattern = f"%{keyword.strip()}%"
        conditions.append(or_(Product.sku.ilike(pattern), Product.name.ilike(pattern)))
    if product_id is not None:
        conditions.append(Product.id == product_id)
    return conditions


async def product_page(db, keyword, page, page_size, product_id=None):
    conditions = product_conditions(keyword, product_id)
    total = await db.scalar(select(func.count()).select_from(Product).where(*conditions))
    page = min(page, max(1, (total + page_size - 1) // page_size))
    rows = (await db.execute(select(Product).where(*conditions).order_by(Product.id.desc())
                            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return rows, total, page


def overview_row(product):
    row = product.model_dump()
    row["product_type"] = effective_product_type(product.product_type, product.safe_stock_quantity)
    row["stock_status"] = ("pending" if product.stock_quantity < 0 else "zero" if product.stock_quantity == 0 else "low"
        if row["product_type"] == "safe_stock" and product.stock_quantity < product.safe_stock_quantity else "normal")
    return row


@router.get("/overview")
async def overview(
    keyword: str | None = Query(None, max_length=255), product_id: int | None = Query(None, gt=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    if product_id is None:
        data = await list_products(keyword=keyword, page=page, page_size=page_size, db=db, user=user)
        products, total, page = data.items, data.total, data.page
    else:
        result = await db.execute(_product_summary_stmt().where(*product_conditions(keyword, product_id)))
        products = [_product_summary_from_row(row) for row in result.all()]
        total, page = len(products), 1
    items = [overview_row(product) for product in products]
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "scope": "shared", "summary": {"quantity": sum(p.stock_quantity for p in products),
            "inventory_amount": sum((p.stock_value for p in products), 0),
            "low_count": sum(row["stock_status"] == "low" for row in items),
            "zero_count": sum(row["stock_status"] == "zero" for row in items)}}


async def activity_data(db, user, month, products, *, bounds=None):
    start, end = bounds if bounds is not None else month_bounds(month)
    scope = _sale_store_scope(user)  # 未绑定账号不能读取出库数据，沿用销售端的兼容归属。
    sources = await load_activity_sources(db, [p.id for p in products], start, end, scope)
    activity, events = build_activity(*sources, start, end, full_scope=user.role == "admin",
                                      include_voided=user.role != "viewer")
    return start, end, activity, events


@router.get("/daily")
async def daily(
    month: date = Query(...), keyword: str | None = Query(None, max_length=255),
    product_id: int | None = Query(None, gt=0), page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    month_bounds(month)
    products, total, page = await product_page(db, keyword, page, page_size, product_id)
    start, end, activity, _events = await activity_data(db, user, month, products)
    items = activity_rows(products, activity, start, end, full_scope=user.role == "admin")
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "month": month, "start": start, "end": end,
            "scope": "global" if user.role == "admin" else "store_outbound",
            "warnings": ["inventory_current_documents"] + ([] if user.role == "admin" else ["inventory_scope_restricted"])}


@router.get("/movements")
async def movements(
    product_id: int = Query(..., gt=0), day: date = Query(...),
    kind: Literal["inbound", "outbound", "adjustment"] = Query("outbound"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), user: User = Depends(RequireAnyRole),
):
    month_bounds(day.replace(day=1))
    if day > business_today():
        raise HTTPException(422, detail={"code": "inventory_future_date", "message": "不能查看未来日期流水。"})
    scope = _sale_store_scope(user)
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(404, detail={"code": "inventory_product_missing", "message": "商品不存在。"})
    if kind == "outbound":
        conditions = [InventoryBatch.product_id == product_id,
                      Sale.sold_at >= datetime.combine(day, time.min),
                      Sale.sold_at < datetime.combine(day + timedelta(days=1), time.min)]
        if scope is not None:
            conditions.append(scope)
        query = select(SaleCostDetail.id.label("source_id"), SaleCostDetail.batch_id,
                       InventoryBatch.batch_no, Sale.sold_at.label("occurred_at"),
                       SaleCostDetail.quantity, Sale.order_no.label("reference"))\
            .join(Sale, Sale.id == SaleCostDetail.sale_id)\
            .join(InventoryBatch, InventoryBatch.id == SaleCostDetail.batch_id).where(*conditions)
        total = await db.scalar(select(func.count()).select_from(query.subquery()))
        page = min(page, max(1, (total + page_size - 1) // page_size))
        rows = (await db.execute(query.order_by(Sale.sold_at.desc(), SaleCostDetail.id.desc())
                                .offset((page - 1) * page_size).limit(page_size))).mappings().all()
        items = [{**dict(row), "kind": "outbound", "quantity": -int(row["quantity"])} for row in rows]
    else:
        if kind == "adjustment" and user.role != "admin":
            raise HTTPException(403, detail={"code": "inventory_adjustment_restricted",
                                            "message": "完整库存调整日结仅管理员可查看。"})
        _start, _end, _activity, events = await activity_data(db, user, day.replace(day=1), [product])
        items = [event for event in events if event["occurred_at"].date() == day
                 and ((event["kind"] == "inbound") if kind == "inbound" else (event["kind"] != "inbound"))]
        items.sort(key=lambda row: (row["occurred_at"], row["source_id"]), reverse=True)
        total = len(items)
        page = min(page, max(1, (total + page_size - 1) // page_size))
        items = items[(page - 1) * page_size:page * page_size]
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "product_id": product_id, "sku": product.sku, "name": product.name,
            "day": day, "kind": kind}


@router.get("/export")
async def export_inventory(
    view: Literal["overview", "daily", "matrix"] = Query("overview"),
    month: date | None = Query(None), keyword: str | None = Query(None, max_length=255),
    product_id: int | None = Query(None, gt=0), db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    start = end = None
    scope = "shared"
    if view != "overview":
        _sale_store_scope(user)
        if month is None:
            raise HTTPException(422, detail={"code": "inventory_export_month_required", "message": "导出每日进出须选择月份。"})
        start, end = month_bounds(month)
        scope = "global" if user.role == "admin" else "store_outbound"
    products = (await db.execute(select(Product).where(*product_conditions(keyword, product_id))
        .order_by(Product.id.desc()).limit(MAX_EXPORT_PRODUCTS + 1))).scalars().all()
    if not products:
        raise HTTPException(404, detail={"code": "inventory_export_empty", "message": "没有符合条件的商品可导出。"})
    if len(products) > MAX_EXPORT_PRODUCTS:
        raise HTTPException(422, detail={"code": "inventory_export_too_large",
            "message": f"单次最多导出{MAX_EXPORT_PRODUCTS}个商品，请缩小查询范围。"})
    selected_sku = products[0].sku if product_id is not None else None
    rows = []
    for offset in range(0, len(products), EXPORT_BATCH_SIZE):
        batch = products[offset:offset + EXPORT_BATCH_SIZE]
        if view == "overview":
            result = await db.execute(_product_summary_stmt().where(Product.id.in_([p.id for p in batch])))
            rows.extend(overview_row(_product_summary_from_row(row)) for row in result.all())
        else:
            try:
                start, end, activity, _events = await activity_data(db, user, month, batch, bounds=(start, end))
            except HTTPException as error:
                if isinstance(error.detail, dict) and error.detail.get("code") == "inventory_sources_too_large":
                    raise HTTPException(422, detail={"code": "inventory_export_sources_too_large",
                        "message": "导出的历史记录较多，请缩小商品范围后重试。"}) from error
                raise
            rows.extend(activity_rows(batch, activity, start, end, full_scope=scope == "global"))
    # 全部响应值已转为普通数据；释放只读事务后在线程中生成文件，不长期占连接池。
    await db.rollback()
    output = await run_in_threadpool(build_inventory_workbook, view, rows, scope=scope,
                                    keyword=keyword.strip() if keyword else None, selected_sku=selected_sku,
                                    start=start, end=end)
    suffix = business_today().strftime("%Y%m%d") if view == "overview" else month.strftime("%Y%m")
    return StreamingResponse(output, media_type=MIME,
        headers={"Content-Disposition": f'attachment; filename="inventory_{view}_{suffix}.xlsx"'})
