"""销售与损益共用核算汇率：真实路由、离线汇率、隔离 SQLite，不连接业务库。"""
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO
from unittest.mock import AsyncMock, patch

from openpyxl import load_workbook
from sqlalchemy import select

from models import (InventoryBatch, Product, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry,
                    SettlementEntryAllocation, StoreProfitLossReport, User)
from routers.reports import _store_profit_loss_auto_values
from services.exchange_rates import ExchangeRateError
from services.report_exchange_rates import FX_OVERRIDE_KEY, invalidate_report_rates
from tests import test_sales_export as exports


class SalesReportCurrencyTests(unittest.IsolatedAsyncioTestCase):
    entry = exports.SalesExportTests.entry
    record_query = exports.SalesExportTests.record_query
    asyncTearDown = exports.SalesExportTests.asyncTearDown

    def setUp(self):
        exports.SalesExportTests.setUp(self)
        clock = patch('services.report_exchange_rates.report_today', return_value=date(2026, 11, 25))
        clock.start(); self.addCleanup(clock.stop)
        for entry in self.session.scalars(select(SettlementEntry)):
            entry.currency = 'MXN'
            entry.exchange_rate_to_cny = D('.01')
            entry.exchange_rate_date = date(2026, 9, 1)
            entry.exchange_rate_source = 'historical-daily'
        self.session.commit()

    def setting(self, rate='.3532', *, store=7, month=date(2026, 9, 1), currency='MXN', mode='manual'):
        row = self.session.scalar(select(StoreProfitLossReport).where(
            StoreProfitLossReport.store_id == store, StoreProfitLossReport.report_month == month))
        if row is None:
            row = StoreProfitLossReport(store_id=store, report_month=month)
            self.session.add(row)
        metadata = dict(row.report_metadata or {})
        metadata[FX_OVERRIDE_KEY] = {**metadata.get(FX_OVERRIDE_KEY, {}),
                                    currency: {'mode': mode, 'manual_rate': rate}}
        row.report_metadata = metadata
        self.session.commit()
        invalidate_report_rates(self.db)

    def snapshot(self):
        return {model.__tablename__: list(self.session.execute(select(*model.__table__.columns).order_by(model.id)))
                for model in (InventoryBatch, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation)}

    async def get(self, url, **params):
        response = await self.client.get(url, params=params)
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        return response

    async def listed(self, **params):
        result = (await self.get('/sales', **params)).json()
        return result['items'] if isinstance(result, dict) else result

    def records(self, book, name):
        values = list(book[name].values)
        return [dict(zip(values[0], row)) for row in values[1:-1]]

    async def workbook(self, url='/sales/export', **params):
        return load_workbook(BytesIO((await self.get(url, **params)).content), data_only=True)

    async def test_manual_rate_matches_report_on_every_read_surface_without_writes(self):
        self.setting()
        before = self.snapshot()
        self.queries.clear()
        with patch('services.report_exchange_rates.get_cny_rates', new=AsyncMock(side_effect=AssertionError('manual must work offline'))):
            listed = await self.listed(month='2026-09')
            paged = await self.listed(month='2026-09', page=1, page_size=100)
            detail = (await self.get('/sales/1')).json()
            breakdown = (await self.get('/sales/1/cost-breakdown')).json()['sale']
            summary = (await self.get('/sales/monthly-summary', month='2026-09')).json()[0]
            books = [await self.workbook(month='2026-09'), await self.workbook('/sales/imports/1/export')]
            provisions = {key: dict.fromkeys(('current', 'previous', 'ytd'), D(0))
                          for key in ('inventory_impairment', 'inventory_writeoff')}
            with patch('routers.reports._store_provision_financial_values', new=AsyncMock(return_value=provisions)):
                report = await _store_profit_loss_auto_values(self.db, 7, date(2026, 9, 1), oa_values={'office': {}}, active_store_count=0)
        self.assertEqual(listed, paged)
        for row in [*listed, detail, breakdown]:
            self.assertEqual(row['exchange_rate_policy'], 'store_month_report_v1')
            self.assertEqual(row['exchange_rate_store_id'], 7)
            self.assertEqual(D(row['exchange_rate_to_cny']), D('.3532'))
            self.assertEqual(row['exchange_rate_month'], '2026-09')
            self.assertEqual(row['exchange_rate_source'], 'manual')
            self.assertFalse(row['exchange_rate_is_provisional'])
        income = report['product_sales_revenue']['current'] + report['compensation_revenue']['current']
        self.assertEqual(summary['exchange_rate_policy'], 'store_month_report_v1')
        self.assertEqual(D(summary['sales_revenue_cny']), income)
        self.assertEqual(D(summary['platform_fee_cny']), report['other_direct_cost']['current'])
        self.assertEqual(D(summary['sales_revenue_cny']), D(85) * D('.3532'))
        self.assertEqual(D(summary['sales_cost_cny']), D(40))
        for book in books:
            original = self.records(book, '原始结算对账')
            split = self.records(book, '销售拆分明细')
            self.assertEqual(len(original), 4)  # includes the pending source, unlike the ledger
            self.assertEqual(len(split), 3)
            for row in [*original, *split]:
                self.assertEqual(D(str(row['核算汇率'])), D('.3532'))
                self.assertEqual(row['汇率核算月份'], '2026-09')
                self.assertEqual(row['汇率来源'], '手动')
            self.assertEqual(sum(D(str(row['店铺收入（元）'])) for row in split), income)
            self.assertIsNotNone(book.active['A2'].hyperlink)
        self.assertTrue(set(self.queries) <= {'SELECT'}, self.queries)
        self.assertEqual(before, self.snapshot())

    async def test_month_end_quote_is_shared_for_different_order_days(self):
        provider = AsyncMock(return_value={('MXN', date(2026, 9, 30)): {
            'rate': D('.4'), 'rate_date': date(2026, 9, 29), 'source': 'Frankfurter/ECB'}})
        with patch('services.report_exchange_rates.get_cny_rates', new=provider):
            rows = await self.listed()
        provider.assert_awaited_once_with({('MXN', date(2026, 9, 30))})
        self.assertEqual({row['exchange_rate_to_cny'] for row in rows}, {'0.4'})
        self.assertEqual({row['exchange_rate_date'] for row in rows}, {'2026-09-29'})
        self.assertEqual({row['exchange_rate_source'] for row in rows}, {'Frankfurter/ECB'})

    async def test_each_store_month_and_currency_uses_its_own_rate(self):
        self.setting('.3')
        self.setting('.7', store=8)
        self.setting('.2', month=date(2025, 12, 1))
        self.setting('7', currency='USD')
        self.entry(6, net=10, total=8, quantity=-1, occurred=datetime(2025, 12, 31), currency='MXN', rate=99)
        self.entry(7, net=10, total=8, quantity=-1, currency='USD', rate=99)
        self.session.commit()
        self.actor.role = 'admin'
        rows = {row['id']: row for row in await self.listed()}
        self.assertEqual(D(rows[1]['exchange_rate_to_cny']), D('.3'))
        self.assertEqual(D(rows[-5]['exchange_rate_to_cny']), D('.7'))
        self.assertEqual(D(rows[-6]['exchange_rate_to_cny']), D('.2'))
        self.assertEqual(rows[-6]['exchange_rate_month'], '2025-12')
        self.assertEqual(D(rows[-7]['exchange_rate_to_cny']), D(7))
        summaries = (await self.get('/sales/monthly-summary')).json()
        for summary in summaries:
            current = [row for row in rows.values() if row['sold_at'].startswith(summary['month'])]
            self.assertEqual(D(summary['sales_revenue_cny']), sum(D(row['sales_revenue_cny']) for row in current))
        self.actor.role = 'viewer'
        self.assertNotIn(-5, {row['id'] for row in await self.listed()})

    async def test_missing_report_rate_does_not_fall_back_to_saved_daily_rate(self):
        before = self.snapshot()
        self.unavailable_monthly_rates = True
        rows = await self.listed()
        summary = (await self.get('/sales/monthly-summary')).json()[0]
        for row in rows:
            self.assertIsNone(row['exchange_rate_to_cny'])
            self.assertIsNone(row['sales_revenue_cny'])
            self.assertIsNone(row['profit_cny'])
        compensation = next(row for row in rows if row['id'] == -3)
        self.assertEqual(D(compensation['platform_fee_cny']), D(0))
        self.assertIsNone(summary['sales_revenue_cny'])
        self.assertEqual(summary['fx_missing_count'], 3)
        self.assertEqual(D(summary['sales_cost_cny']), D(40))
        book = await self.workbook('/sales/imports/1/export')
        self.assertTrue(all(row['核算汇率'] is None for row in self.records(book, '原始结算对账')))
        self.assertEqual(before, self.snapshot())

    async def test_corrupt_manual_rate_is_unknown_instead_of_automatic_or_daily(self):
        self.setting('NaN')
        with patch('services.report_exchange_rates.get_cny_rates', new=AsyncMock(side_effect=AssertionError('no fallback'))):
            row = (await self.get('/sales/1')).json()
        self.assertIsNone(row['exchange_rate_to_cny'])
        self.assertIsNone(row['profit_cny'])
        self.assertEqual(row['exchange_rate_status'], 'missing')

    async def test_missing_saved_daily_rate_does_not_block_valid_report_rate(self):
        entry = self.session.get(SettlementEntry, 1)
        entry.exchange_rate_to_cny = None
        self.session.commit()
        self.setting('.4')
        row = (await self.get('/sales/1')).json()
        self.assertEqual(D(row['sales_revenue_cny']), D(40))
        self.assertIsNone(entry.exchange_rate_to_cny)

    async def test_current_month_auto_is_unknown_manual_is_provisional_and_exported(self):
        self.session.get(Sale, 1).sold_at = datetime(2026, 11, 10)
        for entry in self.session.scalars(select(SettlementEntry)):
            entry.order_created_at = datetime(2026, 11, 10)
        self.session.commit()
        with patch('services.report_exchange_rates.get_cny_rates', new=AsyncMock(side_effect=AssertionError('open month'))):
            automatic = (await self.get('/sales/1')).json()
            self.assertIsNone(automatic['sales_revenue_cny'])
            self.assertEqual(automatic['exchange_rate_status'], 'month_not_closed')
            self.setting('.4', month=date(2026, 11, 1))
            manual = (await self.get('/sales/1')).json()
            self.assertTrue(manual['exchange_rate_is_provisional'])
            summary = (await self.get('/sales/monthly-summary')).json()[0]
            self.assertEqual(summary['fx_provisional_count'], 3)
            book = await self.workbook('/sales/imports/1/export')
        for name in ('原始结算对账', '销售拆分明细'):
            self.assertTrue(all(row['汇率来源'] == '手动（暂估）' for row in self.records(book, name)))

    async def test_old_sale_owner_fallback_and_unassigned_owner_do_not_borrow_viewer_rate(self):
        self.setting('.3')
        sale = self.session.get(Sale, 1)
        sale.store_id = None
        self.session.commit()
        row = (await self.get('/sales/1')).json()
        self.assertEqual(D(row['exchange_rate_to_cny']), D('.3'))
        sale.user_id = None
        self.session.commit()
        self.actor.role = 'admin'
        row = (await self.get('/sales/1')).json()
        self.assertIsNone(row['exchange_rate_to_cny'])
        self.assertEqual(row['exchange_rate_status'], 'store_missing')
        self.assertEqual(D(row['total_cost']), D(40))

    async def test_currency_cny_and_known_zero_remain_calculable_without_external_rates(self):
        self.unavailable_monthly_rates = True
        self.session.get(SettlementEntry, 1).currency = 'CNY'
        self.entry(6, net=0, total=0, quantity=0, currency='USD', rate=None)
        self.session.commit()
        rows = {row['id']: row for row in await self.listed()}
        self.assertEqual(D(rows[1]['exchange_rate_to_cny']), D(1))
        self.assertEqual(D(rows[1]['sales_revenue_cny']), D(100))
        self.assertEqual(D(rows[-6]['sales_revenue_cny']), D(0))
        self.assertEqual(D(rows[-6]['profit_cny']), D(0))

    async def test_changing_rate_and_restoring_auto_only_changes_read_projection(self):
        self.setting('.3')
        before = self.snapshot()
        initial = (await self.get('/sales/1')).json()
        self.setting('.4')
        changed = (await self.get('/sales/1')).json()
        self.setting(mode='auto', rate=None)
        automatic = (await self.get('/sales/1')).json()
        self.assertEqual([D(row['sales_revenue_cny']) for row in (initial, changed, automatic)], [D(30), D(40), D(50)])
        self.assertEqual(before, self.snapshot())

    async def test_linked_source_uses_sales_month_and_keeps_original_order_date(self):
        self.setting('.3')
        self.setting('.6', month=date(2026, 10, 1))
        self.session.get(Sale, 1).sold_at = datetime(2026, 10, 10)
        self.session.commit()
        book = await self.workbook('/sales/imports/1/export')
        original = next(row for row in self.records(book, '原始结算对账') if row['关联编号'] == 'E1')
        detail = next(row for row in self.records(book, '销售拆分明细') if row['销售明细编号'] == 'S1')
        self.assertEqual(original['订单创建日期'].month, 9)
        self.assertEqual(original['汇率核算月份'], '2026-10')
        self.assertEqual(original['核算汇率'], detail['核算汇率'])
        self.assertEqual(original['来源收入（折人民币）'], detail['店铺收入（元）'])

    async def test_source_linked_to_multiple_months_does_not_invent_a_single_rate(self):
        self.setting('.3')
        self.setting('.6', month=date(2026, 10, 1))
        self.session.add(Product(id=11, sku='P11', name='第二组件'))
        self.session.flush()
        self.session.add(Sale(id=2, store_id=7, user_id=1, product_id=11, order_no='CROSS-MONTH',
                              quantity=1, selling_price=100, total_cost=0, platform_fee=10, profit=90,
                              sold_at=datetime(2026, 10, 10)))
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=1, sale_id=2, product_id=11,
                                                   component_name='P11', quantity=1))
        self.session.commit()
        book = await self.workbook('/sales/imports/1/export')
        original = next(row for row in self.records(book, '原始结算对账') if row['关联编号'] == 'E1')
        self.assertEqual(original['汇率核算月份'], '2026-09、2026-10')
        self.assertIsNone(original['核算汇率'])
        self.assertIsNone(original['来源收入（折人民币）'])
        self.assertIn('跨核算月份', original['待核对原因'])
        self.session.get(SettlementEntry, 1).currency = 'CNY'
        self.session.commit()
        book = await self.workbook('/sales/imports/1/export')
        original = next(row for row in self.records(book, '原始结算对账') if row['关联编号'] == 'E1')
        self.assertEqual(original['核算汇率'], 1)
        self.assertEqual(original['来源收入（折人民币）'], 100)

    async def test_export_owner_conflict_is_unknown_in_both_exports_without_changing_source(self):
        self.setting('.3', store=7)
        self.setting('.7', store=8)
        self.actor.role = 'admin'
        sale = self.session.get(Sale, 1)
        for stored_owner in (None, 8):
            with self.subTest(stored_owner=stored_owner):
                sale.store_id = stored_owner
                self.session.get(User, 1).store_id = 8
                self.session.commit()
                before = self.snapshot()
                self.queries.clear()
                detail = (await self.get('/sales/1')).json()
                self.assertEqual(detail['exchange_rate_store_id'], 8)
                self.assertEqual(D(detail['sales_revenue_cny']), D(70))
                for url in ('/sales/export', '/sales/imports/1/export'):
                    book = await self.workbook(url)
                    original = next(row for row in self.records(book, '原始结算对账') if row['关联编号'] == 'E1')
                    split = next(row for row in self.records(book, '销售拆分明细') if row['销售明细编号'] == 'S1')
                    self.assertEqual(original['店铺'], 'S7')
                    self.assertEqual(original['净商品销售额（原币）'], 100)
                    self.assertEqual(original['处理状态'], '待核对')
                    self.assertIn('核算店铺不一致', original['待核对原因'])
                    for column in ('核算汇率', '来源收入（折人民币）', '来源费用（折人民币）', '毛利（元）'):
                        self.assertIsNone(original[column], column)
                    self.assertEqual(original['销售成本（元）'], 40)
                    self.assertEqual(original['销售明细编号'], 'S1')
                    self.assertEqual(D(str(split['核算汇率'])), D('.7'))
                    self.assertEqual(split['店铺收入（元）'], 70)
                    values = list(book['原始结算对账'].values)
                    self.assertIsNone(dict(zip(values[0], values[-1]))['来源收入（折人民币）'])
                self.assertTrue(set(self.queries) <= {'SELECT'}, self.queries)
                self.assertEqual(before, self.snapshot())

    async def test_export_missing_sale_owner_does_not_borrow_source_store_rate(self):
        self.setting('.3')
        self.actor.role = 'admin'
        sale = self.session.get(Sale, 1)
        sale.store_id = sale.user_id = None
        self.session.commit()
        for url in ('/sales/export', '/sales/imports/1/export'):
            book = await self.workbook(url)
            original = next(row for row in self.records(book, '原始结算对账') if row['关联编号'] == 'E1')
            split = next(row for row in self.records(book, '销售拆分明细') if row['销售明细编号'] == 'S1')
            self.assertIsNone(original['核算汇率'])
            self.assertIsNone(split['核算汇率'])
            self.assertIn('销售归属缺失', original['待核对原因'])

    async def test_mixed_sale_owners_are_flagged_even_when_rates_match(self):
        self.setting('.3', store=7)
        self.setting('.3', store=8)
        self.actor.role = 'admin'
        self.session.add(Product(id=11, sku='P11', name='另一店铺组件'))
        self.session.flush()
        self.session.add(Sale(id=2, store_id=8, user_id=1, product_id=11, order_no='CROSS-STORE',
                              quantity=1, selling_price=100, total_cost=0, platform_fee=10, profit=90,
                              sold_at=datetime(2026, 9, 10)))
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=1, sale_id=2, product_id=11,
                                                   component_name='P11', quantity=1))
        self.session.commit()
        for url in ('/sales/export', '/sales/imports/1/export'):
            book = await self.workbook(url)
            original = next(row for row in self.records(book, '原始结算对账') if row['关联编号'] == 'E1')
            self.assertEqual(original['处理状态'], '待核对')
            self.assertIsNone(original['核算汇率'])
            self.assertIn('核算店铺不一致', original['待核对原因'])
        self.setting('.3', store=8, month=date(2026, 10, 1))
        self.session.get(Sale, 2).sold_at = datetime(2026, 10, 10)
        self.session.commit()
        book = await self.workbook('/sales/imports/1/export')
        original = next(row for row in self.records(book, '原始结算对账') if row['关联编号'] == 'E1')
        self.assertEqual(original['汇率核算月份'], '2026-09、2026-10')
        self.assertIn('核算店铺不一致', original['待核对原因'])
        self.assertIn('跨核算月份', original['待核对原因'])
        # 人民币不需要外汇换算，但仍明确提示归属冲突。
        self.session.get(SettlementEntry, 1).currency = 'CNY'
        self.session.commit()
        book = await self.workbook('/sales/imports/1/export')
        original = next(row for row in self.records(book, '原始结算对账') if row['关联编号'] == 'E1')
        self.assertEqual(original['核算汇率'], 1)
        self.assertEqual(original['来源收入（折人民币）'], 100)
        self.assertEqual(original['处理状态'], '待核对')
        self.assertIn('核算店铺不一致', original['待核对原因'])
