"""只读库存日结：当前有效批次/FIFO明细及已有数量更正日志，不补造撤销流水。"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case, func, select

from models import BatchChangeLog, InventoryBatch, Sale, SaleCostDetail

BUSINESS_ZONE = ZoneInfo("Asia/Shanghai")
MAX_SOURCE_ROWS = 10000


def business_today():
    return datetime.now(BUSINESS_ZONE).date()


def local_audit_time(value):
    return value.replace(tzinfo=timezone.utc).astimezone(BUSINESS_ZONE).replace(tzinfo=None)


def month_bounds(month):
    if month.day != 1 or month > business_today().replace(day=1):
        raise HTTPException(422, detail={"code": "inventory_month_invalid",
                                       "message": "请选择当月或以前月份，日期须为该月第一天。"})
    following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return month, min(following - timedelta(days=1), business_today())


def check_source_size(rows):
    if len(rows) > MAX_SOURCE_ROWS:
        raise HTTPException(422, detail={"code": "inventory_sources_too_large",
                                       "message": "所选商品的历史记录较多，请缩小商品范围或减少每页商品数。"})
    return rows


async def load_activity_sources(db, product_ids, start, end, sale_scope=None):
    """查询限于一页商品；出库按批次/日聚合，明细接口另外分页读取。"""
    if not product_ids:
        return [], [], [], []
    batches = check_source_size((await db.execute(select(InventoryBatch).where(
        InventoryBatch.product_id.in_(product_ids)).order_by(InventoryBatch.id)
        .limit(MAX_SOURCE_ROWS + 1))).scalars().all())
    ids = [batch.id for batch in batches]
    if not ids:
        return batches, [], [], []
    # 后来的数量更正也要读取，才能从当前数量还原早期基数；不读取日志身份或原因。
    changes = check_source_size((await db.execute(select(
        BatchChangeLog.id, BatchChangeLog.batch_id, BatchChangeLog.created_at,
        BatchChangeLog.before_values, BatchChangeLog.after_values,
    ).where(BatchChangeLog.batch_id.in_(ids), BatchChangeLog.action == "update")
      .order_by(BatchChangeLog.id).limit(MAX_SOURCE_ROWS + 1))).all())
    query = select(
        SaleCostDetail.batch_id.label("batch_id"),
        func.date(Sale.sold_at).label("day"),
        func.sum(SaleCostDetail.quantity).label("quantity"),
        func.min(Sale.sold_at).label("first_sale"),
        func.max(Sale.sold_at).label("last_sale"),
    ).join(Sale, Sale.id == SaleCostDetail.sale_id).where(SaleCostDetail.batch_id.in_(ids))
    if sale_scope is not None:
        query = query.where(sale_scope)
    # 当月内逐日读取；更早记录只按批次汇总，避免把多年日流水整体加载。
    daily = check_source_size((await db.execute(query.where(
        Sale.sold_at >= datetime.combine(start, time.min),
        Sale.sold_at < datetime.combine(end + timedelta(days=1), time.min),
    ).group_by(SaleCostDetail.batch_id, func.date(Sale.sold_at))
      .limit(MAX_SOURCE_ROWS + 1))).all())
    totals = (await db.execute(select(
        SaleCostDetail.batch_id.label("batch_id"),
        func.sum(SaleCostDetail.quantity).label("quantity"),
        func.sum(case(
            (Sale.sold_at < datetime.combine(start, time.min), SaleCostDetail.quantity), else_=0,
        )).label("opening_outbound"),
        func.min(Sale.sold_at).label("first_sale"),
        func.max(Sale.sold_at).label("last_sale"),
        func.sum(case((SaleCostDetail.quantity <= 0, 1), else_=0)).label("invalid_quantities"),
    ).join(Sale, Sale.id == SaleCostDetail.sale_id).where(
        SaleCostDetail.batch_id.in_(ids), *([sale_scope] if sale_scope is not None else []),
    ).group_by(SaleCostDetail.batch_id))).all()
    return batches, changes, daily, totals


def build_activity(batches, changes, sales, totals, start, end, *, full_scope, include_voided=True):
    """数量更正按日志日列调整；作废日移除余额；缺失/矛盾不伪造平账数。"""
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    result = {}
    events = []
    changes_by_batch, sales_by_batch = defaultdict(list), defaultdict(dict)
    total_by_batch = {row.batch_id: row for row in totals}
    for row in changes:
        changes_by_batch[row.batch_id].append(row)
    for row in sales:
        day = row.day if isinstance(row.day, date) else date.fromisoformat(row.day)
        sales_by_batch[row.batch_id][day] = int(row.quantity)

    def product_days(product_id):
        if product_id not in result:
            result[product_id] = {"days": {day: {
                "date": day, "opening_quantity": 0, "inbound": 0, "outbound": 0,
                "adjustment": 0, "closing_quantity": 0,
            } for day in days}, "warnings": set()}
        return result[product_id]

    for batch in batches:
        product = product_days(batch.product_id)
        arrival = batch.arrived_at.date()
        void_time = local_audit_time(batch.voided_at) if batch.voided_at else None
        void_day = void_time.date() if void_time else None
        cancelled_before_receipt = void_time is not None and void_time < batch.arrived_at
        visible_receipt = (include_voided or not batch.voided_at) and not cancelled_before_receipt
        updates = []
        invalid = False
        previous = None
        for row in changes_by_batch[batch.id]:
            try:
                before, after = int(row.before_values["quantity"]), int(row.after_values["quantity"])
                if previous is not None and before != previous:
                    invalid = True
                previous = after
                if after != before:
                    updates.append((local_audit_time(row.created_at), after - before, row.id))
            except (KeyError, TypeError, ValueError):
                invalid = True
        if previous is not None and previous != batch.quantity:
            invalid = True
        original = batch.quantity - sum(delta for _at, delta, _id in updates)
        if original <= 0:
            invalid = True
        receipt_known = not invalid
        receipt = (original + sum(delta for at, delta, _id in updates if at < batch.arrived_at)) if not cancelled_before_receipt else 0
        total = total_by_batch.get(batch.id)
        outbound_before = int(total.opening_outbound) if total else 0
        if total and total.invalid_quantities:
            invalid = True
        if full_scope and total:
            if total.first_sale.date() < arrival or (void_time and total.last_sale > void_time):
                invalid = True
            if batch.quantity - int(total.quantity) != batch.remaining_quantity:
                invalid = True
        elif full_scope and batch.quantity != batch.remaining_quantity:
            invalid = True
        if invalid and arrival <= end:
            product["warnings"].add("inventory_history_inconsistent")
        if any(isinstance(row.before_values, dict) and isinstance(row.after_values, dict)
               and row.before_values.get("arrived_at") != row.after_values.get("arrived_at")
               for row in changes_by_batch[batch.id]):
            product["warnings"].add("inventory_arrival_restatement")
        if visible_receipt and start <= arrival <= end:
            events.append({"product_id": batch.product_id, "source_id": batch.id, "batch_id": batch.id,
                           "batch_no": batch.batch_no, "kind": "inbound", "occurred_at": batch.arrived_at,
                           "quantity": receipt if receipt_known else None, "reference": batch.dingtalk_order_no or ""})
        adjustments = defaultdict(int)
        for at, delta, source_id in updates:
            if at >= batch.arrived_at and not cancelled_before_receipt:
                adjustments[at.date()] += delta
                if start <= at.date() <= end:
                    events.append({"product_id": batch.product_id, "source_id": source_id,
                                   "batch_id": batch.id, "batch_no": batch.batch_no,
                                   "kind": "quantity_correction", "occurred_at": at,
                                   "quantity": delta, "reference": ""})
        balance = (original + sum(delta for at, delta, _id in updates if at.date() < start)
                   - outbound_before) if arrival < start else 0
        if void_day and void_day < start:
            balance = 0
        if cancelled_before_receipt:
            balance = 0
        for day in days:
            opening = balance
            inbound = receipt if day == arrival and visible_receipt else 0
            outbound = sales_by_batch[batch.id].get(day, 0)
            adjustment = adjustments[day] if day >= arrival else 0
            balance += (receipt if day == arrival else 0) + adjustment - outbound
            if balance < 0:
                invalid = True
                product["warnings"].add("inventory_history_inconsistent")
            if day == void_day and arrival <= day:
                removed = -balance
                adjustment += removed
                balance = 0
                events.append({"product_id": batch.product_id, "source_id": batch.id,
                               "batch_id": batch.id, "batch_no": batch.batch_no,
                               "kind": "void", "occurred_at": void_time,
                               "quantity": None if invalid else removed, "reference": ""})
            item = product["days"][day]
            item["inbound"] = (None if item["inbound"] is None or
                               (day == arrival and visible_receipt and not receipt_known)
                               else item["inbound"] + inbound)
            item["outbound"] += outbound
            item["opening_quantity"] += opening
            item["closing_quantity"] += balance
            item["adjustment"] += adjustment
        if invalid:
            # 日结本就只对全量范围提供；异常不靠虚构调整强行对平。
            product["invalid"] = True
    for product in result.values():
        product["warnings"] = sorted(product["warnings"])
        if not full_scope or product.get("invalid"):
            for item in product["days"].values():
                for key in ("opening_quantity", "closing_quantity", "adjustment"):
                    item[key] = None
        product["days"] = list(product["days"].values())
    return result, events


def activity_rows(products, activity, start, end, *, full_scope):
    """页面及导出共享同一响应行构建，空商品与权限空值口径一致。"""
    items = []
    for product in products:
        value = activity.get(product.id)
        if value is None:
            value = {"days": [{
                "date": start + timedelta(days=i), "opening_quantity": 0 if full_scope else None,
                "closing_quantity": 0 if full_scope else None, "inbound": 0, "outbound": 0,
                "adjustment": 0 if full_scope else None,
            } for i in range((end - start).days + 1)], "warnings": []}
        items.append({"product_id": product.id, "sku": product.sku, "name": product.name,
                      "image": product.image, "days": value["days"], "warnings": value["warnings"]})
    return items
