import os
import uuid
from datetime import datetime
from io import BytesIO
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Product, InventoryBatch, User
from schemas import ProductOptionResponse, ProductPageResponse, ProductResponse
from auth import RequireAdmin, RequireOperator, RequireAnyRole, get_current_user

router = APIRouter(prefix="/products", tags=["商品管理"])

UPLOAD_DIR = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
BASE_DIR = Path(__file__).parent.parent


@router.post("", response_model=ProductResponse)
async def create_product(
    sku: str = Form(...),
    name: str = Form(...),
    image: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireOperator),
):
    """添加商品 - 管理员和运营"""
    existing = await db.execute(select(Product).where(Product.sku == sku))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"SKU已存在: {sku}")

    image_path = None
    if image and image.filename:
        ext = os.path.splitext(image.filename)[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = UPLOAD_DIR / filename
        content = await image.read()
        filepath.write_bytes(content)
        image_path = f"/uploads/{filename}"

    product = Product(sku=sku, name=name, image=image_path)
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return _enrich_product(product, 0, Decimal("0"), None)


@router.get("/options", response_model=list[ProductOptionResponse])
async def list_product_options(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    """Return lightweight product data for selectors and batch lookups."""
    result = await db.execute(select(Product).order_by(Product.id.desc()))
    return result.scalars().all()


def _product_summary_stmt():
    return (
        select(
            Product,
            func.coalesce(func.sum(InventoryBatch.remaining_quantity), 0).label("stock_quantity"),
            func.coalesce(
                func.sum(InventoryBatch.remaining_quantity * InventoryBatch.unit_cost), 0
            ).label("stock_value"),
            func.max(InventoryBatch.arrived_at).label("last_batch_at"),
        )
        .outerjoin(InventoryBatch, InventoryBatch.product_id == Product.id)
        .group_by(Product.id, Product.sku, Product.name, Product.image, Product.created_at)
        .order_by(Product.id.desc())
    )


@router.get("", response_model=list[ProductResponse] | ProductPageResponse)
async def list_products(
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    """列出商品；分页请求在单条聚合查询中返回库存摘要。"""
    stmt = _product_summary_stmt()
    if page is None:
        result = await db.execute(stmt)
        return [_product_summary_from_row(row) for row in result.all()]

    total = (await db.execute(select(func.count()).select_from(Product))).scalar_one()
    page = min(page, max(1, (total + page_size - 1) // page_size))
    result = await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))
    return ProductPageResponse(
        items=[_product_summary_from_row(row) for row in result.all()],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/export")
async def export_products(
    product_ids: list[int] | None = Query(default=None, description="要导出的商品 ID，可重复传入"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    """导出一个或多个商品为 Excel，并嵌入商品图片。"""
    stmt = select(Product).order_by(Product.id.desc())
    if product_ids:
        stmt = stmt.where(Product.id.in_(product_ids))
    result = await db.execute(stmt)
    products = list(result.scalars().all())
    if not products:
        raise HTTPException(status_code=404, detail="没有可导出的商品")

    product_ids = [product.id for product in products]
    summaries = await db.execute(
        select(
            InventoryBatch.product_id,
            func.coalesce(func.sum(InventoryBatch.remaining_quantity), 0).label("stock_quantity"),
            func.coalesce(
                func.sum(InventoryBatch.remaining_quantity * InventoryBatch.unit_cost), 0
            ).label("stock_value"),
            func.max(InventoryBatch.arrived_at).label("last_batch_at"),
        )
        .where(InventoryBatch.product_id.in_(product_ids))
        .group_by(InventoryBatch.product_id)
    )
    summary_map = {
        row.product_id: (row.stock_quantity, row.stock_value, row.last_batch_at)
        for row in summaries.all()
    }
    rows = [
        (product, *summary_map.get(product.id, (0, Decimal("0"), None)))
        for product in products
    ]

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "商品数据"
    sheet.append(["图片", "商品ID", "SKU", "商品名称", "库存数量", "库存价值", "最近入库", "创建时间"])
    header_fill = PatternFill("solid", fgColor="5B9BD5")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"

    for row_index, (product, stock_qty, stock_value, last_batch) in enumerate(rows, start=2):
        sheet.append([
            "", product.id, product.sku, product.name, stock_qty, stock_value,
            last_batch, product.created_at,
        ])
        image_path = None
        if product.image:
            candidate = (BASE_DIR / product.image.lstrip("/")).resolve()
            uploads_root = (BASE_DIR / "uploads").resolve()
            if candidate.is_file() and uploads_root in candidate.parents:
                image_path = candidate
        if image_path:
            try:
                image = ExcelImage(str(image_path))
                max_size = 72
                scale = min(max_size / image.width, max_size / image.height, 1)
                image.width = max(1, int(image.width * scale))
                image.height = max(1, int(image.height * scale))
                sheet.add_image(image, f"A{row_index}")
                sheet.row_dimensions[row_index].height = 60
            except (OSError, ValueError, TypeError):
                pass

    for row in sheet.iter_rows(min_row=2, max_row=sheet.max_row):
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    for cell in sheet["F"][1:]:
        cell.number_format = '¥#,##0.00'
    for column in ("G", "H"):
        for cell in sheet[column][1:]:
            cell.number_format = "yyyy-mm-dd hh:mm"
    for column, width in {
        "A": 12, "B": 10, "C": 18, "D": 24, "E": 12, "F": 16, "G": 20, "H": 20,
    }.items():
        sheet.column_dimensions[column].width = width
    sheet.auto_filter.ref = sheet.dimensions

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f"product_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _enrich_product(product, stock_qty, stock_val, last_batch):
    return ProductResponse(
        id=product.id,
        sku=product.sku,
        name=product.name,
        image=product.image,
        created_at=product.created_at,
        stock_quantity=stock_qty,
        stock_value=stock_val,
        last_batch_at=str(last_batch)[:16] if last_batch else None,
    )


def _product_summary_from_row(row):
    product, stock_qty, stock_val, last_batch = row
    return _enrich_product(product, stock_qty, stock_val, last_batch)


@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    sku: str = Form(...),
    name: str = Form(...),
    image: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireOperator),
):
    """编辑商品 - 管理员和运营"""
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    # 检查SKU重复（排除自己）
    existing = await db.execute(select(Product).where(Product.sku == sku, Product.id != product_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"SKU已存在: {sku}")

    product.sku = sku
    product.name = name

    if image and image.filename:
        # 删除旧图片
        if product.image:
            old_path = Path(__file__).parent.parent / product.image.lstrip("/")
            if old_path.exists():
                old_path.unlink()
        ext = os.path.splitext(image.filename)[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = UPLOAD_DIR / filename
        content = await image.read()
        filepath.write_bytes(content)
        product.image = f"/uploads/{filename}"

    await db.commit()
    await db.refresh(product)
    return _enrich_product(product, 0, Decimal("0"), None)


@router.delete("/{product_id}")
async def delete_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireOperator),
):
    """删除商品 - 管理员和运营"""
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    if product.image:
        filepath = Path(__file__).parent.parent / product.image.lstrip("/")
        if filepath.exists():
            filepath.unlink()

    await db.delete(product)
    await db.commit()
    return {"ok": True}
