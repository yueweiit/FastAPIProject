"""Historical currency conversion for imported sales."""

import asyncio
import http.client
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


FX_SOURCE = "Frankfurter/ECB"
FX_API_URL = "https://api.frankfurter.dev/v1"
BAIDU_FX_API_URL = "https://finance.pae.baidu.com/async"
BAIDU_FX_SOURCE = "Baidu/DailyClose"


class ExchangeRateError(Exception):
    pass


def _fetch_rate_range(currency: str, start_date: date, end_date: date) -> dict:
    url = (
        f"{FX_API_URL}/{start_date.isoformat()}..{end_date.isoformat()}"
        f"?base={currency}&symbols=CNY"
    )
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "FIFO-Inventory/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ExchangeRateError(f"无法获取 {currency} 历史汇率: {exc}") from exc


def _resolve_rate_payload(
    currency: str,
    requested_dates: set[date],
    payload: dict,
    *, source: str = FX_SOURCE,
) -> dict[tuple[str, date], dict]:
    available_rates = []
    for rate_date_text, values in (payload.get("rates") or {}).items():
        try:
            rate_date = date.fromisoformat(rate_date_text)
            rate = Decimal(str(values["CNY"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if rate.is_finite() and rate > 0:
            available_rates.append((rate_date, rate))
    available_rates.sort()

    resolved = {}
    for requested_date in requested_dates:
        eligible = [item for item in available_rates if item[0] <= requested_date]
        if not eligible:
            raise ExchangeRateError(
                f"{currency} 在 {requested_date.isoformat()} 及之前没有可用的人民币汇率"
            )
        rate_date, rate = eligible[-1]
        resolved[(currency, requested_date)] = {
            "rate": rate,
            "rate_date": rate_date,
            "source": source,
        }
    return resolved


def _fetch_baidu_rates(currency: str) -> dict:
    """Read the same historical daily series as the public FX chart."""
    url = BAIDU_FX_API_URL + "?" + urlencode({
        "srcid": "50494", "group": "huilv_kline_day", "tab_id": "6", "query": currency + "CNY",
    })
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "FIFO-Inventory/1.0",
        "Referer": f"https://finance.baidu.com/foreign/global-{currency}CNY",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    # Body reads can fail after urlopen succeeds (for example IncompleteRead).
    except (OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError) as exc:
        raise ExchangeRateError(f"无法获取 {currency} 百度历史收盘汇率") from exc


def _resolve_baidu_rates(currency: str, requested_dates: set[date], payload: dict) -> dict:
    """Use named close/time columns, never an intraday quote or inverse pair."""
    pair = currency + "CNY"
    try:
        if str(payload["status"]) != "0" or not isinstance(payload["data"], list):
            raise ValueError("invalid response")
        matches = [item for item in payload["data"] if isinstance(item, dict) and item.get("code") == pair]
        if len(matches) != 1:
            raise ValueError("currency pair missing or ambiguous")
        series = matches[0]["tab"]["newMarketData"]
        keys, records = series["keys"], series["marketData"]
        if not isinstance(keys, list) or not isinstance(records, str) or keys.count("time") != 1 or keys.count("close") != 1:
            raise ValueError("missing daily columns")
        day_index, close_index = keys.index("time"), keys.index("close")
    except (KeyError, TypeError, ValueError) as exc:
        raise ExchangeRateError(f"{currency} 百度历史收盘汇率响应无效") from exc
    available = {}
    start, end = min(requested_dates) - timedelta(days=14), max(requested_dates)
    for record in records.split(";"):
        values = record.split(",")
        try:
            day = date.fromisoformat(values[day_index])
            rate = Decimal(values[close_index])
        except (IndexError, ValueError, InvalidOperation):
            continue
        if not (start <= day <= end) or not rate.is_finite() or rate <= 0:
            continue
        old = available.get(day.isoformat())
        if old and old["CNY"] != rate:
            raise ExchangeRateError(f"{currency} 百度历史收盘汇率存在同日冲突")
        available[day.isoformat()] = {"CNY": rate}
    resolved = {}
    for day in requested_dates:
        # Bound each date separately; do not use stale data from another month.
        rates = {key: value for key, value in available.items()
                 if day - timedelta(days=14) <= date.fromisoformat(key) <= day}
        resolved.update(_resolve_rate_payload(currency, {day}, {"rates": rates}, source=BAIDU_FX_SOURCE))
    return resolved


async def get_cny_rates(requirements: set[tuple[str, date]]) -> dict[tuple[str, date], dict]:
    """Resolve one CNY rate for every original-currency/date pair."""
    normalized: set[tuple[str, date]] = set()
    resolved: dict[tuple[str, date], dict] = {}
    for currency, requested_date in requirements:
        currency = currency.upper().strip()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ExchangeRateError(f"无法识别货币代码: {currency or '-'}")
        if currency == "CNY":
            resolved[(currency, requested_date)] = {
                "rate": Decimal("1"),
                "rate_date": requested_date,
                "source": "CNY",
            }
        else:
            normalized.add((currency, requested_date))

    by_currency: dict[str, set[date]] = {}
    for currency, requested_date in normalized:
        by_currency.setdefault(currency, set()).add(requested_date)

    async def fetch_currency(currency: str, requested_dates: set[date]):
        if provider == "baidu":
            payload = await asyncio.to_thread(_fetch_baidu_rates, currency)
            return _resolve_baidu_rates(currency, requested_dates, payload)
        start_date = min(requested_dates) - timedelta(days=14)
        end_date = max(requested_dates)
        payload = await asyncio.to_thread(
            _fetch_rate_range, currency, start_date, end_date
        )
        return _resolve_rate_payload(currency, requested_dates, payload)

    if by_currency:
        provider = os.getenv("FIFO_FX_PROVIDER", "frankfurter").strip().lower()
        if provider not in {"frankfurter", "baidu"}:
            raise ExchangeRateError("FIFO_FX_PROVIDER 必须为 frankfurter 或 baidu")
        currency_results = await asyncio.gather(
            *(fetch_currency(currency, dates) for currency, dates in by_currency.items())
        )
        for currency_result in currency_results:
            resolved.update(currency_result)
    return resolved
