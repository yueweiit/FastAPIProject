"""待确认分类、范围与重新处理；复用原始来源，不重建结算行。"""
import re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case, delete, or_, select

from models import Product, Sale, SalesImportBatch, SettlementEntry, SettlementEntryAllocation, Store, StoreProduct
from schemas import PendingComponent
from services.financial_facts import COMPENSATION_TYPES, SETTLEMENT_ONLY_KEY, SETTLEMENT_ONLY_REASONS

MAX_RETRY_ROWS = 200
REASON_PATTERNS = (
    ("stock_shortage", ("销售日可用库存不足%", "截至销售日可用库存不足%", "库存不足%")),
    ("product_ambiguous", ("%匹配到多个本地商品%", "%映射到多个本地商品%")),
    ("expression_invalid", ("产品名为空%", "商品名称组合格式无效%", "商品数量必须大于%")),
    ("product_missing", ("未找到本地商品映射%", "未配置平台 SKU ID 映射%")),
    ("mapping_invalid", ("平台 SKU 映射在%", "平台 SKU ID 未配置商品组件%", "商品映射失败%")),
    ("order_conflict", ("销售订单已存在%", "同一订单%")),
    ("source_invalid", ("待确认来源%", "已确认的商品%")),
)
REASON_CODES = tuple(code for code, _patterns in REASON_PATTERNS) + ("other",)
REASON_LABELS = {"stock_shortage": "销售日库存不足", "product_missing": "未找到本地商品",
    "product_ambiguous": "商品名称或映射不唯一", "expression_invalid": "产品名或组合格式错误",
    "mapping_invalid": "历史映射配置无效", "order_conflict": "订单重复或冲突",
    "source_invalid": "历史来源待核对", "other": "其他原因"}


def reason_code(reason):
    for code, patterns in REASON_PATTERNS:
        if any(re.fullmatch(re.escape(pattern).replace("%", ".*"), reason or "", re.S | re.I) for pattern in patterns):
            return code
    return "other"


def reason_expression():
    return case(*[(or_(*(SettlementEntry.mapping_error.like(p) for p in patterns)), code)
                  for code, patterns in REASON_PATTERNS], else_="other")


def visible_conditions(user):
    conditions = [or_(SettlementEntry.transaction_type.is_(None),
                      SettlementEntry.transaction_type.notin_(COMPENSATION_TYPES))]
    if user.role != "admin":
        if user.store_id is None:
            raise HTTPException(403, detail="当前账号未绑定店铺，无法查看店铺敏感数据")
        conditions.append(SettlementEntry.store_id == user.store_id)
    return conditions


def pending_conditions(user, reason=None, keyword=None):
    conditions = [*visible_conditions(user), SettlementEntry.mapping_status == "pending_confirmation"]
    if reason:
        conditions.append(reason_expression() == reason)
    if keyword and keyword.strip():
        value = f"%{keyword.strip()}%"
        conditions.append(or_(*(column.ilike(value) for column in (SettlementEntry.order_id,
            SettlementEntry.platform_sku_id, SettlementEntry.product_name, SettlementEntry.sku_name,
            SettlementEntry.source_file, SettlementEntry.mapping_error))))
    return conditions


def saved_components(entry):
    resolution = (entry.raw_data or {}).get("_pending_resolution", {})
    return resolution.get("components", []) if isinstance(resolution, dict) else []


async def retry_pending(db, user, data):
    from routers.sales import (_allocate_available_stock, _order_no,
        _order_settlement_only_reason, _resolve_product_components, _write_candidate_sales)
    from services.fifo import lock_fifo_batches

    conditions = visible_conditions(user) if data.entry_ids is not None else pending_conditions(user, data.reason, data.keyword)
    if data.entry_ids is not None:
        conditions.append(SettlementEntry.id.in_(data.entry_ids))
    selected = (await db.execute(select(SettlementEntry.id, SettlementEntry.import_batch_id)
        .where(*conditions).order_by(SettlementEntry.id).limit(MAX_RETRY_ROWS + 1))).all()
    if len(selected) > MAX_RETRY_ROWS:
        raise HTTPException(422, detail={"code": "pending_retry_too_large", "max_rows": MAX_RETRY_ROWS,
            "message": "单次最多重新处理200条，请按原因筛选或勾选较少记录。"})
    ids = [row.id for row in selected]
    if data.entry_ids is not None and set(ids) != set(data.entry_ids):
        raise HTTPException(404, detail={"code": "pending_not_found", "message": "部分待确认记录不存在或无权处理，请刷新。"})
    if not ids:
        return dict(confirmed_rows=0, pending_rows=0, skipped_rows=0, sales_created=0, results=[])

    # 导入头 -> 结算行/分配 -> 全量FIFO商品批次；与文件撤销串行，批次锁顺序不变。
    header_ids = sorted({row.import_batch_id for row in selected if row.import_batch_id is not None})
    headers = {row.id: row for row in (await db.execute(select(SalesImportBatch)
        .where(SalesImportBatch.id.in_(header_ids)).order_by(SalesImportBatch.id)
        .with_for_update().execution_options(populate_existing=True))).scalars().all()} if header_ids else {}
    if any(header.status != "completed" for header in headers.values()):
        raise HTTPException(409, detail={"code": "pending_import_rolled_back", "message": "来源导入已撤销，未重新处理。"})
    entries = (await db.execute(select(SettlementEntry).where(SettlementEntry.id.in_(ids))
        .order_by(SettlementEntry.id).with_for_update().execution_options(populate_existing=True))).scalars().all()
    expected = {row.id: row.import_batch_id for row in selected}
    if len(entries) != len(ids) or any(entry.import_batch_id != expected[entry.id] for entry in entries):
        raise HTTPException(409, detail={"code": "pending_source_changed", "message": "待确认来源已改变，请刷新后重试。"})
    allocations = (await db.execute(select(SettlementEntryAllocation).where(
        SettlementEntryAllocation.settlement_entry_id.in_(ids)).order_by(SettlementEntryAllocation.settlement_entry_id,
        SettlementEntryAllocation.id).with_for_update().execution_options(populate_existing=True))).scalars().all()
    linked = {row.settlement_entry_id for row in allocations if row.sale_id is not None}
    from services.finance_config import financial_controls_enabled
    financial_linked = set()
    if financial_controls_enabled():
        from models import FinancialExpense, SettlementClassification
        for model in (FinancialExpense, SettlementClassification):
            financial_linked.update((await db.execute(select(model.settlement_entry_id).where(
                model.settlement_entry_id.in_(ids)).order_by(model.id).with_for_update())).scalars().all())
    products = (await db.execute(select(Product).order_by(Product.id))).scalars().all()
    by_id = {p.id: p for p in products}
    if data.components is not None and any(c.product_id not in by_id for c in data.components):
        raise HTTPException(422, detail={"code": "pending_component_invalid", "message": "确认的本地商品已不存在，请重新选择。"})
    store_ids = {entry.store_id for entry in entries if entry.store_id is not None}
    active_stores = set((await db.execute(select(Store.id).where(Store.id.in_(store_ids), Store.is_active.is_(True)))).scalars().all()) if store_ids else set()
    store_products = (await db.execute(select(StoreProduct).where(StoreProduct.is_active.is_(True)))).scalars().all() if any(
        header.file_type == "legacy_csv" for header in headers.values()) else []
    candidates, results = [], []
    for entry in sorted(entries, key=lambda e: (e.import_batch_id or 0, e.source_row_number, e.id)):
        if entry.mapping_status != "pending_confirmation":
            results.append(dict(id=entry.id, status="skipped", message="记录已处理，未重复出库。"))
            continue
        header = headers.get(entry.import_batch_id)
        error = None
        if (not header or header.file_type not in ("legacy_csv", "order_detail") or entry.store_id not in active_stores
            or header.store_id != entry.store_id or user.role != "admin" and header.store_id != user.store_id):
            error = "待确认来源缺少有效导入批次或店铺，请核对历史来源。"
        elif entry.sale_id is not None or entry.id in linked:
            error = "待确认来源已关联销售，请核对状态，未重复出库。"
        elif entry.id in financial_linked:
            error = "待确认来源已有费用凭证或结算分类，请先核对，未重新出库。"
        row = dict(entry.raw_data or {})
        row["_row_number"] = entry.source_row_number
        sold_at = entry.order_created_at or entry.settlement_date
        settlement_only = None
        is_inventory = False
        if not error:
            try:
                if header.file_type == "order_detail":
                    settlement_only = row.get(SETTLEMENT_ONLY_KEY) if row.get(SETTLEMENT_ONLY_KEY) in SETTLEMENT_ONLY_REASONS else None
                    if not settlement_only and (entry.transaction_type or "订单") == "订单":
                        settlement_only = _order_settlement_only_reason(row, entry.net_product_sales, entry.settlement_total)
                    is_inventory = (entry.transaction_type or "订单") == "订单" and entry.quantity > 0 and entry.net_product_sales >= 0 and not settlement_only
                else:
                    is_inventory = True
                if is_inventory and (sold_at is None or not entry.order_id or not entry.platform_sku_id or not 0 < entry.quantity <= 2147483647):
                    error = "待确认来源日期、订单标识或数量不完整，未重新出库。"
            except (ValueError, TypeError):
                error = "待确认来源金额或数量不合法，请核对原始来源。"
        components, manual = [], None
        if not error:
            manual = [c.model_dump() for c in data.components] if data.components is not None else saved_components(entry)
            if manual:
                try:
                    counts = Counter()
                    for raw in manual:
                        component = PendingComponent.model_validate(raw)
                        if component.product_id not in by_id:
                            raise ValueError()
                        counts[component.product_id] += component.multiplier
                    components = [{"product": by_id[key], "component_name": by_id[key].name, "multiplier": count}
                                  for key, count in sorted(counts.items())]
                except (ValueError, TypeError):
                    error = "已确认的商品组件失效，请重新选择本地商品。"
            else:
                components, error = _resolve_product_components(row, products, store_products,
                    sold_at.date() if sold_at else None, strict_names=header.file_type == "order_detail")
                components = components or []
                if not is_inventory:
                    error = None
            if is_inventory and any(entry.quantity * c["multiplier"] > 2147483647 for c in components):
                error = "待确认来源组件数量超出范围，请核对倍数。"
                components = []
        order_no = (f"{row.get('日期')}-{row.get('SKU')}"[:64] if header and header.file_type == "legacy_csv"
                    else _order_no(entry.order_id, entry.platform_sku_id))
        candidates.append(dict(entry=entry, row=row, components=components, quantity=entry.quantity,
            sold_at=sold_at, order_no=order_no, net_product_sales=entry.net_product_sales,
            other_expense=entry.net_product_sales-entry.settlement_total, is_inventory_sale=is_inventory and not error,
            mapping_error=error, settlement_only_reason=settlement_only,
            manual=manual if data.components is not None and not error else None))
    orders = Counter(c["order_no"] for c in candidates if c["is_inventory_sale"])
    required = {part["product"].id for c in candidates if c["is_inventory_sale"] for part in c["components"]}
    batches = await lock_fifo_batches(db, required)
    existing = set((await db.execute(select(Sale.order_no).where(Sale.order_no.in_(orders))
        .with_for_update())).scalars().all()) if orders else set()
    for candidate in candidates:
        if candidate["is_inventory_sale"] and (candidate["order_no"] in existing or orders[candidate["order_no"]] > 1):
            candidate["mapping_error"] = "销售订单已存在或同一订单有多条待确认来源，请核对重复记录。"
            candidate["is_inventory_sale"] = False
    _allocate_available_stock(candidates, {}, batches=batches)
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    created = 0
    for candidate in candidates:
        entry = candidate["entry"]
        before = entry.mapping_error
        sales = await _write_candidate_sales(db, candidate, user_id=user.id, store_id=entry.store_id)
        error = candidate["mapping_error"]
        raw = dict(entry.raw_data or {})
        audit = {"at": now, "actor_id": user.id, "before_reason": before, "after_reason": error}
        if candidate["manual"]:
            audit["components"] = candidate["manual"]
        raw["_pending_last_attempt"] = audit
        if candidate["manual"]:
            raw["_pending_resolution"] = {"components": candidate["manual"], "actor_id": user.id, "at": now}
        if candidate["manual"] or not error:
            raw["_pending_resolution_history"] = [*(raw.get("_pending_resolution_history") or []), audit]
        if candidate["settlement_only_reason"]:
            raw[SETTLEMENT_ONLY_KEY] = candidate["settlement_only_reason"]
        entry.raw_data = raw
        entry.mapping_error = error[:500] if error else None
        entry.mapping_status = "pending_confirmation" if error else "confirmed"
        # 已关联销售的异常来源必须保留其关系，不能删除后再次扣库。
        if entry.sale_id is None and entry.id not in linked and entry.id not in financial_linked:
            await db.execute(delete(SettlementEntryAllocation).where(SettlementEntryAllocation.settlement_entry_id == entry.id))
            parts = candidate["components"]
            entry.product_id = parts[0]["product"].id if len(parts) == 1 else None
            entry.sale_id = next(iter(sales.values())).id if len(sales) == 1 else None
            for part in parts:
                sale = sales.get(part["product"].id)
                db.add(SettlementEntryAllocation(settlement_entry_id=entry.id, product_id=part["product"].id,
                    sale_id=sale.id if sale else None, component_name=part["component_name"],
                    multiplier=part["multiplier"], quantity=entry.quantity * part["multiplier"]))
        count = len(sales)
        created += count
        if count:
            headers[entry.import_batch_id].sales_created += count
        results.append(dict(id=entry.id, status="pending" if error else "confirmed", reason_code=reason_code(error) if error else None,
            message=error or ("已确认并完成销售出库。" if sales else "已确认结算，无库存出库。")))
    await db.flush()
    pending_headers = Counter((await db.execute(select(SettlementEntry.import_batch_id).where(
        SettlementEntry.import_batch_id.in_(header_ids), SettlementEntry.mapping_status == "pending_confirmation")
        .order_by(SettlementEntry.id).with_for_update())).scalars().all()) if header_ids else Counter()
    for key, header in headers.items():
        header.pending_confirmation = pending_headers[key]
    from services.cost_layers import invalidate_finance_cache
    if isinstance(getattr(db, "info", None), dict):
        invalidate_finance_cache(db)
    await db.commit()
    return dict(confirmed_rows=sum(r["status"] == "confirmed" for r in results),
        pending_rows=sum(r["status"] == "pending" for r in results), skipped_rows=sum(r["status"] == "skipped" for r in results),
        sales_created=created, results=results)
