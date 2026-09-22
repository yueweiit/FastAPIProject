import io
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from openpyxl import Workbook

from models import Sale, SaleCostDetail
from routers.sales import (
    _allocate_available_stock,
    _build_sale_response,
    _component_allocation_shares,
    _group_pending_confirmations,
    _is_missing_sku_id,
    _pending_confirmation_reason,
    _monthly_sales_summary,
    _other_expense_from_row,
    _read_import_file,
    _resolve_platform_sku_components,
    _select_standalone_price,
    _standalone_price_from_row,
)
from services.exchange_rates import _resolve_rate_payload
from services.fifo import fifo_sell


class PendingConfirmationExportTests(unittest.TestCase):
    def test_logistics_compensation_placeholder_is_missing_sku(self):
        for value in (None, "", "/", "-", "N/A"):
            self.assertTrue(_is_missing_sku_id(value))
        self.assertFalse(_is_missing_sku_id("SKU-1"))

    def test_existing_logistics_compensation_is_reclassified(self):
        entry = SimpleNamespace(
            transaction_type="物流赔付",
            platform_sku_id="/",
            mapping_error="未配置平台 SKU ID 映射: /",
        )

        self.assertEqual(
            _pending_confirmation_reason(entry),
            "物流赔付（无 SKU ID，金额计入其他费用）",
        )

    def test_groups_by_sku_id_and_reason(self):
        entries = [
            SimpleNamespace(platform_sku_id="SKU-1", mapping_error="商品未匹配", quantity=2,
                            product_name="商品 A", sku_name="红色", source_file="a.xlsx",
                            transaction_type="订单", currency="MXN", net_product_sales=Decimal("10"),
                            settlement_total=Decimal("8"), store=SimpleNamespace(name="店铺 A")),
            SimpleNamespace(platform_sku_id="SKU-1", mapping_error="商品未匹配", quantity=3,
                            product_name="商品 A", sku_name="红色", source_file="b.xlsx",
                            transaction_type="订单", currency="MXN", net_product_sales=Decimal("20"),
                            settlement_total=Decimal("15"), store=SimpleNamespace(name="店铺 B")),
            SimpleNamespace(platform_sku_id="SKU-1", mapping_error="库存不足", quantity=4,
                            product_name="商品 A", sku_name="红色", source_file="a.xlsx",
                            transaction_type="订单", currency="MXN", net_product_sales=Decimal("4"),
                            settlement_total=Decimal("4"), store=SimpleNamespace(name="店铺 A")),
        ]

        rows = _group_pending_confirmations(entries)

        self.assertEqual(len(rows), 2)
        unmatched = next(row for row in rows if row["mapping_error"] == "商品未匹配")
        self.assertEqual(unmatched["quantity"], 5)
        self.assertEqual(unmatched["record_count"], 2)
        self.assertEqual(unmatched["other_expense"], Decimal("7"))
        self.assertEqual(unmatched["store_names"], "店铺 A、店铺 B")
        self.assertEqual(unmatched["source_files"], "a.xlsx、b.xlsx")


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


class PlatformSkuMappingTests(unittest.TestCase):
    def test_platform_sku_mapping_returns_configured_bundle_components(self):
        duck = SimpleNamespace(id=1, sku="DUCK", name="小黄鸭")
        elephant = SimpleNamespace(id=2, sku="ELEPHANT", name="小蓝象")
        mapping = SimpleNamespace(
            components=[
                SimpleNamespace(product=duck, quantity_per_sale=1),
                SimpleNamespace(product=elephant, quantity_per_sale=2),
            ]
        )

        components, error = _resolve_platform_sku_components(
            {"SKU ID": "1736993559553869688"},
            {"1736993559553869688": mapping},
        )

        self.assertIsNone(error)
        self.assertEqual(
            [(component["product"].sku, component["multiplier"]) for component in components],
            [("DUCK", 1), ("ELEPHANT", 2)],
        )

    def test_unmapped_platform_sku_does_not_fall_back_to_product_name(self):
        components, error = _resolve_platform_sku_components(
            {"SKU ID": "UNCONFIGURED", "产品名": "小黄鸭"}, {}
        )

        self.assertIsNone(components)
        self.assertEqual(error, "未配置平台 SKU ID 映射: UNCONFIGURED")


class ExchangeRateTests(unittest.TestCase):
    def test_other_expense_is_net_sales_minus_settlement_total(self):
        self.assertEqual(
            _other_expense_from_row(Decimal("144"), Decimal("98.82")),
            Decimal("45.18"),
        )

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

    def test_sale_response_uses_source_net_sales_and_other_expense(self):
        sale = Sale(
            id=1,
            product_id=1,
            order_no="ORDER-1",
            quantity=2,
            selling_price=Decimal("10"),
            total_cost=Decimal("10"),
            platform_fee=Decimal("1"),
            profit=Decimal("9"),
            sold_at=datetime(2026, 8, 31),
            created_at=datetime(2026, 9, 1),
        )

        response = _build_sale_response(
            sale,
            [],
            currency="MXN",
            exchange_rate_to_cny=Decimal("0.4"),
            source_net_product_sales=Decimal("144"),
            source_other_expense=Decimal("45.18"),
        )

        self.assertEqual(response.selling_price_cny, Decimal("28.8"))
        self.assertEqual(response.sales_revenue_cny, Decimal("57.6"))
        self.assertEqual(response.platform_fee_cny, Decimal("18.072"))
        self.assertEqual(response.profit_cny, Decimal("29.528"))


class MonthlySalesSummaryTests(unittest.TestCase):
    def test_summary_aggregates_all_sales_in_each_month(self):
        sales = [
            SimpleNamespace(
                id=1,
                sold_at=datetime(2026, 8, 10),
                quantity=2,
                selling_price=Decimal("10"),
                total_cost=Decimal("3"),
                platform_fee=Decimal("1"),
            ),
            SimpleNamespace(
                id=2,
                sold_at=datetime(2026, 8, 20),
                quantity=1,
                selling_price=Decimal("20"),
                total_cost=Decimal("5"),
                platform_fee=Decimal("2"),
            ),
            SimpleNamespace(
                id=3,
                sold_at=datetime(2026, 7, 31),
                quantity=4,
                selling_price=Decimal("8"),
                total_cost=Decimal("12"),
                platform_fee=Decimal("0"),
            ),
        ]

        result = _monthly_sales_summary(
            sales,
            {
                1: {"currency": "CNY", "exchange_rate_to_cny": Decimal("1")},
                2: {"currency": "USD", "exchange_rate_to_cny": Decimal("0.5")},
                3: {"currency": "CNY", "exchange_rate_to_cny": Decimal("1")},
            },
        )

        self.assertEqual([item.month for item in result], ["2026-08", "2026-07"])
        august = result[0]
        self.assertEqual(august.sales_count, 2)
        self.assertEqual(august.sold_quantity, 3)
        self.assertEqual(august.sales_revenue_cny, Decimal("30.0"))
        self.assertEqual(august.sales_cost_cny, Decimal("8"))
        self.assertEqual(august.platform_fee_cny, Decimal("2.0"))
        self.assertEqual(august.gross_profit_cny, Decimal("20.0"))

    def test_summary_uses_source_amounts_when_available(self):
        sale = SimpleNamespace(
            id=1,
            sold_at=datetime(2026, 8, 10),
            quantity=2,
            selling_price=Decimal("10"),
            total_cost=Decimal("10"),
            platform_fee=Decimal("1"),
        )

        result = _monthly_sales_summary(
            [sale],
            {
                1: {
                    "currency": "MXN",
                    "exchange_rate_to_cny": Decimal("0.4"),
                    "source_net_product_sales": Decimal("144"),
                    "source_other_expense": Decimal("45.18"),
                },
            },
        )

        self.assertEqual(result[0].sales_revenue_cny, Decimal("57.6"))
        self.assertEqual(result[0].platform_fee_cny, Decimal("18.072"))
        self.assertEqual(result[0].gross_profit_cny, Decimal("29.528"))


class ImportFormatTests(unittest.TestCase):
    def test_order_detail_sheet_is_selected_and_required_header_spacing_is_tolerated(self):
        workbook = Workbook()
        workbook.active.title = "说明"
        sheet = workbook.create_sheet("订单详情")
        sheet.append([
            "结算日期", "结算单ID", "付款ID", "状态", "货币", "交易类型",
            "订单 ID/调整单 ID", "SKU ID", "数量", "额外字段",
        ])
        sheet.append([
            "2026/08/31", "SETTLEMENT", "PAYMENT", "已付款", "MXN", "订单",
            "ORDER-1", "1735284450932852600", "2", "ignored",
        ])
        output = io.BytesIO()
        workbook.save(output)

        source_kind, sheet_name, rows = _read_import_file("future-export.xlsx", output.getvalue())

        self.assertEqual(source_kind, "order_detail")
        self.assertEqual(sheet_name, "订单详情")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["结算单 ID"], "SETTLEMENT")
        self.assertEqual(rows[0]["订单ID/调整单ID"], "ORDER-1")
        self.assertEqual(rows[0]["SKU ID"], "1735284450932852600")


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
