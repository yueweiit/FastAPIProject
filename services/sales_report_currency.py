"""销售只读核算与财务报表共用店铺/月汇率；不修改导入记录或 FIFO。"""
from dataclasses import replace
from decimal import Decimal

from sqlalchemy import select

from models import User
from services.report_exchange_rates import (
    convert_report_amount, month_end, monthly_store_report_rates, store_rate_key,
)

SALES_RATE_POLICY = "store_month_report_v1"


async def sales_report_rates(db, requirements):
    keys = {store_rate_key(store_id, currency, day) for store_id, currency, day in requirements}
    rates = await monthly_store_report_rates(db, {key for key in keys if key[0] is not None and key[1] != "CNY"})
    for key in keys - rates.keys():
        store_id, currency, month = key
        cny = currency == "CNY"
        rates[key] = {"store_id": store_id, "currency": currency, "report_month": month.isoformat()[:7],
                      "rate_to_cny": Decimal(1) if cny else None,
                      "rate_date": month_end(month) if cny else None,
                      "source": "CNY" if cny else None, "mode": "auto", "is_provisional": False,
                      "status": "available" if cny else "store_missing"}
    return rates


def sale_rate_fields(rate):
    return {"exchange_rate_to_cny": rate["rate_to_cny"], "exchange_rate_date": rate["rate_date"],
            "exchange_rate_policy": SALES_RATE_POLICY, "exchange_rate_store_id": rate["store_id"],
            "exchange_rate_source": rate["source"], "exchange_rate_month": rate["report_month"],
            "exchange_rate_mode": rate.get("mode", "auto"), "exchange_rate_status": rate["status"],
            "exchange_rate_is_provisional": rate.get("is_provisional", False)}


async def sale_report_contexts(db, sales, contexts):
    # 与损益和销售权限相同：旧销售仅回退创建人的店铺，不用商品映射猜测。
    user_ids = {sale.user_id for sale in sales if sale.store_id is None and sale.user_id is not None}
    stores = dict((await db.execute(select(User.id, User.store_id).where(User.id.in_(user_ids)))).all()) if user_ids else {}
    keys = {sale.id: store_rate_key(sale.store_id if sale.store_id is not None else stores.get(sale.user_id),
                                   contexts.get(sale.id, {}).get("currency"), sale.sold_at.date()) for sale in sales}
    rates = await sales_report_rates(db, keys.values())
    return {sale.id: {**contexts.get(sale.id, {}), **sale_rate_fields(rates[keys[sale.id]])} for sale in sales}


async def report_financial_events(db, events):
    rates = await sales_report_rates(db, {(event.store_id, event.currency, event.occurred_at.date()) for event in events})
    result = []
    for event in events:
        rate = rates[store_rate_key(event.store_id, event.currency, event.occurred_at.date())]
        result.append(replace(event, rate=rate["rate_to_cny"], rate_date=rate["rate_date"], rate_source=rate["source"],
                              revenue=convert_report_amount(event.original_revenue, rate),
                              expense=convert_report_amount(event.original_expense, rate), report_rate=rate))
    return result
