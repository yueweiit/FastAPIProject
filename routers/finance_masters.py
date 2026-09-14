from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import RequireAdmin, RequireAnyRole
from database import get_db
from models import (
    AccountingPeriod,
    Product,
    ProductLine,
    InventoryPeriodSnapshot,
    Sale,
    SettlementEntry,
    Store,
    StoreProduct,
    User,
)
from schemas import (
    AccountingPeriodRequest,
    AccountingPeriodResponse,
    ProductLineRequest,
    ProductLineResponse,
    StoreProductRequest,
    StoreProductResponse,
    StoreRequest,
    StoreResponse,
)
from services.accounting_periods import (
    auto_confirm_expired_periods,
    create_period_snapshot,
    period_confirmation_deadline,
    snapshot_count,
)

router = APIRouter(prefix="/finance-masters", tags=["财务主数据"])


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _validate_date_range(start: date, end: date | None) -> None:
    if end is not None and end < start:
        raise HTTPException(status_code=400, detail="失效日期不能早于生效日期")


async def _commit(db: AsyncSession, duplicate_message: str) -> None:
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=duplicate_message)


def _store_product_response(row) -> StoreProductResponse:
    mapping, store, product, product_line = row
    return StoreProductResponse(
        id=mapping.id,
        store_id=mapping.store_id,
        product_id=mapping.product_id,
        product_line_id=mapping.product_line_id,
        platform_product_id=mapping.platform_product_id,
        platform_sku_id=mapping.platform_sku_id,
        store_sku=mapping.store_sku,
        effective_from=mapping.effective_from,
        effective_to=mapping.effective_to,
        is_active=mapping.is_active,
        store_name=store.name,
        product_sku=product.sku,
        product_name=product.name,
        product_line_code=product_line.code,
        product_line_name=product_line.name,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


async def _accounting_period_response(
    db: AsyncSession, period: AccountingPeriod
) -> AccountingPeriodResponse:
    return AccountingPeriodResponse(
        id=period.id,
        period_start=period.period_start,
        period_end=period.period_end,
        timezone=period.timezone,
        status=period.status,
        snapshot_version=period.snapshot_version,
        closed_at=period.closed_at,
        closed_by_user_id=period.closed_by_user_id,
        snapshot_count=await snapshot_count(db, period.id),
        confirmation_deadline=(
            period_confirmation_deadline(period)
            if period.status == "pending_confirmation"
            else None
        ),
        created_at=period.created_at,
        updated_at=period.updated_at,
    )


async def _validate_store_product_references(
    db: AsyncSession, data: StoreProductRequest
) -> None:
    store = await db.get(Store, data.store_id)
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    if not await db.get(Product, data.product_id):
        raise HTTPException(status_code=404, detail="商品不存在")
    product_line = await db.get(ProductLine, data.product_line_id)
    if not product_line:
        raise HTTPException(status_code=404, detail="产品线不存在")
    if data.is_active and (not store.is_active or not product_line.is_active):
        raise HTTPException(status_code=400, detail="启用映射前，店铺和产品线必须处于启用状态")


async def _validate_store_sku_availability(
    db: AsyncSession, data: StoreProductRequest, mapping_id: int | None = None
) -> None:
    store_sku = _normalized(data.store_sku)
    if not store_sku:
        raise HTTPException(status_code=400, detail="店铺 SKU 不能为空")

    exact_stmt = select(StoreProduct.id).where(
        StoreProduct.store_id == data.store_id,
        StoreProduct.store_sku == store_sku,
        StoreProduct.effective_from == data.effective_from,
    )
    active_stmt = select(StoreProduct.id).where(
        StoreProduct.store_id == data.store_id,
        StoreProduct.store_sku == store_sku,
        StoreProduct.is_active.is_(True),
    )
    if mapping_id is not None:
        exact_stmt = exact_stmt.where(StoreProduct.id != mapping_id)
        active_stmt = active_stmt.where(StoreProduct.id != mapping_id)
    if (await db.execute(exact_stmt)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="该店铺 SKU 在相同生效日期已有映射")
    if data.is_active and (await db.execute(active_stmt)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="该店铺 SKU 已有启用中的映射，请先停用旧映射")


# ---------- 产品线 ----------
@router.get("/product-lines", response_model=list[ProductLineResponse])
async def list_product_lines(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    stmt = select(ProductLine).order_by(ProductLine.is_active.desc(), ProductLine.code)
    if active_only:
        stmt = stmt.where(ProductLine.is_active.is_(True))
    return (await db.execute(stmt)).scalars().all()


@router.post("/product-lines", response_model=ProductLineResponse, status_code=status.HTTP_201_CREATED)
async def create_product_line(
    data: ProductLineRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    product_line = ProductLine(
        code=data.code.strip(), name=data.name.strip(), is_active=data.is_active
    )
    db.add(product_line)
    await _commit(db, "产品线编码已存在")
    await db.refresh(product_line)
    return product_line


@router.put("/product-lines/{product_line_id}", response_model=ProductLineResponse)
async def update_product_line(
    product_line_id: int,
    data: ProductLineRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    product_line = await db.get(ProductLine, product_line_id)
    if not product_line:
        raise HTTPException(status_code=404, detail="产品线不存在")
    product_line.code = data.code.strip()
    product_line.name = data.name.strip()
    product_line.is_active = data.is_active
    await _commit(db, "产品线编码已存在")
    await db.refresh(product_line)
    return product_line


@router.delete("/product-lines/{product_line_id}")
async def delete_product_line(
    product_line_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    product_line = await db.get(ProductLine, product_line_id)
    if not product_line:
        raise HTTPException(status_code=404, detail="产品线不存在")
    count = await db.scalar(
        select(func.count())
        .select_from(StoreProduct)
        .where(StoreProduct.product_line_id == product_line_id)
    )
    if count:
        raise HTTPException(status_code=400, detail="产品线已有店铺 SKU 映射，不能删除")
    await db.delete(product_line)
    await db.commit()
    return {"ok": True}


# ---------- 店铺 ----------
@router.get("/stores", response_model=list[StoreResponse])
async def list_stores(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    stmt = select(Store).order_by(Store.is_active.desc(), Store.name, Store.id)
    if active_only:
        stmt = stmt.where(Store.is_active.is_(True))
    return (await db.execute(stmt)).scalars().all()


@router.post("/stores", response_model=StoreResponse, status_code=status.HTTP_201_CREATED)
async def create_store(
    data: StoreRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    store = Store(
        platform=data.platform.strip(),
        platform_store_id=data.platform_store_id.strip(),
        name=data.name.strip(),
        country_or_region=data.country_or_region.strip(),
        settlement_currency=data.settlement_currency.upper(),
        timezone=data.timezone.strip(),
        legal_entity=_normalized(data.legal_entity),
        is_active=data.is_active,
    )
    db.add(store)
    await _commit(db, "该平台店铺 ID 已存在")
    await db.refresh(store)
    return store


@router.put("/stores/{store_id}", response_model=StoreResponse)
async def update_store(
    store_id: int,
    data: StoreRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    store.platform = data.platform.strip()
    store.platform_store_id = data.platform_store_id.strip()
    store.name = data.name.strip()
    store.country_or_region = data.country_or_region.strip()
    store.settlement_currency = data.settlement_currency.upper()
    store.timezone = data.timezone.strip()
    store.legal_entity = _normalized(data.legal_entity)
    store.is_active = data.is_active
    await _commit(db, "该平台店铺 ID 已存在")
    await db.refresh(store)
    return store


@router.delete("/stores/{store_id}")
async def delete_store(
    store_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    count = await db.scalar(
        select(func.count()).select_from(StoreProduct).where(StoreProduct.store_id == store_id)
    )
    if count:
        raise HTTPException(status_code=400, detail="店铺已有店铺 SKU 映射，不能删除")
    user_count = await db.scalar(
        select(func.count()).select_from(User).where(User.store_id == store_id)
    )
    if user_count:
        raise HTTPException(status_code=400, detail="店铺已绑定用户，不能删除")
    record_count = await db.scalar(
        select(func.count()).select_from(Sale).where(Sale.store_id == store_id)
    )
    settlement_count = await db.scalar(
        select(func.count()).select_from(SettlementEntry).where(SettlementEntry.store_id == store_id)
    )
    if record_count or settlement_count:
        raise HTTPException(status_code=400, detail="店铺已有销售或结算记录，不能删除")
    await db.delete(store)
    await db.commit()
    return {"ok": True}


# ---------- 店铺 SKU 映射 ----------
@router.get("/store-products", response_model=list[StoreProductResponse])
async def list_store_products(
    store_id: int | None = Query(default=None, ge=1),
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    stmt = (
        select(StoreProduct, Store, Product, ProductLine)
        .join(Store, Store.id == StoreProduct.store_id)
        .join(Product, Product.id == StoreProduct.product_id)
        .join(ProductLine, ProductLine.id == StoreProduct.product_line_id)
        .order_by(Store.name, StoreProduct.store_sku, StoreProduct.effective_from.desc())
    )
    if store_id is not None:
        stmt = stmt.where(StoreProduct.store_id == store_id)
    if active_only:
        stmt = stmt.where(StoreProduct.is_active.is_(True))
    return [_store_product_response(row) for row in (await db.execute(stmt)).all()]


@router.post("/store-products", response_model=StoreProductResponse, status_code=status.HTTP_201_CREATED)
async def create_store_product(
    data: StoreProductRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    _validate_date_range(data.effective_from, data.effective_to)
    await _validate_store_product_references(db, data)
    await _validate_store_sku_availability(db, data)
    mapping = StoreProduct(
        store_id=data.store_id,
        product_id=data.product_id,
        product_line_id=data.product_line_id,
        platform_product_id=_normalized(data.platform_product_id),
        platform_sku_id=_normalized(data.platform_sku_id),
        store_sku=_normalized(data.store_sku),
        effective_from=data.effective_from,
        effective_to=data.effective_to,
        is_active=data.is_active,
    )
    db.add(mapping)
    await _commit(db, "店铺 SKU 映射已存在")
    result = await db.execute(
        select(StoreProduct, Store, Product, ProductLine)
        .join(Store).join(Product).join(ProductLine)
        .where(StoreProduct.id == mapping.id)
    )
    return _store_product_response(result.one())


@router.put("/store-products/{mapping_id}", response_model=StoreProductResponse)
async def update_store_product(
    mapping_id: int,
    data: StoreProductRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    mapping = await db.get(StoreProduct, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="店铺 SKU 映射不存在")
    _validate_date_range(data.effective_from, data.effective_to)
    await _validate_store_product_references(db, data)
    await _validate_store_sku_availability(db, data, mapping_id)
    mapping.store_id = data.store_id
    mapping.product_id = data.product_id
    mapping.product_line_id = data.product_line_id
    mapping.platform_product_id = _normalized(data.platform_product_id)
    mapping.platform_sku_id = _normalized(data.platform_sku_id)
    mapping.store_sku = _normalized(data.store_sku)
    mapping.effective_from = data.effective_from
    mapping.effective_to = data.effective_to
    mapping.is_active = data.is_active
    await _commit(db, "店铺 SKU 映射已存在")
    result = await db.execute(
        select(StoreProduct, Store, Product, ProductLine)
        .join(Store).join(Product).join(ProductLine)
        .where(StoreProduct.id == mapping.id)
    )
    return _store_product_response(result.one())


@router.delete("/store-products/{mapping_id}")
async def delete_store_product(
    mapping_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    mapping = await db.get(StoreProduct, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="店铺 SKU 映射不存在")
    await db.delete(mapping)
    await db.commit()
    return {"ok": True}


# ---------- 会计期间 ----------
@router.get("/accounting-periods", response_model=list[AccountingPeriodResponse])
async def list_accounting_periods(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    await auto_confirm_expired_periods(db)
    result = await db.execute(
        select(AccountingPeriod).order_by(
            AccountingPeriod.period_start.desc(), AccountingPeriod.id.desc()
        )
    )
    return [
        await _accounting_period_response(db, period)
        for period in result.scalars().all()
    ]


@router.post(
    "/accounting-periods",
    response_model=AccountingPeriodResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_accounting_period(
    data: AccountingPeriodRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    _validate_date_range(data.period_start, data.period_end)
    period = AccountingPeriod(
        period_start=data.period_start,
        period_end=data.period_end,
        timezone=data.timezone.strip(),
        status="open",
        snapshot_version=0,
    )
    db.add(period)
    await _commit(db, "该会计期间已存在")
    await db.refresh(period)
    return await _accounting_period_response(db, period)


@router.put("/accounting-periods/{period_id}", response_model=AccountingPeriodResponse)
async def update_accounting_period(
    period_id: int,
    data: AccountingPeriodRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    period = await db.get(AccountingPeriod, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="会计期间不存在")
    if period.status != "open":
        raise HTTPException(status_code=400, detail="已关闭的会计期间不能修改")
    _validate_date_range(data.period_start, data.period_end)
    period.period_start = data.period_start
    period.period_end = data.period_end
    period.timezone = data.timezone.strip()
    await _commit(db, "该会计期间已存在")
    await db.refresh(period)
    return await _accounting_period_response(db, period)


async def _confirm_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    period = await db.get(AccountingPeriod, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="会计期间不存在")
    if period.status not in ("open", "pending_confirmation"):
        raise HTTPException(status_code=400, detail="会计期间已经关闭")
    await create_period_snapshot(db, period)
    period.status = "closed"
    period.snapshot_version = max(period.snapshot_version, 1)
    period.closed_at = datetime.now()
    period.closed_by_user_id = user.id
    await db.commit()
    await db.refresh(period)
    return await _accounting_period_response(db, period)


@router.post("/accounting-periods/{period_id}/close", response_model=AccountingPeriodResponse)
async def close_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    return await _confirm_accounting_period(period_id, db, user)


@router.post("/accounting-periods/{period_id}/confirm", response_model=AccountingPeriodResponse)
async def confirm_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    return await _confirm_accounting_period(period_id, db, user)


@router.post("/accounting-periods/{period_id}/prepare", response_model=AccountingPeriodResponse)
async def prepare_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    period = await db.get(AccountingPeriod, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="会计期间不存在")
    if period.status != "open":
        raise HTTPException(status_code=400, detail="该会计期间已生成快照或已经关闭")
    await create_period_snapshot(db, period)
    period.status = "pending_confirmation"
    await db.commit()
    await db.refresh(period)
    return await _accounting_period_response(db, period)


@router.post("/accounting-periods/{period_id}/reject", response_model=AccountingPeriodResponse)
async def reject_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    period = await db.get(AccountingPeriod, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="会计期间不存在")
    if period.status != "pending_confirmation":
        raise HTTPException(status_code=400, detail="只有待确认期间可以驳回")
    await db.execute(
        delete(InventoryPeriodSnapshot).where(
            InventoryPeriodSnapshot.accounting_period_id == period.id
        )
    )
    period.status = "open"
    period.snapshot_version = 0
    period.closed_at = None
    period.closed_by_user_id = None
    await db.commit()
    await db.refresh(period)
    return await _accounting_period_response(db, period)


@router.delete("/accounting-periods/{period_id}")
async def delete_accounting_period(
    period_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAdmin),
):
    period = await db.get(AccountingPeriod, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="会计期间不存在")
    if period.status != "open":
        raise HTTPException(status_code=400, detail="已关闭的会计期间不能删除")
    await db.delete(period)
    await db.commit()
    return {"ok": True}
