import unittest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from routers.reports import (
    _build_store_profit_loss_values,
    _month_periods,
    _store_profit_loss_auto_values,
)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _AutoValuesDb:
    def __init__(self, sales, cost_rows):
        self.results = [_Result(sales), _Result(cost_rows)]

    async def execute(self, _statement):
        return self.results.pop(0)

    async def scalar(self, _statement):
        return 2


class StoreProfitLossTests(unittest.IsolatedAsyncioTestCase):
    def test_month_periods_include_previous_month_and_ytd(self):
        periods = _month_periods(date(2026, 8, 1))
        self.assertEqual(periods["current"], (date(2026, 8, 1), date(2026, 9, 1)))
        self.assertEqual(periods["previous"], (date(2026, 7, 1), date(2026, 8, 1)))
        self.assertEqual(periods["ytd"], (date(2026, 1, 1), date(2026, 9, 1)))

    async def test_auto_values_use_sales_fifo_costs_and_period_impairment(self):
        current_sale = SimpleNamespace(
            id=1,
            sold_at=datetime(2026, 8, 8),
            selling_price=Decimal("50"),
            quantity=2,
            platform_fee=Decimal("0"),
        )
        previous_sale = SimpleNamespace(
            id=2,
            sold_at=datetime(2026, 7, 18),
            selling_price=Decimal("50"),
            quantity=1,
            platform_fee=Decimal("0"),
        )
        current_batch = SimpleNamespace(
            quantity=5,
            purchase_price=Decimal("15"),
            shipping_cost=Decimal("10"),
            last_mile_cost=Decimal("5"),
            other_cost=Decimal("5"),
        )
        previous_batch = SimpleNamespace(
            quantity=3,
            purchase_price=Decimal("12"),
            shipping_cost=Decimal("6"),
            last_mile_cost=Decimal("3"),
            other_cost=Decimal("3"),
        )
        db = _AutoValuesDb(
            [current_sale, previous_sale],
            [
                (SimpleNamespace(quantity=2), current_batch, current_sale.sold_at),
                (SimpleNamespace(quantity=1), previous_batch, previous_sale.sold_at),
            ],
        )

        async def import_context(_db, _sales):
            return ({
                1: {
                    "exchange_rate_to_cny": Decimal("1"),
                    "source_net_product_sales": Decimal("100"),
                    "source_other_expense": Decimal("20"),
                },
                2: {
                    "exchange_rate_to_cny": Decimal("1"),
                    "source_net_product_sales": Decimal("50"),
                    "source_other_expense": Decimal("5"),
                },
            }, {})

        impairment_total = AsyncMock(side_effect=[
            Decimal("40"), Decimal("30"), Decimal("25"), Decimal("10"),
        ])
        with (
            patch("routers.sales._sale_import_context", new=AsyncMock(side_effect=import_context)),
            patch(
                "routers.reports._store_inventory_impairment_total",
                new=impairment_total,
            ),
            patch(
                "routers.reports.office_space_totals_by_application_date",
                new=AsyncMock(return_value={
                    date(2026, 8, 8): Decimal("20"),
                    date(2026, 7, 18): Decimal("10"),
                }),
            ),
        ):
            values = await _store_profit_loss_auto_values(db, 7, date(2026, 8, 1))

        self.assertEqual(values["product_sales_revenue"], {
            "current": Decimal("100"),
            "previous": Decimal("50"),
            "ytd": Decimal("150"),
        })
        self.assertEqual(values["product_purchase_cost"], {
            "current": Decimal("30"),
            "previous": Decimal("12"),
            "ytd": Decimal("42"),
        })
        self.assertEqual(values["head_logistics_cost"], {
            "current": Decimal("6"),
            "previous": Decimal("3"),
            "ytd": Decimal("9"),
        })
        self.assertEqual(values["other_direct_cost"], {
            "current": Decimal("20"),
            "previous": Decimal("5"),
            "ytd": Decimal("25"),
        })
        self.assertEqual(values["inventory_impairment"], {
            "current": Decimal("10"),
            "previous": Decimal("5"),
            "ytd": Decimal("30"),
        })
        self.assertEqual(values["rent_utilities"], {
            "current": Decimal("10"),
            "previous": Decimal("5"),
            "ytd": Decimal("15"),
        })
        self.assertEqual(
            [call.args[2] for call in impairment_total.await_args_list],
            [
                datetime(2026, 9, 1),
                datetime(2026, 8, 1),
                datetime(2026, 7, 1),
                datetime(2026, 1, 1),
            ],
        )

    def test_template_formulas_use_manual_and_generated_cells(self):
        auto_values = {
            "product_sales_revenue": {
                "current": Decimal("100"), "previous": Decimal("40"), "ytd": Decimal("300"),
            },
            "product_purchase_cost": {
                "current": Decimal("30"), "previous": Decimal("10"), "ytd": Decimal("100"),
            },
            "head_logistics_cost": {
                "current": Decimal("6"), "previous": Decimal("2"), "ytd": Decimal("18"),
            },
            "other_direct_cost": {
                "current": Decimal("4"), "previous": Decimal("1"), "ytd": Decimal("12"),
            },
            "inventory_impairment": {
                "current": Decimal("3"), "previous": Decimal("1"), "ytd": Decimal("8"),
            },
        }
        manual_values = {
            "shipping_revenue": {"current": "5", "previous": "1", "ytd": "10"},
            "other_revenue": {"current": "2", "previous": "0", "ytd": "3"},
            "customs_import_tax": {"current": "5", "previous": "2", "ytd": "15"},
            "local_logistics_cost": {"current": "6", "previous": "3", "ytd": "18"},
            "fulfillment_cost": {"current": "4", "previous": "2", "ytd": "12"},
            "tax_surcharge": {"current": "1", "previous": "1", "ytd": "3"},
            "salary": {"current": "10", "previous": "2", "ytd": "25"},
            "advertising": {"current": "2", "previous": "1", "ytd": "5"},
            "platform_subscription": {"current": "3", "previous": "1", "ytd": "4"},
            "warehousing": {"current": "4", "previous": "1", "ytd": "6"},
            "delivery": {"current": "5", "previous": "2", "ytd": "8"},
            "platform_fines": {"current": "6", "previous": "0", "ytd": "7"},
            "shared_admin": {"current": "9", "previous": "3", "ytd": "15"},
            "research_development": {"current": "2", "previous": "1", "ytd": "3"},
            "finance_expenses": {"current": "3", "previous": "1", "ytd": "4"},
            "credit_impairment_loss": {"current": "4", "previous": "2", "ytd": "6"},
            "non_operating_income": {"current": "10", "previous": "0", "ytd": "12"},
            "non_operating_expense": {"current": "2", "previous": "0", "ytd": "4"},
            "income_tax_expense": {"current": "5", "previous": "0", "ytd": "6"},
        }

        values = _build_store_profit_loss_values(auto_values, manual_values)

        self.assertEqual(values["operating_revenue"], {
            "current": Decimal("107"), "previous": Decimal("41"), "ytd": Decimal("313"),
        })
        self.assertEqual(values["operating_cost"], {
            "current": Decimal("55"), "previous": Decimal("20"), "ytd": Decimal("175"),
        })
        self.assertEqual(values["gross_profit"], {
            "current": Decimal("52"), "previous": Decimal("21"), "ytd": Decimal("138"),
        })
        self.assertEqual(values["gross_margin"]["current"], Decimal("52") / Decimal("107"))
        self.assertEqual(values["selling_expenses"], {
            "current": Decimal("30"), "previous": Decimal("7"), "ytd": Decimal("55"),
        })
        self.assertEqual(values["operating_profit"], {
            "current": Decimal("0"), "previous": Decimal("5"), "ytd": Decimal("44"),
        })
        self.assertEqual(values["net_profit"], {
            "current": Decimal("3"), "previous": Decimal("5"), "ytd": Decimal("46"),
        })

    def test_gross_margin_is_empty_when_revenue_is_zero(self):
        values = _build_store_profit_loss_values({}, {})
        self.assertIsNone(values["gross_margin"]["current"])


if __name__ == "__main__":
    unittest.main()
