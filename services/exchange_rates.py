"""Historical currency conversion for imported sales."""

import asyncio
import json
import re
import urllib.error
import urllib.request
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation


FX_SOURCE = "Frankfurter/ECB"
FX_API_URL = "https://api.frankfurter.dev/v1"


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
) -> dict[tuple[str, date], dict]:
    available_rates = []
    for rate_date_text, values in (payload.get("rates") or {}).items():
        try:
            rate_date = date.fromisoformat(rate_date_text)
            rate = Decimal(str(values["CNY"]))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if rate > 0:
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
            "source": FX_SOURCE,
        }
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
        start_date = min(requested_dates) - timedelta(days=14)
        end_date = max(requested_dates)
        payload = await asyncio.to_thread(
            _fetch_rate_range, currency, start_date, end_date
        )
        return _resolve_rate_payload(currency, requested_dates, payload)

    if by_currency:
        currency_results = await asyncio.gather(
            *(fetch_currency(currency, dates) for currency, dates in by_currency.items())
        )
        for currency_result in currency_results:
            resolved.update(currency_result)
    return resolved
