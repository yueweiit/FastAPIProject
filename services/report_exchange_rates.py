"""损益按店铺/月优先读取手动汇率，否则取自动月末汇率；不重写销售逐日汇率。"""
import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, or_, select

from models import Sale, SalesImportBatch, SettlementEntry, SettlementEntryAllocation, StoreProfitLossReport, User
from services.exchange_rates import ExchangeRateError, get_cny_rates
from services.financial_facts import settlement_accounting_date_expression
from services.sales_classification import inventory_sales_condition


def report_today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def month_end(day):
    next_month = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return next_month - timedelta(days=1)


def rate_key(currency, day):
    return str(currency or "CNY").strip().upper(), day.replace(day=1)


def store_rate_key(store_id, currency, day):
    return store_id, *rate_key(currency, day)


async def monthly_store_report_rates(db, requirements):
    by_store = {}
    for store_id, currency, month in requirements:
        by_store.setdefault(store_id, set()).add(rate_key(currency, month))
    resolved = {}
    # A session cannot run SQL concurrently; automatic quotes are shared by the request cache.
    for store_id, keys in by_store.items():
        rates = await monthly_report_rates(db, keys, store_id=store_id)
        resolved.update({(store_id, *key): value for key, value in rates.items()})
    return resolved


FX_OVERRIDE_KEY = "report_fx_overrides"


def manual_rate_text(value):
    """Keep the exact rate, without trailing decimal zeros or exponent notation."""
    if value is None:
        return None
    try:
        text = format(Decimal(str(value)), "f")
    except (InvalidOperation, ValueError, TypeError):
        return value  # Corrupt legacy values remain invalid; do not invent a rate.
    return text.rstrip("0").rstrip(".") if "." in text else text


def fx_setting(currency, saved=None):
    saved = saved if isinstance(saved, dict) else {}
    return {"currency": currency, "mode": saved.get("mode", "auto"),
            "manual_rate": manual_rate_text(saved.get("manual_rate")), "revision": saved.get("revision", 0),
            "note": saved.get("note", ""), "updated_at": saved.get("updated_at"),
            "updated_by": saved.get("updated_by"), "updated_by_user_id": saved.get("updated_by_user_id")}


async def load_fx_settings(db, store_id, months):
    """Cache saved settings per store/month, never share an effective manual rate across stores."""
    info = getattr(db, "info", None)
    cache = info.setdefault("report_fx_settings", {}) if isinstance(info, dict) else {}
    missing = {month for month in months if (store_id, month) not in cache}
    if missing:
        rows = (await db.execute(select(StoreProfitLossReport).where(
            StoreProfitLossReport.store_id == store_id,
            StoreProfitLossReport.report_month.in_(missing)))).scalars().all()
        for month in missing:
            cache[(store_id, month)] = {}
        for row in rows:
            settings = (row.report_metadata or {}).get(FX_OVERRIDE_KEY, {})
            cache[(store_id, row.report_month)] = settings if isinstance(settings, dict) else {}
    return {month: cache[(store_id, month)] for month in months}


def invalidate_report_rates(db):
    info = getattr(db, "info", None)
    if isinstance(info, dict):
        info.pop("report_fx_settings", None)
        info.pop("monthly_report_rates", None)


async def _store_monthly_rates(db, store_id, requirements):
    keys = {rate_key(currency, day) for currency, day in requirements}
    settings = await load_fx_settings(db, store_id, {month for _, month in keys})
    manual, automatic = {}, set()
    for currency, month in keys:
        setting = fx_setting(currency, settings[month].get(currency))
        if currency == "CNY" or setting["mode"] != "manual":
            automatic.add((currency, month))
            continue
        try:
            rate = Decimal(str(setting["manual_rate"]))
            if not rate.is_finite() or rate <= 0:
                rate = None
        except (ValueError, TypeError, InvalidOperation):
            rate = None
        manual[(currency, month)] = {
            **setting, "store_id": store_id, "report_month": month.isoformat()[:7],
            "requested_date": month_end(month), "rate_date": None, "rate_to_cny": rate,
            "source": "manual", "status": "available" if rate is not None else "missing",
            "is_provisional": month >= report_today().replace(day=1),
        }
    rates = await monthly_report_rates(db, automatic)
    for (currency, month), value in rates.items():
        manual[(currency, month)] = {**value, **fx_setting(currency, settings[month].get(currency)),
                                    "mode": "auto", "store_id": store_id, "is_provisional": False}
    return {key: manual[key] for key in sorted(keys)}


async def monthly_report_rates(db, requirements, *, store_id=None):
    """每请求共用币种/月缓存；失败、未到月末均留空，不回退到订单日汇率。"""
    if store_id is not None:
        return await _store_monthly_rates(db, store_id, requirements)
    info = getattr(db, "info", None)
    cache = info.setdefault("monthly_report_rates", {}) if isinstance(info, dict) else {}
    keys = {rate_key(currency, day) for currency, day in requirements}
    missing = keys - cache.keys()
    by_currency = {}
    for currency, month in missing:
        requested = month_end(month)
        value = {"currency": currency, "report_month": month.isoformat()[:7],
                 "requested_date": requested, "rate_date": None, "rate_to_cny": None,
                 "source": None, "status": "missing"}
        cache[(currency, month)] = value
        if currency == "CNY":
            value.update(rate_to_cny=Decimal(1), rate_date=requested, source="CNY", status="available")
        elif requested > report_today():
            value["status"] = "month_not_closed"
        else:
            by_currency.setdefault(currency, set()).add(requested)

    async def resolve(currency, dates):
        try:
            result = await get_cny_rates({(currency, day) for day in dates})
        except ExchangeRateError:
            return
        for day in dates:
            item = result.get((currency, day))
            if not item:
                continue
            try:
                rate = Decimal(str(item["rate"]))
                actual = item["rate_date"]
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
            if type(actual) is not date or not rate.is_finite() or rate <= 0 or actual > day:
                continue
            cache[rate_key(currency, day)].update(rate_to_cny=rate, rate_date=actual,
                source=item.get("source"), status="available")
    await asyncio.gather(*(resolve(currency, dates) for currency, dates in by_currency.items()))
    return {key: cache[key] for key in sorted(keys)}


def convert_report_amount(amount, rate):
    amount = Decimal(str(amount))
    if not amount:
        return Decimal(0)
    return None if rate["rate_to_cny"] is None else amount * rate["rate_to_cny"]


def rate_evidence_text(item):
    """Chinese financial exports share one label for manual/provisional rate evidence."""
    source = item.get("source") or item["status"]
    evidence = str(item.get("rate_date") or "—")
    if item.get("mode") == "manual":
        source = "手动（暂估）" if item.get("is_provisional") else "手动"
        evidence = f"{item.get('updated_at') or '—'} · {item.get('updated_by') or '—'}"
        if item.get("note"):
            evidence += f" · {item['note']}"
    return f"{item['report_month']} {item['currency']} / CNY：{item['rate_to_cny']}；{source}；{evidence}"


def fulfillment_orders(sales, contexts, store_ids_by_sale=None):
    """兼容无结算来源的手工销售及旧三列 CSV；订单详情由来源查询统计。"""
    result = {}
    from services.sales_classification import is_opening_deduction
    for sale in sales:
        if sale.quantity <= 0 or is_opening_deduction(sale):
            continue
        context = contexts.get(sale.id, {})
        if context.get("is_fulfillment_order") is False:
            continue
        # 旧三列CSV的来源order_id是SKU占位，按原保存编号兼容，不能将整月同SKU合并。
        order = None if context.get("source_file_type") == "legacy_csv" else (
            context.get("source_fulfillment_order_id") or context.get("source_order_no"))
        order = order or getattr(sale, "order_no", "") or f"sale:{sale.id}"
        month = sale.sold_at.date().replace(day=1)
        store_id = (store_ids_by_sale or {}).get(sale.id, getattr(sale, "store_id", None))
        result.setdefault(month, set()).add((store_id, order))
    return result


async def load_fulfillment_orders(db, store_ids, start, end, sales=(), contexts=None, store_ids_by_sale=None):
    """代发与扣库存资格分开：只按来源交易类型为订单的原始订单号去重。"""
    if not store_ids:
        return {}
    contexts = contexts or {}
    fallback = [sale for sale in sales if not contexts.get(sale.id)
                or contexts[sale.id].get("source_file_type") == "legacy_csv"]
    result = fulfillment_orders(fallback, contexts, store_ids_by_sale)
    occurred = settlement_accounting_date_expression()
    owner = func.coalesce(SettlementEntry.store_id, SalesImportBatch.store_id, User.store_id)
    opening_direct = exists(select(Sale.id).where(Sale.id == SettlementEntry.sale_id,
        ~inventory_sales_condition()).correlate(SettlementEntry))
    opening_component = exists(select(SettlementEntryAllocation.id)
        .join(Sale, Sale.id == SettlementEntryAllocation.sale_id)
        .where(SettlementEntryAllocation.settlement_entry_id == SettlementEntry.id,
               ~inventory_sales_condition()).correlate(SettlementEntry))
    stmt = (select(owner, SettlementEntry.order_id, occurred)
        .outerjoin(SalesImportBatch, SalesImportBatch.id == SettlementEntry.import_batch_id)
        .outerjoin(User, User.id == SalesImportBatch.user_id)
        .where(owner.in_(store_ids), SettlementEntry.transaction_type == "订单",
               func.trim(SettlementEntry.order_id) != "",
               or_(SalesImportBatch.file_type.is_(None), SalesImportBatch.file_type != "legacy_csv"),
               or_(SalesImportBatch.status.is_(None), SalesImportBatch.status == "completed"),
               ~opening_direct, ~opening_component))
    if start is not None:
        stmt = stmt.where(occurred >= datetime.combine(start, time.min))
    if end is not None:
        stmt = stmt.where(occurred < datetime.combine(end, time.min))
    for store_id, order, created_at in (await db.execute(stmt.distinct())).all():
        if created_at is not None:
            result.setdefault(created_at.date().replace(day=1), set()).add((store_id, order.strip()))
    return result


def rate_warnings(rates):
    warnings = []
    for item in rates.values():
        if item["rate_to_cny"] is not None:
            continue
        reason = "月份尚未结束，月末汇率尚未形成" if item["status"] == "month_not_closed" else "月末或之前没有取得有效汇率"
        warnings.append(f"{item['report_month']} {item['currency']}：{reason}，相关人民币金额待核对。")
    return warnings
