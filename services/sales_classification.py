"""库存销售与经确认的期初扣减/非实物结算分类；不推断历史，不改数量成本。"""
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from fastapi import HTTPException

from models import NonInventorySkuRule, Sale

INVENTORY_SALE = "inventory_sale"
OPENING_DEDUCTION = "opening_deduction"
NON_INVENTORY = "non_inventory"


def is_opening_deduction(sale):
    metadata = getattr(sale, "business_metadata", None)
    return isinstance(metadata, dict) and metadata.get("kind") == OPENING_DEDUCTION


def inventory_sales_condition():
    """历史NULL仍为真实销售；只排除显式确认的期初扣减。"""
    return func.coalesce(Sale.business_metadata["kind"].as_string(), INVENTORY_SALE) != OPENING_DEDUCTION


async def non_inventory_skus(db, store_ids):
    ids = {value for value in store_ids if value is not None}
    if not ids:
        return set()
    try:
        return set((await db.execute(select(NonInventorySkuRule.store_id, NonInventorySkuRule.platform_sku_id)
            .where(NonInventorySkuRule.store_id.in_(ids), NonInventorySkuRule.is_active.is_(True)))).all())
    except DBAPIError as exc:
        if getattr(exc.orig, "args", [None])[0] != 1146 and "no such table: non_inventory_sku_rules" not in str(exc.orig):
            raise
        raise HTTPException(503, detail={"code":"sales_classification_schema_missing",
            "message":"销售分类结构尚未准备，请先完成迁移，未处理库存或结算。"}) from exc
