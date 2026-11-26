"""经业务确认的销售分类；保留原始库存及 FIFO 成本。"""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.exc import DBAPIError
from fastapi import HTTPException

from models import (NonInventorySkuRule, Sale, SalesImportBatch, SettlementEntry,
                    SettlementEntryAllocation, Store, User)

INVENTORY_SALE = "inventory_sale"
OPENING_DEDUCTION = "opening_deduction"
NON_INVENTORY = "non_inventory"
INCOME_ONLY_GIFT = "income_only_gift"
FINANCIAL_CLASSIFICATION_KEY = "_financial_classification"
CLASSIFICATION_TEXT = {"zh-CN": {
    "income_only_conflict": "赠品来源与销售店铺不一致或关联多个来源，免成本资格及相关成本、利润待核对",
}}

# 这是受版本控制的业务确认清单，不能由导入文件自带的元字段扩大豁免范围。
_GIFT_POLICY = json.loads(Path(__file__).with_name("income_only_orders.json").read_text(encoding="utf-8"))
_GIFT_ORDERS = frozenset((order, sku, date.fromisoformat(day)) for order, sku, day in _GIFT_POLICY["orders"])


def is_income_only_order(store, order_id, sku, created_at):
    day = created_at.date() if isinstance(created_at, datetime) else created_at
    return (store is not None and store.platform == _GIFT_POLICY["platform"]
            and store.platform_store_id == _GIFT_POLICY["platform_store_id"]
            and (str(order_id or "").strip(), str(sku or "").strip(), day) in _GIFT_ORDERS)


def settlement_store_id():
    """结算行归属优先，其次导入批次、导入人店铺。"""
    return func.coalesce(SettlementEntry.store_id,
        select(SalesImportBatch.store_id).where(SalesImportBatch.id == SettlementEntry.import_batch_id)
            .correlate(SettlementEntry).scalar_subquery(),
        select(User.store_id).join(SalesImportBatch, SalesImportBatch.user_id == User.id)
            .where(SalesImportBatch.id == SettlementEntry.import_batch_id).correlate(SettlementEntry).scalar_subquery())


def income_only_entry_condition():
    """与导入共用精确清单；旧已出库/待确认记录只读适用，不回写历史。"""
    store = exists(select(Store.id).where(Store.id == settlement_store_id(),
        Store.platform == _GIFT_POLICY["platform"], Store.platform_store_id == _GIFT_POLICY["platform_store_id"])
        .correlate(SettlementEntry))
    rows = [and_(SettlementEntry.order_id == order, SettlementEntry.platform_sku_id == sku,
                 SettlementEntry.order_created_at >= datetime.combine(day, time.min),
                 SettlementEntry.order_created_at < datetime.combine(day + timedelta(days=1), time.min))
            for order, sku, day in sorted(_GIFT_ORDERS)]
    return and_(SettlementEntry.transaction_type == "订单", store, or_(*rows))


async def income_only_sale_ids(db, sale_ids, *, source_entry_ids=None, source_store_id=None, review_ids=None):
    """仅同店且单一来源可免成本；限定导出来源，冲突ID交由调用方标记待核对。"""
    ids = set(sale_ids)
    if not ids or source_entry_ids is not None and not source_entry_ids:
        return set()
    # 单组件通常同时保存直接和分摊关联，UNION按来源/销售去重，不能误判为两个来源。
    links = select(SettlementEntry.id.label("entry_id"), SettlementEntry.sale_id.label("sale_id")).where(
        SettlementEntry.sale_id.in_(ids)).union(select(SettlementEntryAllocation.settlement_entry_id,
            SettlementEntryAllocation.sale_id).where(SettlementEntryAllocation.sale_id.in_(ids))).subquery()
    filters = [SettlementEntry.id.in_(source_entry_ids)] if source_entry_ids is not None else []
    if source_store_id is not None:
        filters.append(settlement_store_id() == source_store_id)
    candidates = dict((await db.execute(select(links.c.sale_id, settlement_store_id())
        .select_from(links).join(SettlementEntry, SettlementEntry.id == links.c.entry_id)
        .where(income_only_entry_condition(), *filters))).all())
    if not candidates:
        return set()
    # 范围外来源不能授予豁免；但获准赠品若共用销售，仍须核对全部引用。
    # 这里只计引用数，不读取范围外来源的名称、金额等业务字段。
    ownership = (await db.execute(select(Sale.id, func.coalesce(Sale.store_id, User.store_id),
        func.count(links.c.entry_id)).join(links, links.c.sale_id == Sale.id)
        .outerjoin(User, User.id == Sale.user_id).where(Sale.id.in_(candidates))
        .group_by(Sale.id, Sale.store_id, User.store_id))).all()
    exempt = {sale_id for sale_id, owner, count in ownership
              if count == 1 and owner is not None and owner == candidates[sale_id]}
    if review_ids is not None:
        review_ids.update(set(candidates) - exempt)
    return exempt


def gift_stock_retry_condition():
    """兼容旧误标为仅结算的赠品；只让尚未关联出库的精确来源进入待确认。"""
    linked = exists(select(SettlementEntryAllocation.id).where(
        SettlementEntryAllocation.settlement_entry_id == SettlementEntry.id,
        SettlementEntryAllocation.sale_id.is_not(None)).correlate(SettlementEntry))
    return and_(income_only_entry_condition(), SettlementEntry.sale_id.is_(None), ~linked,
        SettlementEntry.quantity > 0, SettlementEntry.net_product_sales >= 0,
        SettlementEntry.raw_data["_settlement_only_reason"].as_string() == INCOME_ONLY_GIFT)


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
