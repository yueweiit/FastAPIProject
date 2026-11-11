"""损益使用核算月末汇率；仅读取外部数据，不重写销售保存的逐日汇率。"""
import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from services.exchange_rates import ExchangeRateError, get_cny_rates


def report_today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def month_end(day):
    next_month = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return next_month - timedelta(days=1)


def rate_key(currency, day):
    return str(currency or "CNY").strip().upper(), day.replace(day=1)


async def monthly_report_rates(db, requirements):
    """每请求共用币种/月缓存；失败、未到月末均留空，不回退到订单日汇率。"""
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


def fulfillment_orders(sales, contexts, store_ids_by_sale=None):
    """按店铺、原始订单号及销售月去重，不按SKU或组合组件重复收取。"""
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


def rate_warnings(rates):
    warnings = []
    for item in rates.values():
        if item["rate_to_cny"] is not None:
            continue
        reason = "月份尚未结束，月末汇率尚未形成" if item["status"] == "month_not_closed" else "月末或之前没有取得有效汇率"
        warnings.append(f"{item['report_month']} {item['currency']}：{reason}，相关人民币金额待核对。")
    return warnings
