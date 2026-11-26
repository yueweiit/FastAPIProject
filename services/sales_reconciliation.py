"""销售对账导出的来源查询与投影；仅 SELECT，不重算或写入 FIFO。"""
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from models import Product, SalesImportBatch, SettlementEntry, SettlementEntryAllocation, User
from services.financial_facts import settlement_amounts, settlement_accounting_date
from services.report_exchange_rates import convert_report_amount, store_rate_key
from services.sales_report_currency import sales_report_rates
from services.sales_product_identity import source_local_specification


TEXT = {"zh-CN": {
    "source_sheet": "原始结算对账", "detail_sheet": "销售拆分明细",
    "fulfilled": "已出库", "pending": "待确认", "settlement": "仅结算",
    "cost_actual": "已核对", "cost_unverified": "未核对（沿用现有成本）", "cost_review": "待核对", "cost_na": "不出库",
    "cost_exempt": "赠品免成本",
    "review": "待核对", "legacy": "无原始结算记录", "unknown_store": "未关联店铺",
    "missing": "来源关联的出库明细不完整，出库数量、成本与毛利待核对",
    "shared": "销售明细关联多个来源行，不能重复归集出库数量与成本",
    "unverified": "出库成本明细不完整或异常，成本与毛利留空",
    "no_sale": "尚无可核对的出库明细或独立结算记录",
    "no_source": "历史销售未保存可访问的原始结算行，未推测来源数量或金额",
    "legacy_amount": "旧 CSV 未提供原始结算金额",
    "missing_fx": "汇率缺失，折人民币金额待核对",
    "provisional_fx": "本月手动核算汇率为暂估",
    "manual_fx": "手动", "manual_provisional_fx": "手动（暂估）",
    "period_conflict": "来源关联出库跨核算月份，无法采用单一汇率，折人民币金额待核对",
    "store_conflict": "原始结算店铺与关联销售的核算店铺不一致或销售归属缺失，折人民币金额请核对拆分明细",
    "too_large": "原始结算行或关联明细超过导出上限，请缩小范围。",
}}


def check_export_limit(count, limit):
    if count > limit:
        raise HTTPException(422, detail={"code": "sales_export_too_large", "max_rows": limit,
                                        "message": TEXT["zh-CN"]["too_large"]})


async def export_sources(db, user, items, *, month, keyword, product_id, import_batch_id, limit, store_id=None):
    """当前台账来源 + 同筛选待确认行；批次导出不继承页面筛选。"""
    owner = func.coalesce(SettlementEntry.store_id, SalesImportBatch.store_id, User.store_id)
    stmt = (select(SettlementEntry, owner.label("source_store_id"), SalesImportBatch.file_type)
            .outerjoin(SalesImportBatch, SalesImportBatch.id == SettlementEntry.import_batch_id)
            .outerjoin(User, User.id == SalesImportBatch.user_id))
    if user.role != "admin":
        stmt = stmt.where(owner == user.store_id)
    elif store_id is not None:
        stmt = stmt.where(owner == store_id)
    if import_batch_id is not None:
        stmt = stmt.where(SettlementEntry.import_batch_id == import_batch_id)
    else:
        sale_ids = [item.id for item in items if item.id > 0]
        event_ids = [-item.id for item in items if item.id < 0]
        pending = [SettlementEntry.mapping_status == "pending_confirmation"]
        if month:
            start = datetime.strptime(month, "%Y-%m")
            end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1)
            occurred = func.coalesce(SettlementEntry.order_created_at, SettlementEntry.settlement_date,
                                     SettlementEntry.imported_at)
            pending.extend((occurred >= start, occurred < end))
        if product_id is not None:
            pending.append(or_(SettlementEntry.product_id == product_id,
                SettlementEntry.id.in_(select(SettlementEntryAllocation.settlement_entry_id)
                    .where(SettlementEntryAllocation.product_id == product_id))))
        if keyword:
            pattern = f"%{keyword}%"
            products = select(Product.id).where(or_(Product.name.ilike(pattern), Product.sku.ilike(pattern)))
            pending.append(or_(SettlementEntry.order_id.ilike(pattern), SettlementEntry.platform_sku_id.ilike(pattern),
                SettlementEntry.product_name.ilike(pattern), SettlementEntry.raw_data["产品名"].as_string().ilike(pattern),
                SettlementEntry.transaction_type.ilike(pattern), SettlementEntry.product_id.in_(products),
                SettlementEntry.id.in_(select(SettlementEntryAllocation.settlement_entry_id)
                    .where(SettlementEntryAllocation.product_id.in_(products)))))
        stmt = stmt.where(or_(SettlementEntry.id.in_(event_ids), SettlementEntry.sale_id.in_(sale_ids),
            SettlementEntry.id.in_(select(SettlementEntryAllocation.settlement_entry_id)
                .where(SettlementEntryAllocation.sale_id.in_(sale_ids))), and_(*pending)))
    sources = (await db.execute(stmt.order_by(SettlementEntry.import_batch_id, SettlementEntry.source_row_number,
                                             SettlementEntry.id).limit(limit + 1))).all()
    check_export_limit(len(sources), limit)
    entry_ids = [source[0].id for source in sources]
    links = defaultdict(set)
    if entry_ids:
        allocations = (await db.execute(select(SettlementEntryAllocation.settlement_entry_id,
            SettlementEntryAllocation.sale_id).where(SettlementEntryAllocation.settlement_entry_id.in_(entry_ids),
                SettlementEntryAllocation.sale_id.is_not(None)).limit(limit + 1))).all()
        check_export_limit(len(allocations), limit)
        for entry_id, sale_id in allocations:
            links[entry_id].add(sale_id)
        for entry, _store_id, _file_type in sources:
            if entry.sale_id is not None:
                links[entry.id].add(entry.sale_id)
    linked_ids = {sale_id for ids in links.values() for sale_id in ids}
    shared_sales = set()
    if linked_ids:
        # 不读取其他来源的业务字段，仅核对同一销售是否被多个来源引用。
        references = select(SettlementEntry.id.label("entry_id"), SettlementEntry.sale_id.label("sale_id")).where(
            SettlementEntry.sale_id.in_(linked_ids)).union(select(SettlementEntryAllocation.settlement_entry_id,
                SettlementEntryAllocation.sale_id).where(SettlementEntryAllocation.sale_id.in_(linked_ids))).subquery()
        shared_sales = set((await db.execute(select(references.c.sale_id).group_by(references.c.sale_id)
            .having(func.count() > 1))).scalars().all())
    return sources, links, shared_sales


def _source_product_name(entry):
    raw = entry.raw_data or {}
    return raw["产品名"] if "产品名" in raw else entry.product_name


async def reconciliation_rates(db, sources, links, rows):
    by_id = {row["id"]: row for row in rows}
    keys, conflicts, source_months = {}, {}, {}
    for entry, store_id, _file_type in sources:
        sales = [by_id[sale_id] for sale_id in links.get(entry.id, ()) if sale_id in by_id]
        months = {row["exchange_rate_month"] for row in sales if row.get("exchange_rate_month")}
        day = date.fromisoformat(next(iter(months)) + "-01") if len(months) == 1 else settlement_accounting_date(entry).date()
        keys[entry.id] = store_rate_key(store_id, entry.currency, day)
        conflicts[entry.id] = []
        # 只核对已经获准输出的明细；原始店铺是来源凭据，不能随创建人改绑而改写。
        if any(row.get("exchange_rate_store_id") != store_id for row in sales):
            conflicts[entry.id].append("store_conflict")
        if len(months) > 1:
            conflicts[entry.id].append("period_conflict")
        source_months[entry.id] = "、".join(sorted(months)) or day.isoformat()[:7]
    rates = await sales_report_rates(db, keys.values())
    return {entry_id: {**rates[key], "report_month": source_months[entry_id],
                      "rate_to_cny": Decimal(1) if key[1] == "CNY" else None,
                      "rate_date": None, "source": "CNY" if key[1] == "CNY" else None,
                      "mode": "auto", "is_provisional": False,
                      "status": "available" if key[1] == "CNY" else conflicts[entry_id][0],
                      "conflicts": conflicts[entry_id]}
            if conflicts[entry_id] else rates[key]
            for entry_id, key in keys.items()}


def reconciliation_rows(sources, links, rows, stores, *, limit, rates, shared_sales=()):
    """按结算记录 ID 保留一行；多来源、缺失/无权组件不推测数量和成本。"""
    labels = TEXT["zh-CN"]
    by_id = {row["id"]: row for row in rows}
    source_by_sale = defaultdict(list)
    for entry, _store_id, _file_type in sources:
        for sale_id in links[entry.id]:
            source_by_sale[sale_id].append(entry)
        if -entry.id in by_id:
            source_by_sale[-entry.id].append(entry)
    result = []
    for entry, store_id, file_type in sources:
        ids = links[entry.id]
        sales = [by_id[key] for key in sorted(ids) if key in by_id]
        missing = len(sales) != len(ids)
        shared = any(sale_id in shared_sales or len(source_by_sale[sale_id]) != 1 for sale_id in ids)
        quantity_known = not (missing or shared)
        event = by_id.get(-entry.id)
        pending = entry.mapping_status == "pending_confirmation"
        reasons = [entry.mapping_error] if entry.mapping_error else []
        cost = None
        income_only = bool(event and event.get("income_only")) or (
            not missing and not shared and bool(sales) and all(row.get("income_only") for row in sales))
        if income_only:
            status, cost = ("pending" if pending else "fulfilled" if ids else "settlement"), Decimal(0)
        elif pending:
            status = "pending"
        elif ids:
            status = "fulfilled"
            if missing:
                reasons.append(labels["missing"])
            elif shared:
                reasons.append(labels["shared"])
            elif any(row["cost_status"] == "needs_review" or row.get("cost_breakdown_review_reason")
                     or row.get("order_total_cost_cny") is None for row in sales):
                reasons.append(labels["unverified"])
            else:
                cost = sum((row["order_total_cost_cny"] for row in sales), Decimal(0))
            if cost is None:
                status = "review"
        elif event:
            status, cost = "settlement", Decimal(0)
        else:
            status = "review"
            reasons.append(labels["no_sale"])
        original_net, original_total = entry.net_product_sales, entry.settlement_total
        rate_info = rates[entry.id]
        rate = rate_info["rate_to_cny"]
        if rate_info.get("conflicts"):
            reasons.extend(labels[code] for code in rate_info["conflicts"])
            status = "review"
        if file_type == "legacy_csv":
            original_net = original_total = None
            reasons.append(labels["legacy_amount"])
        elif rate is None and not rate_info.get("conflicts"):
            reasons.append(labels["missing_fx"])
        if rate_info.get("is_provisional"):
            reasons.append(labels["provisional_fx"])
        revenue = expense = profit = None
        if original_net is not None and original_total is not None:
            net, fee = settlement_amounts(original_net, original_total, entry.transaction_type)
            revenue, expense = convert_report_amount(net, rate_info), convert_report_amount(fee, rate_info)
            if cost is not None and revenue is not None and expense is not None:
                profit = revenue - expense - cost
        raw = entry.raw_data or {}
        result.append(dict(
            source_ref=f"E{entry.id}", store_name=stores.get(store_id) or labels["unknown_store"],
            source_file=entry.source_file, source_sheet=entry.source_sheet, source_row_number=entry.source_row_number,
            order_no=raw.get("订单ID/调整单ID") or entry.order_id,
            sku=entry.platform_sku_id, product_name=_source_product_name(entry),
            source_specification=entry.sku_name,
            requested_local_specification=source_local_specification(raw) or None,
            transaction_type=entry.transaction_type, quantity=entry.quantity, order_created_at=entry.order_created_at,
            settlement_date=entry.settlement_date, currency=entry.currency,
            net_product_sales=original_net, settlement_total=original_total, status=labels[status],
            reason="；".join(dict.fromkeys(reasons)), line_count=len(sales) if quantity_known else None,
            actual_quantity=sum(row["quantity"] for row in sales) if quantity_known else None, revenue=revenue, cost=cost,
            expense=expense, profit=profit, rate=rate, rate_date=rate_info["rate_date"],
            rate_month=rate_info["report_month"],
            rate_source=labels["manual_provisional_fx" if rate_info.get("is_provisional") else "manual_fx"]
                if rate_info.get("mode") == "manual" else rate_info["source"],
            detail_ids="\n".join([f"S{row['id']}" for row in sales] + ([f"F{entry.id}"] if event else [])),
            cost_status=labels["cost_exempt"] if income_only else labels["cost_na"] if status == "settlement" else labels["cost_review"] if cost is None
                else labels["cost_actual"] if all(row["cost_status"] == "actual" for row in sales) else labels["cost_unverified"],
        ))
    for row in rows:
        parents = source_by_sale[row["id"]]
        row["detail_id"] = f"S{row['id']}" if row["id"] > 0 else f"F{-row['id']}"
        if parents:
            row.update(source_ref="\n".join(f"E{entry.id}" for entry in parents),
                source_file="\n".join(entry.source_file or "" for entry in parents),
                source_sheet="\n".join(entry.source_sheet or "" for entry in parents),
                source_row_number="\n".join(str(entry.source_row_number) for entry in parents),
                source_sku_id="\n".join(entry.platform_sku_id or "" for entry in parents),
                source_product_name="\n".join(_source_product_name(entry) or "" for entry in parents),
                source_specification="\n".join(entry.sku_name or "" for entry in parents),
                requested_local_specification="\n".join(source_local_specification(entry.raw_data or {}) for entry in parents))
        else:
            row.update(source_ref=row["detail_id"], source_file=None, source_sheet=None, source_row_number=None,
                       source_sku_id=None, source_product_name=None, source_specification=None, requested_local_specification=None)
            result.append(dict(source_ref=row["source_ref"], store_name=row.get("store_name"),
                order_no=row.get("display_order_no") or row["order_no"], sku=row.get("sku"),
                product_name=row.get("product_name"), status=labels["legacy"], reason=labels["no_source"],
                line_count=1, actual_quantity=row["quantity"], revenue=row["sales_revenue_cny"],
                cost=row.get("order_total_cost_cny") if row["cost_status"] != "needs_review" else None,
                expense=row["platform_fee_cny"], profit=row["profit_cny"] if row["cost_status"] != "needs_review" and row.get("order_total_cost_cny") is not None else None,
                cost_status=labels["cost_review"] if row["cost_status"] == "needs_review" or row.get("order_total_cost_cny") is None
                    else labels["cost_actual"] if row["cost_status"] == "actual" else labels["cost_unverified"],
                detail_ids=row["detail_id"]))
    check_export_limit(len(result), limit)
    return result
