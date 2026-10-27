import os
import unittest
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database import Base
from models import InventoryBatch, Product, Sale, SaleCostDetail, Store, User
from routers.reports import (
    _build_store_profit_loss_values,
    _fifo_cost_components,
    _month_periods,
    _prepare_profit_loss_oa_values,
    _store_profit_loss_auto_values,
    _store_profit_loss_response,
    update_store_profit_loss,
)
from schemas import StoreProfitLossUpdateRequest
from tests.test_batch_edit import AsyncFacade


class VoidedHoldingPeriodTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flag = patch.dict(os.environ, {"FIFO_FINANCIAL_CONTROLS_ENABLED": "0"})
        self.flag.start()
        self.addCleanup(self.flag.stop)
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.session.add_all([
            Store(id=7, platform="test", platform_store_id="7", name="S7",
                  country_or_region="CN", settlement_currency="CNY"),
            Store(id=8, platform="test", platform_store_id="8", name="S8",
                  country_or_region="CN", settlement_currency="CNY"),
            User(id=1, username="holder", role="operator", password_hash="test", store_id=7),
            Product(id=10, sku="HOLDING", name="持有期测试", product_type="stable", safe_stock_quantity=0),
            InventoryBatch(id=20, product_id=10, user_id=1, batch_no="HOLDING-001",
                quantity=10, remaining_quantity=6, unit_cost=Decimal("2"), purchase_price=Decimal("2"),
                total_amount=Decimal("20"), shipping_cost=0, last_mile_cost=0, other_cost=0,
                arrived_at=datetime(2026, 8, 1)),
            Sale(id=30, product_id=10, user_id=1, store_id=7, order_no="HOLDING-SALE", quantity=4,
                 selling_price=Decimal("10"), total_cost=Decimal("8"), platform_fee=0,
                 profit=Decimal("32"), sold_at=datetime(2026, 9, 8)),
            SaleCostDetail(id=40, sale_id=30, batch_id=20, quantity=4, unit_cost=Decimal("2")),
        ])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    async def values(self, month, store_id=7):
        # 每次调用模拟独立请求；不沿用上次查询或作废前的回放缓存。
        self.session.info.clear()
        return await _store_profit_loss_auto_values(
            AsyncFacade(self.session), store_id, month,
            oa_values={"office": {}, "shared_admin": {}, "salaries": {}}, active_store_count=2,
        )

    def void(self, when=datetime(2026, 10, 27)):
        self.session.get(InventoryBatch, 20).voided_at = when
        self.session.commit()

    async def test_voided_batch_keeps_presale_holding_month_pending_in_all_periods(self):
        before = await self.values(date(2026, 8, 1))
        self.assertEqual(before["inventory_impairment"]["current"], Decimal("6"))
        self.void()
        august = await self.values(date(2026, 8, 1))
        self.assertIsNone(august["inventory_impairment"]["current"])
        self.assertIsNone(august["inventory_impairment"]["ytd"])
        self.assertEqual(august["inventory_impairment"]["previous"], Decimal(0))
        self.assertEqual(august["inventory_writeoff"]["current"], Decimal(0))
        computed = _build_store_profit_loss_values(august, {})
        self.assertIsNone(computed["net_profit"]["current"])
        self.assertTrue(any("已作废批次" in warning and "持有" in warning
                            for warning in august["_warnings"]))
        september = await self.values(date(2026, 9, 1))
        self.assertIsNone(september["inventory_impairment"]["previous"])
        other = await self.values(date(2026, 8, 1), store_id=8)
        self.assertEqual(other["inventory_impairment"]["current"], Decimal(0))
        self.assertEqual(other["_warnings"], [])

    async def test_sold_out_batch_does_not_mark_later_months_or_next_year_pending(self):
        self.session.get(InventoryBatch, 20).remaining_quantity = 0
        self.session.get(Sale, 30).quantity = 10
        self.session.get(Sale, 30).total_cost = Decimal("20")
        self.session.get(Sale, 30).profit = Decimal("80")
        self.session.get(SaleCostDetail, 40).quantity = 10
        self.void()
        october = await self.values(date(2026, 10, 1))
        self.assertEqual(october["inventory_impairment"]["current"], Decimal(0))
        self.assertIsNone(october["inventory_impairment"]["previous"])
        self.assertIsNone(october["inventory_impairment"]["ytd"])
        january = await self.values(date(2027, 1, 1))
        self.assertEqual(january["inventory_impairment"],
                         {period: Decimal(0) for period in ("current", "previous", "ytd")})

    async def test_no_sales_holdings_stop_at_void_business_day_including_month_boundary(self):
        self.session.delete(self.session.get(SaleCostDetail, 40))
        self.session.delete(self.session.get(Sale, 30))
        self.session.get(InventoryBatch, 20).remaining_quantity = 10
        # UTC 9月30日18时在现有核算时区为10月1日，不能提前一天结束。
        self.void(datetime(2026, 9, 30, 18))
        october = await self.values(date(2026, 10, 1))
        self.assertIsNone(october["inventory_impairment"]["current"])
        november = await self.values(date(2026, 11, 1))
        self.assertEqual(november["inventory_impairment"]["current"], Decimal(0))
        self.assertIsNone(november["inventory_impairment"]["previous"])

    async def test_arrival_day_zero_and_cross_year_comparisons_remain_separate(self):
        self.session.get(InventoryBatch, 20).arrived_at = datetime(2025, 12, 31)
        self.session.get(Sale, 30).sold_at = datetime(2026, 2, 1)
        self.void(datetime(2026, 3, 1))
        january = await self.values(date(2026, 1, 1))
        self.assertEqual(january["inventory_impairment"]["previous"], Decimal(0))
        self.assertIsNone(january["inventory_impairment"]["current"])
        self.assertIsNone(january["inventory_impairment"]["ytd"])


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _AutoValuesDb:
    def __init__(self, sales, cost_rows):
        self.results = [_Result(sales), _Result(cost_rows), _Result([])]

    async def execute(self, _statement):
        return self.results.pop(0)

    async def scalar(self, _statement):
        return 2


class StoreProfitLossTests(unittest.IsolatedAsyncioTestCase):
    async def test_voided_batch_marks_original_sale_month_cost_and_profit_pending(self):
        sale = SimpleNamespace(id=1, sold_at=datetime(2026, 9, 8),
                               selling_price=Decimal("10"), quantity=2, platform_fee=Decimal(0))
        detail = SimpleNamespace(quantity=2, unit_cost=Decimal("3"))
        batch = SimpleNamespace(id=11, batch_no="VOID-001", voided_at=datetime(2026, 10, 1))
        db = _AutoValuesDb([sale], [(detail, batch, sale.sold_at)])
        provision = {
            key: {"current": Decimal("2"), "previous": Decimal("1"), "ytd": Decimal("3")}
            for key in ("inventory_impairment", "inventory_writeoff")
        }
        with (
            patch("routers.sales._sale_import_context", new=AsyncMock(return_value=({}, {}))),
            patch("routers.reports._store_provision_financial_values", new=AsyncMock(return_value=provision)),
            patch("routers.reports.unlinked_settlement_events", new=AsyncMock(return_value=[])),
        ):
            values = await _store_profit_loss_auto_values(
                db, 7, date(2026, 9, 1), oa_values={"office": {}, "shared_admin": {}, "salaries": {}},
                active_store_count=1,
            )
        self.assertEqual(values["product_sales_revenue"]["current"], Decimal("20"))
        for key in ("product_purchase_cost", "head_logistics_cost", "inventory_other_cost",
                    "inventory_impairment", "inventory_writeoff"):
            self.assertIsNone(values[key]["current"])
            self.assertIsNone(values[key]["ytd"])
        self.assertEqual(values["inventory_impairment"]["previous"], Decimal("1"))
        self.assertEqual(values["inventory_writeoff"]["previous"], Decimal("1"))
        computed = _build_store_profit_loss_values(values, {})
        self.assertIsNone(computed["gross_profit"]["current"])
        self.assertIsNone(computed["net_profit"]["current"])
        self.assertTrue(any("已作废批次" in warning for warning in values["_warnings"]))

    async def test_voided_batch_marks_previous_sale_month_without_blanking_current(self):
        sale = SimpleNamespace(id=1, sold_at=datetime(2026, 9, 8),
                               selling_price=Decimal("10"), quantity=2, platform_fee=Decimal(0))
        detail = SimpleNamespace(quantity=2, unit_cost=Decimal("3"))
        batch = SimpleNamespace(id=11, batch_no="VOID-001", voided_at=datetime(2026, 10, 1))
        db = _AutoValuesDb([sale], [(detail, batch, sale.sold_at)])
        provision = {
            key: {period: Decimal("2") for period in ("current", "previous", "ytd")}
            for key in ("inventory_impairment", "inventory_writeoff")
        }
        with (
            patch("routers.sales._sale_import_context", new=AsyncMock(return_value=({}, {}))),
            patch("routers.reports._store_provision_financial_values", new=AsyncMock(return_value=provision)),
            patch("routers.reports.unlinked_settlement_events", new=AsyncMock(return_value=[])),
        ):
            values = await _store_profit_loss_auto_values(
                db, 7, date(2026, 10, 1), oa_values={"office": {}, "shared_admin": {}, "salaries": {}},
                active_store_count=1,
            )
        for key in ("product_purchase_cost", "head_logistics_cost", "inventory_other_cost",
                    "inventory_impairment", "inventory_writeoff"):
            self.assertIsNone(values[key]["previous"])
            self.assertIsNone(values[key]["ytd"])
        self.assertEqual(values["inventory_impairment"]["current"], Decimal("2"))
        self.assertEqual(values["inventory_writeoff"]["current"], Decimal("2"))

    async def test_oa_reads_start_after_mysql_transaction_is_released(self):
        events = []
        report_month = date(2026, 9, 1)

        async def count_stores(_statement):
            events.append('mysql_count')
            return 2

        async def release_mysql():
            events.append('mysql_commit')

        async def read_oa(name, result):
            self.assertEqual(events[:2], ['mysql_count', 'mysql_commit'])
            events.append(name)
            return result

        db = SimpleNamespace(scalar=count_stores, commit=release_mysql)
        office = {date(2026, 9, 8): Decimal('100')}
        shared = {date(2026, 9, 9): Decimal('20')}
        salaries = {'测试店铺': {date(2026, 9, 22): Decimal('30')}}

        async def office_reader(*_):
            return await read_oa('office', office)

        async def shared_reader(*_):
            return await read_oa('shared', shared)

        async def salary_reader(*_):
            return await read_oa('salary', salaries)

        with (
            patch('routers.reports.office_space_totals_by_application_date',
                  new=office_reader),
            patch('routers.reports.shared_admin_totals_by_application_date',
                  new=shared_reader),
            patch('routers.reports.china_salary_totals_by_store_and_application_date',
                  new=salary_reader),
        ):
            count, values = await _prepare_profit_loss_oa_values(db, report_month, True)
        self.assertEqual(events, ['mysql_count', 'mysql_commit', 'office', 'shared', 'salary'])
        self.assertEqual(count, 2)
        self.assertEqual(values, {'office': office, 'shared_admin': shared, 'salaries': salaries})

    async def test_prefetched_oa_values_do_not_reopen_external_connection(self):
        db = _AutoValuesDb([], [])
        zero = {key: {period: Decimal(0) for period in ('current', 'previous', 'ytd')}
                for key in ('inventory_impairment', 'inventory_writeoff')}
        oa_values = {
            'office': {date(2026, 9, 8): Decimal('100')},
            'shared_admin': {date(2026, 9, 9): Decimal('20')},
            'salaries': {'测试店铺': {date(2026, 9, 22): Decimal('30')}},
        }
        with (
            patch('routers.reports._store_provision_financial_values', new=AsyncMock(return_value=zero)),
            patch('routers.reports.unlinked_settlement_events', new=AsyncMock(return_value=[])),
            patch('routers.reports.office_space_totals_by_application_date',
                  new=AsyncMock(side_effect=AssertionError('unexpected OA read'))),
            patch('routers.reports.shared_admin_totals_by_application_date',
                  new=AsyncMock(side_effect=AssertionError('unexpected OA read'))),
            patch('routers.reports.china_salary_totals_by_store_and_application_date',
                  new=AsyncMock(side_effect=AssertionError('unexpected OA read'))),
        ):
            values = await _store_profit_loss_auto_values(
                db, 7, date(2026, 9, 1), '测试店铺',
                oa_values=oa_values, active_store_count=2,
            )
        self.assertEqual(values['rent_utilities']['current'], Decimal('50'))
        self.assertEqual(values['shared_admin']['current'], Decimal('10'))
        self.assertEqual(values['salary']['current'], Decimal('30'))

    async def _save_manual_report(self, saved, **payload):
        report = SimpleNamespace(manual_values=saved) if saved is not None else None
        db = SimpleNamespace(scalar=AsyncMock(return_value=report), add=Mock(),
                             commit=AsyncMock(), refresh=AsyncMock())
        data = StoreProfitLossUpdateRequest(store_id=7, report_month=date(2026,9,1), **payload)
        with patch("routers.reports._resolve_report_store", new=AsyncMock(return_value=SimpleNamespace(id=7, name="测试店铺"))), \
             patch("routers.reports._prepare_profit_loss_oa_values", new=AsyncMock(return_value=(2, {}))), \
             patch("routers.reports._store_profit_loss_response", new=AsyncMock(return_value=None)):
            await update_store_profit_loss(data, db, SimpleNamespace(id=9))
        self.assertIn("FOR UPDATE", str(db.scalar.await_args.args[0]))
        db.commit.assert_awaited_once()
        return report if report is not None else db.add.call_args.args[0]

    async def test_current_save_preserves_comparison_values_and_ignores_auto_keys(self):
        saved = {"salary": {"current":"100", "previous":"80", "ytd":"500"},
                 "advertising": {"current":"30", "previous":"20", "ytd":"300"}}
        report = await self._save_manual_report(saved, update_scope="current", manual_values={
            "salary": {"current":"120", "previous":"999", "ytd":"999"},
            "inventory_other_cost": {"current":"999"},
        })
        self.assertEqual(report.manual_values, {
            "salary": {"current":"120", "previous":"80", "ytd":"500"},
            "advertising": {"current":None, "previous":"20", "ytd":"300"},
        })
        self.assertEqual(saved["salary"]["current"], "100")

    async def test_current_save_clears_current_without_freezing_oa_comparison_defaults(self):
        report = await self._save_manual_report(
            {"salary": {"current":"120", "previous":"0", "ytd":None}},
            update_scope="current", manual_values={"salary":{"current":None}},
        )
        self.assertEqual(report.manual_values["salary"], {"current":None, "previous":"0", "ytd":None})
        values = _build_store_profit_loss_values(
            {"salary":{"current":Decimal(100), "previous":Decimal(80), "ytd":Decimal(500)}},
            report.manual_values,
        )
        self.assertEqual(values["salary"], {"current":Decimal(100), "previous":Decimal(0), "ytd":Decimal(500)})

    async def test_full_save_remains_the_default_replacement_contract(self):
        report = await self._save_manual_report(
            {"salary":{"current":"100", "previous":"80", "ytd":"500"}},
            manual_values={"advertising":{"current":"30", "ytd":"300"}},
        )
        self.assertEqual(report.manual_values, {
            "advertising":{"current":"30", "previous":None, "ytd":"300"},
        })

    async def test_new_current_only_report_does_not_save_comparison_inputs(self):
        report = await self._save_manual_report(None, update_scope="current", manual_values={
            "salary":{"current":"120", "previous":"80", "ytd":"500"},
        })
        self.assertEqual(report.manual_values, {
            "salary":{"current":"120", "previous":None, "ytd":None},
        })

    async def test_landed_other_cost_is_included_once_in_net_profit(self):
        sale = SimpleNamespace(id=1, sold_at=datetime(2026,9,30), selling_price=Decimal(30),
                               quantity=10, platform_fee=Decimal(0))
        batch = SimpleNamespace(id=1, quantity=100, purchase_price=Decimal(10), shipping_cost=Decimal(0),
                                last_mile_cost=Decimal(0), other_cost=Decimal(1000))
        db = _AutoValuesDb([sale], [(SimpleNamespace(quantity=10, unit_cost=Decimal(20)), batch, sale.sold_at)])
        zero = {key: {period: Decimal(0) for period in ("current", "previous", "ytd")}
                for key in ("inventory_impairment", "inventory_writeoff")}
        with patch("routers.sales._sale_import_context", new=AsyncMock(return_value=({},{}))), \
             patch("routers.reports._store_provision_financial_values", new=AsyncMock(return_value=zero)), \
             patch("routers.reports.office_space_totals_by_application_date", new=AsyncMock(return_value={})), \
             patch("routers.reports.shared_admin_totals_by_application_date", new=AsyncMock(return_value={})):
            auto = await _store_profit_loss_auto_values(db, 7, date(2026,9,1))
        values = _build_store_profit_loss_values(auto,{})
        self.assertEqual(auto["inventory_other_cost"]["current"], Decimal(100))
        self.assertEqual(values["operating_cost"]["current"], Decimal(200))
        self.assertEqual(values["net_profit"]["current"], Decimal(100))

    def test_cost_components_reconcile_to_saved_fifo_cost_after_rounding(self):
        detail = SimpleNamespace(quantity=1000, unit_cost=Decimal("0.0000"))
        batch = SimpleNamespace(id=1, quantity=1000, purchase_price=Decimal(0),
                                shipping_cost=Decimal(0), last_mile_cost=Decimal(0),
                                other_cost=Decimal("0.01"))
        self.assertEqual(_fifo_cost_components(detail, batch),
                         (Decimal(0), Decimal(0), Decimal(0)))

        detail.unit_cost = Decimal("10.0001")
        batch.purchase_price = Decimal(10)
        batch.other_cost = Decimal("0.01")
        purchase, logistics, other = _fifo_cost_components(detail, batch)
        self.assertEqual(purchase + logistics + other, Decimal("10000.1"))
        self.assertEqual(logistics, Decimal(0))
        self.assertGreater(other, Decimal(0))

    async def test_small_other_cost_does_not_exceed_saved_fifo_cost_in_report(self):
        sale = SimpleNamespace(id=1, sold_at=datetime(2026,9,30), selling_price=Decimal(0),
                               quantity=1000, platform_fee=Decimal(0))
        batch = SimpleNamespace(id=1, quantity=1000, purchase_price=Decimal(0),
                                shipping_cost=Decimal(0), last_mile_cost=Decimal(0),
                                other_cost=Decimal("0.01"))
        detail = SimpleNamespace(quantity=1000, unit_cost=Decimal("0.0000"))
        db = _AutoValuesDb([sale], [(detail, batch, sale.sold_at)])
        zero = {key: {period: Decimal(0) for period in ("current", "previous", "ytd")}
                for key in ("inventory_impairment", "inventory_writeoff")}
        with patch("routers.sales._sale_import_context", new=AsyncMock(return_value=({},{}))), \
             patch("routers.reports._store_provision_financial_values", new=AsyncMock(return_value=zero)), \
             patch("routers.reports.office_space_totals_by_application_date", new=AsyncMock(return_value={})), \
             patch("routers.reports.shared_admin_totals_by_application_date", new=AsyncMock(return_value={})):
            auto = await _store_profit_loss_auto_values(db, 7, date(2026,9,1))
        values = _build_store_profit_loss_values(auto, {})
        self.assertEqual(auto["inventory_other_cost"]["current"], Decimal(0))
        self.assertEqual(values["operating_cost"]["current"], Decimal(0))

    async def test_oa_salary_default_is_separate_from_saved_manual_value(self):
        auto = {"salary": {"current": Decimal(100), "previous": Decimal(80),
                            "ytd": Decimal(500)}}
        store = SimpleNamespace(id=7, name="测试店铺", platform="TikTok")
        user = SimpleNamespace(role="admin")
        with patch("routers.reports._store_profit_loss_auto_values", new=AsyncMock(return_value=auto)):
            response = await _store_profit_loss_response(None, user, store, date(2026,9,1), None)
            salary = next(row for row in response.rows if row.key == "salary")
            self.assertEqual(salary.value.current, Decimal(100))
            self.assertIsNone(salary.manual_value)

            saved = SimpleNamespace(manual_values={"salary": {"current": "120"}},
                                    remarks={}, updated_at=None)
            response = await _store_profit_loss_response(None, user, store, date(2026,9,1), saved)
            salary = next(row for row in response.rows if row.key == "salary")
            self.assertEqual(salary.value.current, Decimal(120))
            self.assertEqual(salary.manual_value.current, Decimal(120))

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
            id=1,
            quantity=5,
            purchase_price=Decimal("15"),
            shipping_cost=Decimal("10"),
            last_mile_cost=Decimal("5"),
            other_cost=Decimal("5"),
        )
        previous_batch = SimpleNamespace(
            id=2,
            quantity=3,
            purchase_price=Decimal("12"),
            shipping_cost=Decimal("6"),
            last_mile_cost=Decimal("3"),
            other_cost=Decimal("3"),
        )
        db = _AutoValuesDb(
            [current_sale, previous_sale],
            [
                (SimpleNamespace(quantity=2, unit_cost=Decimal(19)), current_batch, current_sale.sold_at),
                (SimpleNamespace(quantity=1, unit_cost=Decimal(16)), previous_batch, previous_sale.sold_at),
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

        impairment_totals = AsyncMock(return_value={
            "inventory_impairment": {"current": Decimal("10"), "previous": Decimal("5"), "ytd": Decimal("30")},
            "inventory_writeoff": {period: Decimal("0") for period in ("current", "previous", "ytd")},
            "_warnings": [],
        })
        with (
            patch("routers.sales._sale_import_context", new=AsyncMock(side_effect=import_context)),
            patch(
                "routers.reports._store_provision_financial_values",
                new=impairment_totals,
            ),
            patch(
                "routers.reports.office_space_totals_by_application_date",
                new=AsyncMock(return_value={
                    date(2026, 8, 8): Decimal("20"),
                    date(2026, 7, 18): Decimal("10"),
                }),
            ),
            patch(
                "routers.reports.china_salary_totals_by_store_and_application_date",
                new=AsyncMock(return_value={
                    "测试店铺": {
                        date(2026, 8, 8): Decimal("80"),
                        date(2026, 7, 18): Decimal("40"),
                    },
                }),
            ),
            patch(
                "routers.reports.shared_admin_totals_by_application_date",
                new=AsyncMock(return_value={
                    date(2026, 8, 8): Decimal("30"),
                    date(2026, 7, 18): Decimal("10"),
                }),
            ),
        ):
            values = await _store_profit_loss_auto_values(
                db, 7, date(2026, 8, 1), "测试店铺"
            )

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
        self.assertEqual(values["inventory_other_cost"], {
            "current": Decimal("2"), "previous": Decimal("1"), "ytd": Decimal("3"),
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
        self.assertEqual(values["salary"], {
            "current": Decimal("80"),
            "previous": Decimal("40"),
            "ytd": Decimal("120"),
        })
        self.assertEqual(values["shared_admin"], {
            "current": Decimal("15"),
            "previous": Decimal("5"),
            "ytd": Decimal("20"),
        })
        self.assertEqual(impairment_totals.await_args.args[2], _month_periods(date(2026, 8, 1)))

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
            "shared_admin": {
                "current": Decimal("9"), "previous": Decimal("3"), "ytd": Decimal("15"),
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

    def test_saved_salary_overrides_oa_default_and_blank_uses_default(self):
        auto_values = {
            "salary": {
                "current": Decimal("100"),
                "previous": Decimal("80"),
                "ytd": Decimal("500"),
            },
        }
        manual_values = {
            "salary": {"current": "120", "previous": None, "ytd": ""},
        }

        values = _build_store_profit_loss_values(auto_values, manual_values)

        self.assertEqual(values["salary"], {
            "current": Decimal("120"),
            "previous": Decimal("80"),
            "ytd": Decimal("500"),
        })

    def test_saved_shared_admin_value_cannot_override_oa_default(self):
        auto_values = {
            "shared_admin": {
                "current": Decimal("100"),
                "previous": Decimal("80"),
                "ytd": Decimal("500"),
            },
        }
        manual_values = {
            "shared_admin": {"current": "999", "previous": "999", "ytd": "999"},
        }

        values = _build_store_profit_loss_values(auto_values, manual_values)

        self.assertEqual(values["shared_admin"], {
            "current": Decimal("100"),
            "previous": Decimal("80"),
            "ytd": Decimal("500"),
        })

    def test_gross_margin_is_empty_when_revenue_is_zero(self):
        values = _build_store_profit_loss_values({}, {})
        self.assertIsNone(values["gross_margin"]["current"])


if __name__ == "__main__":
    unittest.main()
