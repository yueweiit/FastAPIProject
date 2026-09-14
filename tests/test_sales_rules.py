import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from models import Sale, SaleCostDetail
from routers.sales import (
    _allocate_available_stock,
    _build_sale_response,
    _component_allocation_shares,
    _select_standalone_price,
    _standalone_price_from_row,
)
from services.exchange_rates import _resolve_rate_payload
from services.fifo import fifo_sell


class StandalonePriceTests(unittest.TestCase):
    def test_original_price_is_preferred_and_converted_to_unit_price(self):
        row = {"商品原价小计": "180", "净商品销售额": "150"}

        self.assertEqual(_standalone_price_from_row(row, 2), Decimal("90"))

    def test_net_sales_is_used_when_original_price_is_missing(self):
        row = {"商品原价小计": "0", "净商品销售额": "150"}

        self.assertEqual(_standalone_price_from_row(row, 2), Decimal("75"))

    def test_nearest_price_prefers_past_date_when_distance_is_tied(self):
        observations = [
            {"price": Decimal("80"), "sold_at": datetime(2026, 8, 9), "row_number": 2},
            {"price": Decimal("90"), "sold_at": datetime(2026, 8, 11), "row_number": 3},
        ]

        selected = _select_standalone_price(observations, datetime(2026, 8, 10))

        self.assertEqual(selected, Decimal("80"))

    def test_most_common_price_is_used_for_same_nearest_date(self):
        observations = [
            {"price": Decimal("85"), "sold_at": datetime(2026, 8, 10), "row_number": 2},
            {"price": Decimal("95"), "sold_at": datetime(2026, 8, 10), "row_number": 3},
            {"price": Decimal("95"), "sold_at": datetime(2026, 8, 10), "row_number": 4},
        ]

        selected = _select_standalone_price(observations, datetime(2026, 8, 10))

        self.assertEqual(selected, Decimal("95"))

    def test_bundle_shares_use_standalone_price_and_multiplier(self):
        components = [
            {"multiplier": 3, "price_allocation_weight": Decimal("300")},
            {"multiplier": 1, "price_allocation_weight": Decimal("50")},
        ]

        shares = _component_allocation_shares(components, item_quantity=2)

        self.assertEqual(shares, [Decimal("6") / Decimal("7"), Decimal("1") / Decimal("7")])


class StockAllocationTests(unittest.TestCase):
    def test_short_row_is_pending_without_partial_deduction(self):
        product = SimpleNamespace(id=14, sku="CW000014")
        candidates = [
            {
                "is_inventory_sale": True,
                "quantity": quantity,
                "components": [{"product": product, "multiplier": 1}],
                "mapping_error": None,
            }
            for quantity in (2, 2, 2, 1)
        ]

        _allocate_available_stock(candidates, {product.id: 5})

        self.assertEqual(
            [item["components"][0].get("sale_quantity", 0) for item in candidates],
            [2, 2, 0, 1],
        )
        self.assertIsNone(candidates[0]["mapping_error"])
        self.assertIn("本行需要 2 件，当前可用 1 件", candidates[2]["mapping_error"])
        self.assertFalse(candidates[2]["is_inventory_sale"])
        self.assertIsNone(candidates[3]["mapping_error"])
        self.assertTrue(candidates[3]["is_inventory_sale"])

    def test_bundle_row_is_not_deducted_when_any_component_is_short(self):
        product_a = SimpleNamespace(id=1, sku="A")
        product_b = SimpleNamespace(id=2, sku="B")
        candidates = [{
            "is_inventory_sale": True,
            "quantity": 1,
            "components": [
                {"product": product_a, "multiplier": 1},
                {"product": product_b, "multiplier": 1},
            ],
            "mapping_error": None,
        }]

        _allocate_available_stock(candidates, {product_a.id: 2, product_b.id: 0})

        self.assertFalse(candidates[0]["is_inventory_sale"])
        self.assertNotIn("sale_quantity", candidates[0]["components"][0])
        self.assertNotIn("sale_quantity", candidates[0]["components"][1])
        self.assertIn("商品 B 本行需要 1 件，当前可用 0 件", candidates[0]["mapping_error"])


class ExchangeRateTests(unittest.TestCase):
    def test_weekend_uses_latest_available_business_day(self):
        payload = {
            "rates": {
                "2026-08-28": {"CNY": 0.39655},
                "2026-08-31": {"CNY": 0.39509},
            }
        }

        result = _resolve_rate_payload(
            "MXN",
            {datetime(2026, 8, 30).date(), datetime(2026, 8, 31).date()},
            payload,
        )

        self.assertEqual(result[("MXN", datetime(2026, 8, 30).date())]["rate"], Decimal("0.39655"))
        self.assertEqual(result[("MXN", datetime(2026, 8, 30).date())]["rate_date"], datetime(2026, 8, 28).date())
        self.assertEqual(result[("MXN", datetime(2026, 8, 31).date())]["rate"], Decimal("0.39509"))

    def test_sale_response_exposes_all_cny_amounts(self):
        sale = Sale(
            id=1,
            product_id=1,
            order_no="ORDER-1",
            quantity=3,
            selling_price=Decimal("20"),
            total_cost=Decimal("10"),
            platform_fee=Decimal("2"),
            profit=Decimal("48"),
            sold_at=datetime(2026, 8, 31),
            created_at=datetime(2026, 9, 1),
        )

        response = _build_sale_response(
            sale,
            [],
            currency="MXN",
            exchange_rate_to_cny=Decimal("0.4"),
            exchange_rate_date=datetime(2026, 8, 31).date(),
            exchange_rate_source="test",
        )

        self.assertEqual(response.selling_price_cny, Decimal("8.0"))
        self.assertEqual(response.sales_revenue_cny, Decimal("24.0"))
        self.assertEqual(response.platform_fee_cny, Decimal("0.8"))
        self.assertEqual(response.profit_cny, Decimal("13.2"))


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _FakeScalars(self._values)


class _FakeDb:
    def __init__(self, batches):
        self.batches = batches
        self.added = []

    async def execute(self, _statement):
        return _FakeResult(self.batches)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if isinstance(value, Sale) and value.id is None:
                value.id = 1


class FifoCostTests(unittest.IsolatedAsyncioTestCase):
    async def test_oldest_batch_is_consumed_first_and_cost_is_weighted(self):
        old_batch = SimpleNamespace(
            id=1,
            batch_no="OLD",
            remaining_quantity=2,
            unit_cost=Decimal("10"),
        )
        new_batch = SimpleNamespace(
            id=2,
            batch_no="NEW",
            remaining_quantity=5,
            unit_cost=Decimal("14"),
        )
        db = _FakeDb([old_batch, new_batch])

        sale, details = await fifo_sell(
            db=db,
            product_id=1,
            order_no="ORDER-1",
            quantity=4,
            selling_price=Decimal("20"),
            platform_fee=Decimal("4"),
            sold_at=datetime(2026, 8, 10),
            commit=False,
        )

        self.assertEqual([(d["batch_no"], d["quantity"]) for d in details], [("OLD", 2), ("NEW", 2)])
        self.assertEqual(old_batch.remaining_quantity, 0)
        self.assertEqual(new_batch.remaining_quantity, 3)
        self.assertEqual(sale.total_cost, Decimal("48"))
        self.assertEqual(sale.total_cost / sale.quantity, Decimal("12"))
        self.assertEqual(sale.profit, Decimal("28"))
        self.assertEqual(sum(isinstance(value, SaleCostDetail) for value in db.added), 2)


if __name__ == "__main__":
    unittest.main()
