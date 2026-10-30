"""财务取数的公共规则；不提交事务、不改变库存或历史成本。"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import exists, func, or_, select

from models import (
    Product, SalesImportBatch, SettlementEntry, SettlementEntryAllocation,
    StoreProfitLossReport, User,
)


COMPENSATION_TYPES = {"物流赔付", "平台赔付", "物料赔付"}
ZERO = Decimal("0")


def valid_rate(currency, rate):
    if str(currency or "").strip().upper() == "CNY":
        return Decimal("1")
    if rate is None:
        return None
    value = Decimal(str(rate))
    return value if value.is_finite() and value > 0 else None


def settlement_amounts(net_sales, settlement_total, transaction_type=None):
    """赔付按实际结算额计入收入；负数赔付冲减收入，不重复生成费用。"""
    if transaction_type in COMPENSATION_TYPES:
        return settlement_total, ZERO
    return net_sales, net_sales - settlement_total


def settlement_expense(net_sales, settlement_total, transaction_type=None):
    return settlement_amounts(net_sales, settlement_total, transaction_type)[1]


@dataclass(frozen=True)
class FinancialEvent:
    entry_id: int
    store_id: int | None
    occurred_at: datetime
    revenue: Decimal | None
    expense: Decimal | None
    source_amount: Decimal
    currency: str
    product_id: int | None = None
    platform_sku_id: str = ""
    uses_import_date: bool = False
    transaction_type: str = ""
    order_no: str = ""
    original_revenue: Decimal = ZERO
    original_expense: Decimal = ZERO
    rate: Decimal | None = None
    rate_date: date | None = None
    rate_source: str | None = None
    created_at: datetime | None = None
    product_name: str = ""
    quantity: int = 0
    source_type: str = ""


def _unlinked_settlement_stmt(store_ids, start, end, *, product_id=None, keyword=None, entry_ids=None):
    linked_sale = exists(select(SettlementEntryAllocation.id).where(
        SettlementEntryAllocation.settlement_entry_id == SettlementEntry.id,
        SettlementEntryAllocation.sale_id.is_not(None),
    ))
    occurred = func.coalesce(SettlementEntry.settlement_date, SettlementEntry.order_created_at,
                             SettlementEntry.imported_at)
    owner = func.coalesce(SettlementEntry.store_id, SalesImportBatch.store_id, User.store_id)
    stmt = (select(SettlementEntry, owner.label("financial_store_id"))
            .outerjoin(SalesImportBatch, SalesImportBatch.id == SettlementEntry.import_batch_id)
            .outerjoin(User, User.id == SalesImportBatch.user_id)
            .where(SettlementEntry.sale_id.is_(None), ~linked_sale,
                   or_(func.coalesce(SettlementEntry.transaction_type, "订单") != "订单",
                       SettlementEntry.quantity <= 0, SettlementEntry.net_product_sales < 0,
                       SalesImportBatch.file_type == "manual_settlement")))
    if store_ids is not None:
        stmt = stmt.where(owner.in_(store_ids))
    if entry_ids is not None:
        stmt = stmt.where(SettlementEntry.id.in_(entry_ids))
    if start is not None:
        stmt = stmt.where(occurred >= datetime.combine(start, time.min))
    if end is not None:
        stmt = stmt.where(occurred < datetime.combine(end, time.min))
    if product_id is not None:
        # 组合事件无已保存金额分摊时不猜测组件份额。
        stmt = stmt.where(SettlementEntry.product_id == product_id)
    if keyword:
        matching_products = select(Product.id).where(or_(Product.sku.ilike(f"%{keyword}%"),
                                                        Product.name.ilike(f"%{keyword}%")))
        stmt = stmt.where(or_(SettlementEntry.product_id.in_(matching_products),
                              SettlementEntry.platform_sku_id.ilike(f"%{keyword}%"),
                              SettlementEntry.product_name.ilike(f"%{keyword}%"),
                              SettlementEntry.transaction_type.ilike(f"%{keyword}%")))
    return stmt.order_by(occurred, SettlementEntry.id)


async def count_unlinked_settlement_events(db, store_ids, start, end, *, product_id=None, keyword=None):
    stmt = _unlinked_settlement_stmt(store_ids, start, end, product_id=product_id, keyword=keyword)
    return await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))


async def unlinked_settlement_events(db, store_ids, start, end, *, product_id=None, keyword=None, entry_ids=None):
    """仅取没有出库销售关联的财务事件，避免与销售收入重复计入。"""
    stmt = _unlinked_settlement_stmt(store_ids, start, end, product_id=product_id, keyword=keyword, entry_ids=entry_ids)
    rows = (await db.execute(stmt)).all()
    result = []
    for entry, store_id in rows:
        rate = valid_rate(entry.currency, entry.exchange_rate_to_cny)
        net, expense = settlement_amounts(entry.net_product_sales or ZERO,
            entry.settlement_total or ZERO, entry.transaction_type)
        result.append(FinancialEvent(
            entry.id, store_id, entry.settlement_date or entry.order_created_at or entry.imported_at,
            net * rate if rate is not None else None,
            expense * rate if rate is not None else None,
            entry.settlement_total or ZERO, entry.currency or "",
            entry.product_id, entry.platform_sku_id or "",
            entry.settlement_date is None and entry.order_created_at is None,
            entry.transaction_type or "结算调整", entry.order_id or "",
            net, expense, rate, entry.exchange_rate_date, entry.exchange_rate_source, entry.imported_at,
            product_name=entry.product_name or "", quantity=entry.quantity,
            source_type=("manual_settlement" if (entry.raw_data or {}).get("_manual_settlement") else "import"),
        ))
    return result


def derive_manual_periods(reports, report_month, keys, monthly_defaults=None):
    """月记录的 current 是事实；旧比较列仅作缺少月事实时的兼容回退。"""
    monthly_defaults = monthly_defaults or {}
    by_month = {report.report_month: report for report in reports}
    current_report = by_month.get(report_month)
    current = getattr(current_report, "manual_values", {}) or {}
    metadata = getattr(current_report, "report_metadata", {}) or {}
    monthly_mode = metadata.get("manual_period_mode") == "monthly"
    previous_month = (report_month - timedelta(days=1)).replace(day=1)
    result = {}
    for key in keys:
        cell = dict(current.get(key, {}))
        prior = by_month.get(previous_month)
        if monthly_mode or cell.get("previous") in (None, ""):
            if prior is not None:
                cell["previous"] = (prior.manual_values or {}).get(key, {}).get("current")
            elif monthly_mode:
                cell["previous"] = None
        values = []
        overridden = False
        for month in range(1, report_month.month + 1):
            day = date(report_month.year, month, 1)
            row = by_month.get(day)
            value = ((row.manual_values or {}).get(key, {}).get("current") if row else None)
            default = monthly_defaults.get(key, {}).get(day.isoformat(), ZERO)
            if value is not None and value != "":
                values.append(Decimal(str(value)))
                overridden = True
            else:
                values.append(Decimal(str(default)))
        # all模式的显式比较金额保持兼容；current页面保存后统一按月重算。
        if monthly_mode or cell.get("ytd") in (None, ""):
            cell["ytd"] = sum(values, ZERO) if overridden else None
        result[key] = cell
    return result


async def manual_period_values(db, store_id, report_month, keys, monthly_defaults=None):
    start = min(date(report_month.year, 1, 1),
                (report_month - timedelta(days=1)).replace(day=1))
    reports = (await db.execute(select(StoreProfitLossReport).where(
        StoreProfitLossReport.store_id == store_id,
        StoreProfitLossReport.report_month >= start,
        StoreProfitLossReport.report_month <= report_month,
    ))).scalars().all()
    from services.finance_config import financial_controls_enabled
    if financial_controls_enabled():
        from services.financial_controls import ledger_manual_periods
        return await ledger_manual_periods(db, store_id, report_month, keys, reports, monthly_defaults)
    return derive_manual_periods(reports, report_month, keys, monthly_defaults)
