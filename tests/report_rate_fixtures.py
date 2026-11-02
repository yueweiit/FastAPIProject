"""报表回归的离线月末汇率；禁止测试访问真实汇率服务。"""
from datetime import timedelta
from decimal import Decimal


async def fixed_report_rates(requirements):
    result = {}
    for currency, requested in requirements:
        actual = requested
        while actual.weekday() > 4:
            actual -= timedelta(days=1)
        result[(currency, requested)] = {"rate": {"MXN": Decimal("0.5"), "USD": Decimal("2")}.get(currency, Decimal(1)),
                                         "rate_date": actual, "source": "test-month-end"}
    return result
