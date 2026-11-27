"""月末汇率、8 MXN履约费、真实累计与历史值保护；隔离SQLite，无外部请求。"""
import os
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete

from models import (Product, ProductLine, StoreProduct, Sale, SalesImportBatch, SettlementEntry,
                    SettlementEntryAllocation, Store, StoreProfitLossReport, FinancialExpenseSource)
from routers.reports import (_store_profit_loss_auto_values, _store_profit_loss_response,
                             _build_store_profit_loss_values, update_store_profit_loss)
from schemas import StoreProfitLossUpdateRequest
from services.exchange_rates import ExchangeRateError
from services.report_exchange_rates import monthly_report_rates, rate_key, fulfillment_orders, convert_report_amount
from tests import test_financial_facts as facts
from tests.test_sales_import_store import Facade


class MonthlyRatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_month_end_nearest_prior_date_cache_and_currency_isolation(self):
        db=NS(info={});calls=[]
        async def provider(requirements):
            calls.append(requirements)
            currency, day=next(iter(requirements))
            if currency=='EUR':raise ExchangeRateError('unavailable')
            return {(currency,day):{'rate':D('.4'),'rate_date':date(2026,8,28),'source':'fixture'}}
        with patch('services.report_exchange_rates.get_cny_rates',new=provider):
            rates=await monthly_report_rates(db,{('MXN',date(2026,8,2)),('EUR',date(2026,8,30)),('CNY',date(2026,8,1))})
            await monthly_report_rates(db,{('MXN',date(2026,8,5))})
        self.assertEqual(len(calls),2)
        self.assertTrue(all(next(iter(req))[1]==date(2026,8,31) for req in calls))
        self.assertEqual(rates[('MXN',date(2026,8,1))]['rate_date'],date(2026,8,28))
        self.assertEqual(convert_report_amount(8,rates[('MXN',date(2026,8,1))]),D('3.2'))
        self.assertIsNone(rates[('EUR',date(2026,8,1))]['rate_to_cny'])
        self.assertEqual(convert_report_amount(0,rates[('EUR',date(2026,8,1))]),D(0))
        self.assertEqual(rates[('CNY',date(2026,8,1))]['rate_to_cny'],D(1))

    async def test_open_month_does_not_invent_month_end_rate(self):
        provider=AsyncMock()
        with patch('services.report_exchange_rates.report_today',return_value=date(2026,11,2)), \
             patch('services.report_exchange_rates.get_cny_rates',new=provider):
            rates=await monthly_report_rates(NS(info={}),{('MXN',date(2026,11,1))})
        provider.assert_not_awaited()
        self.assertEqual(rates[('MXN',date(2026,11,1))]['status'],'month_not_closed')
        self.assertIsNone(rates[('MXN',date(2026,11,1))]['rate_to_cny'])

    def test_fulfillment_keys_ignore_sku_and_components_but_keep_store_month(self):
        sales=[NS(id=i,store_id=7 if i<3 else 8,quantity=1,sold_at=datetime(2026,9,1),order_no=f'O-SKU-{i}') for i in range(1,4)]
        sales.append(NS(id=4,store_id=7,quantity=0,sold_at=datetime(2026,9,1),order_no='REFUND'))
        contexts={i:{'source_fulfillment_order_id':'O'} for i in range(1,4)}
        self.assertEqual(fulfillment_orders(sales,contexts),{date(2026,9,1):{(7,'O'),(8,'O')}})


class MonthlyProfitPolicyTests(unittest.IsolatedAsyncioTestCase):
    entry=facts.FinancialFactsTests.entry
    tearDown=facts.FinancialFactsTests.tearDown
    def setUp(self):
        facts.FinancialFactsTests.setUp(self)
        self.session.execute(delete(SettlementEntry).where(SettlementEntry.id!=1))
        entry=self.session.get(SettlementEntry,1)
        entry.currency='USD';entry.exchange_rate_to_cny=D('.01')
        self.session.commit()
        self.calls=[]
        async def rates(requirements):
            self.calls.append(requirements)
            return {(currency,day):{'rate':({8:D('.4'),9:D('.5')} if currency=='USD' else {8:D('.2'),9:D('.3')})[day.month],
                'rate_date':day,'source':'monthly-fixture'} for currency,day in requirements}
        for target,value in [
            ('services.report_exchange_rates.get_cny_rates',rates),
            ('routers.reports._store_provision_financial_values',AsyncMock(return_value=facts.FinancialFactsTests.zero_provisions(self))),
            ('routers.reports.office_space_totals_by_application_date',AsyncMock(return_value={})),
            ('routers.reports.shared_admin_expenses_by_store_and_month',AsyncMock(return_value=({}, {}))),
            ('routers.reports.china_salary_totals_by_store_and_application_date',AsyncMock(return_value={})),
            ('routers.reports.payroll_supplements_by_store',AsyncMock(return_value={})),
        ]:
            item=patch(target,new=value);item.start();self.addCleanup(item.stop)
        flag=patch.dict(os.environ,{'FIFO_FINANCIAL_CONTROLS_ENABLED':'0'});flag.start();self.addCleanup(flag.stop)

    async def auto(self):
        return await _store_profit_loss_auto_values(self.db,7,date(2026,9,1))

    async def product_lines(self):
        from routers.reports import _product_line_profit_data
        with patch('routers.reports._sale_provision_adjustments',new=AsyncMock(return_value={1:(D(0),'')})), \
             patch('routers.reports._load_inventory_provision_data',new=AsyncMock(return_value=([],[],None))):
            return await _product_line_profit_data(self.db,{7},date(2026,9,1),date(2026,9,30))

    def add_august_product_line(self, *, zero_revenue=False):
        self.session.add_all([Product(id=11,sku='P11',name='Second'),
            ProductLine(id=1,code='A',name='A'),ProductLine(id=2,code='B',name='B')])
        self.session.flush()
        self.session.add_all([
            StoreProduct(store_id=7,product_id=10,product_line_id=1,platform_sku_id='P10',
                         store_sku='P10',effective_from=date(2026,1,1)),
            StoreProduct(store_id=7,product_id=11,product_line_id=2,platform_sku_id='P11',
                         store_sku='P11',effective_from=date(2026,1,1))])
        net=0 if zero_revenue else 100
        self.session.add(Sale(id=2,product_id=11,user_id=1,store_id=7,order_no='AUG',quantity=1,
            selling_price=net,total_cost=0,platform_fee=0,profit=net,sold_at=datetime(2026,8,20)))
        self.session.flush()
        entry=self.entry(6,net=net,total=net,quantity=1,product=11,currency='USD',rate='.01',occurred=datetime(2026,8,20))
        entry.platform_sku_id='P11'
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=entry.id,product_id=11,
            sale_id=2,component_name='P11',quantity=1))
        self.session.commit()

    async def test_legacy_csv_uses_saved_sale_number_for_allocated_and_direct_sources(self):
        from routers.sales import _sale_import_context, _settlement_entry
        entry=self.session.get(SettlementEntry,1)
        entry.order_id='P10';entry.raw_data={'SKU':'P10','日期':'2026-09-10'}
        self.session.get(Sale,1).order_no='2026-09-10-P10'
        self.session.get(SalesImportBatch,1).file_type='legacy_csv'
        self.session.add(Sale(id=2,product_id=10,user_id=1,store_id=7,order_no='2026-09-20-P10',quantity=1,
            selling_price=0,total_cost=0,platform_fee=0,profit=0,sold_at=datetime(2026,9,20)))
        self.session.flush()
        second=_settlement_entry({'SKU':'P10','日期':datetime(2026,9,20),'数量':1,'_row_number':2},
            'legacy-second','legacy.csv',None,10,2,7,1,None)
        self.session.add(second);self.session.commit()
        auto=await self.auto()
        self.assertEqual(auto['_fulfillment_counts']['current'],2)
        self.assertEqual(auto['fulfillment_cost']['current'],D('4.8'))
        # 真实订单号恰好等于SKU时仍按订单去重，不能仅比较编号文本猜测文件类型。
        self.session.get(SalesImportBatch,1).file_type='order_detail';self.session.commit()
        contexts,_=await _sale_import_context(self.db,list(self.session.query(Sale).all()))
        self.assertEqual(len(fulfillment_orders(list(self.session.query(Sale).all()),contexts)[date(2026,9,1)]),1)
        self.assertEqual(second.order_id,'P10')

    async def test_store_fallback_deduplicates_bundle_and_matches_product_lines(self):
        self.session.add(Product(id=11,sku='PART',name='Second'));self.session.flush()
        self.session.add(Sale(id=2,product_id=11,user_id=1,store_id=None,order_no='SECOND-COMPONENT',quantity=1,
            selling_price=10,total_cost=0,platform_fee=0,profit=10,sold_at=datetime(2026,9,10)))
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=1,product_id=11,sale_id=2,
            component_name='PART',quantity=1));self.session.commit()
        auto=await self.auto()
        rows=await self.product_lines()
        self.assertEqual(auto['_fulfillment_counts']['current'],1)
        self.assertEqual(auto['fulfillment_cost']['current'],D('2.4'))
        self.assertEqual(sum(row['current']['cost'] for row in rows),D('47.4'))
        self.assertIsNone(self.session.get(Sale,2).store_id)

    async def test_product_line_fulfillment_ytd_is_sum_of_monthly_allocations(self):
        self.add_august_product_line()
        rows={row['name']:row for row in await self.product_lines()}
        self.assertEqual(rows['P10 · 测试商品']['current']['cost'],D('47.4'))
        self.assertEqual(rows['P11 · Second']['previous']['cost'],D('1.6'))
        for row in rows.values():
            self.assertEqual(row['ytd']['cost'],row['current']['cost']+row['previous']['cost'])
        from io import BytesIO
        from openpyxl import Workbook, load_workbook
        from routers.reports import _build_product_line_profit_sheet
        book=Workbook()
        _build_product_line_profit_sheet(book,list(rows.values()),date(2026,9,1))
        data=BytesIO();book.save(data);data.seek(0)
        exported=load_workbook(data,data_only=True).worksheets[-1]
        exported_profit={row[0].value:row[9].value for row in exported.iter_rows(min_row=2)}
        self.assertEqual(exported_profit['P10 · 测试商品'],2.6)
        self.assertEqual(exported_profit['P11 · Second'],38.4)

    async def test_zero_revenue_month_keeps_its_fee_unallocated_in_ytd(self):
        self.add_august_product_line(zero_revenue=True)
        rows={row['name']:row for row in await self.product_lines()}
        unallocated=rows['未分配产品线（代发成本）']
        self.assertEqual(unallocated['previous']['cost'],D('1.6'))
        self.assertEqual(unallocated['ytd']['cost'],D('1.6'))
        self.assertEqual(rows['P10 · 测试商品']['ytd']['cost'],rows['P10 · 测试商品']['current']['cost'])

    async def test_missing_monthly_fee_does_not_pollute_other_month_product_line(self):
        self.add_august_product_line()
        async def rates(requirements):
            return {(currency,day):{'rate':D('.5') if currency=='USD' else D('.3'),'rate_date':day,'source':'fixture'}
                for currency,day in requirements if (currency,day)!=( 'MXN',date(2026,8,31))}
        with patch('services.report_exchange_rates.get_cny_rates',new=rates):
            rows={row['name']:row for row in await self.product_lines()}
        self.assertIsNone(rows['P11 · Second']['previous']['cost'])
        self.assertIsNone(rows['P11 · Second']['ytd']['cost'])
        self.assertEqual(rows['P10 · 测试商品']['ytd']['cost'],rows['P10 · 测试商品']['current']['cost'])
        self.assertEqual(rows['P10 · 测试商品']['ytd']['cost'],D('47.4'))

    async def test_missing_fee_rate_keeps_known_zero_product_line_share_zero(self):
        self.add_august_product_line(zero_revenue=True)
        self.session.get(Sale,2).sold_at=datetime(2026,9,20)
        self.session.get(SettlementEntry,6).settlement_date=datetime(2026,9,20)
        self.session.commit()
        async def rates(requirements):
            return {(currency,day):{'rate':D('.5'),'rate_date':day,'source':'fixture'}
                    for currency,day in requirements if currency!='MXN'}
        with patch('services.report_exchange_rates.get_cny_rates',new=rates):
            rows={row['name']:row for row in await self.product_lines()}
        self.assertIsNone(rows['P10 · 测试商品']['current']['cost'])
        self.assertEqual(rows['P11 · Second']['current']['cost'],D(0))
        self.assertEqual(rows['P11 · Second']['ytd']['cost'],D(0))

    async def test_each_month_uses_own_month_end_and_keeps_saved_daily_fx(self):
        self.session.add(Sale(id=2,product_id=10,user_id=1,store_id=7,order_no='AUG',quantity=1,
            selling_price=100,total_cost=0,platform_fee=0,profit=100,sold_at=datetime(2026,8,20)))
        self.session.flush()
        entry=self.entry(6,net=100,total=100,quantity=1,currency='USD',rate='.01',occurred=datetime(2026,8,20));entry.sale_id=2
        self.session.commit()
        auto=await self.auto();values=_build_store_profit_loss_values(auto,{})
        self.assertEqual(auto['product_sales_revenue'],{'current':D(50),'previous':D(40),'ytd':D(90)})
        self.assertEqual(auto['fulfillment_cost'],{'current':D('2.4'),'previous':D('1.6'),'ytd':D(4)})
        self.assertEqual(values['net_profit']['ytd'],D(41))
        self.assertEqual(self.session.get(SettlementEntry,1).exchange_rate_to_cny,D('.01'))
        self.assertEqual(self.session.get(Sale,1).total_cost,D(40))
        self.assertEqual({day for call in self.calls for _currency,day in call},{date(2026,8,31),date(2026,9,30)})

    async def test_bundle_and_compensation_do_not_add_fulfillment_orders(self):
        self.session.add(Product(id=11,sku='PART',name='组件'))
        self.session.flush()
        self.session.add(Sale(id=2,product_id=11,user_id=1,store_id=7,order_no='INTERNAL-OTHER-SKU',quantity=1,
            selling_price=25,total_cost=0,platform_fee=0,profit=25,sold_at=datetime(2026,9,10)))
        self.session.flush()
        self.session.add(SettlementEntryAllocation(settlement_entry_id=1,product_id=11,sale_id=2,component_name='PART',quantity=1))
        self.entry(6,net=0,total=10,quantity=0,kind='物流赔付',currency='USD',rate='.01')
        self.session.commit()
        auto=await self.auto()
        self.assertEqual(auto['_fulfillment_counts']['current'],1)
        self.assertEqual(auto['fulfillment_cost']['current'],D('2.4'))
        self.assertEqual(auto['product_sales_revenue']['current'],D(50))
        self.assertEqual(auto['compensation_revenue']['current'],D(5))

    async def test_true_manual_ytd_includes_non_operating_items_and_keeps_auto_fulfillment(self):
        august=StoreProfitLossReport(store_id=7,report_month=date(2026,8,1),manual_values={
            'advertising':{'current':'7'},'sample_expenses':{'current':'3'},
            'non_operating_income':{'current':'40'},'non_operating_expense':{'current':'15'},
            'income_tax_expense':{'current':'3'}},remarks={})
        september=StoreProfitLossReport(store_id=7,report_month=date(2026,9,1),manual_values={
            'advertising':{'current':'11','ytd':'9999'},'sample_expenses':{'current':'5'},
            'non_operating_income':{'current':'100','ytd':'9999'},'non_operating_expense':{'current':'20'},
            'income_tax_expense':{'current':'9'},'fulfillment_cost':{'current':'123'}},remarks={})
        self.session.add_all([august,september]);self.session.commit()
        response=await _store_profit_loss_response(self.db,NS(role='admin'),self.session.get(Store,7),date(2026,9,1),september)
        rows={r.key:r for r in response.rows}
        self.assertEqual(rows['advertising'].value.ytd,D(18))
        self.assertEqual(rows['sample_expenses'].value.ytd,D(8))
        self.assertEqual(rows['selling_expenses'].value.ytd,D(26))
        for key, amounts in (('non_operating_income', (100, 40, 140)),
                             ('non_operating_expense', (20, 15, 35)),
                             ('income_tax_expense', (9, 3, 12))):
            self.assertEqual(rows[key].kind, 'manual')
            self.assertFalse(rows[key].is_reserved)
            self.assertEqual(rows[key].value.model_dump(), dict(zip(('current', 'previous', 'ytd'), map(D, amounts))))
        self.assertTrue(rows['net_profit'].is_formula)
        for period, adjustment in (('current', 71), ('previous', 22), ('ytd', 93)):
            self.assertEqual(getattr(rows['net_profit'].value, period),
                             getattr(rows['operating_profit'].value, period) + D(adjustment))
        self.assertEqual(rows['fulfillment_cost'].value.current,D('2.4'))
        self.assertEqual(september.manual_values['advertising']['ytd'],'9999')
        self.assertEqual(september.manual_values['fulfillment_cost']['current'],'123')
        self.assertTrue(response.exchange_rates)

    async def test_save_accepts_net_profit_inputs_and_keeps_omitted_legacy_values(self):
        saved = {'income_tax_expense': {'current': '9'}, 'fulfillment_cost': {'current': '123'}}
        row=StoreProfitLossReport(store_id=7,report_month=date(2026,9,1),manual_values=saved,remarks={})
        self.session.add(row);self.session.commit()
        for scope in ('current','all'):
            with self.subTest(scope=scope):
                row.manual_values = saved
                self.session.commit()
                options = dict(store_id=7, report_month=date(2026, 9, 1), update_scope=scope)
                db = Facade(self.session)
                user = NS(id=1, role='operator', store_id=7)
                original_tax = row.manual_values['income_tax_expense']
                request = StoreProfitLossUpdateRequest(**options, manual_values={'sample_expenses':{'current':'5'}})
                await update_store_profit_loss(request, db, user)
                self.assertEqual(row.manual_values['income_tax_expense'], original_tax)

                request = StoreProfitLossUpdateRequest(**options, manual_values={
                    'non_operating_income':{'current':'100'}, 'non_operating_expense':{'current':'20'},
                    'income_tax_expense':{'current':'0'}, 'net_profit':{'current':'9999'},
                })
                result = await update_store_profit_loss(request, db, user)
                rows = {item.key: item for item in result.rows}
                self.assertEqual(rows['income_tax_expense'].manual_value.current, D(0))
                self.assertEqual(rows['net_profit'].value.current, rows['operating_profit'].value.current + D(80))
                self.assertNotIn('net_profit', row.manual_values)

                request = StoreProfitLossUpdateRequest(**options, manual_values={'income_tax_expense':{'current':None}})
                await update_store_profit_loss(request, db, user)
                self.assertIsNone(row.manual_values['income_tax_expense']['current'])
                self.assertEqual(row.manual_values['non_operating_income']['current'], '100')
                self.assertEqual(row.manual_values['non_operating_expense']['current'], '20')
                self.assertEqual(row.manual_values['fulfillment_cost'], {'current':'123'})

    async def test_detail_and_summary_exports_agree_after_manual_save_clear_and_zero(self):
        from io import BytesIO
        from openpyxl import load_workbook
        from routers.reports import (FINANCIAL_ATTACHMENT_SHEET, STORE_PROFIT_LOSS_SUMMARY_SHEET,
                                     export_financial_attachments, get_store_profit_loss)

        db = Facade(self.session)
        user = NS(id=1, role='admin', store_id=None)
        month = date(2026, 9, 1)
        periods = ('current', 'previous', 'ytd')
        manual_keys = ('non_operating_income', 'non_operating_expense', 'income_tax_expense')

        async def save(report_month, current_values):
            request = StoreProfitLossUpdateRequest(store_id=7, report_month=report_month, update_scope='current',
                manual_values={key: {'current': value} for key, value in current_values.items()})
            await update_store_profit_loss(request, db, user)

        await save(date(2026, 8, 1), dict(zip(manual_keys, ('40', '15', '3'))))
        cases = (
            (dict(zip(manual_keys, ('100.25', '20.10', '9.05'))), ('71.10', '22', '93.10')),
            ({'non_operating_income': None}, ('-29.15', '22', '-7.15')),
            ({'income_tax_expense': '0'}, ('-20.10', '22', '1.90')),
        )
        for changes, adjustments in cases:
            with self.subTest(changes=changes):
                await save(month, changes)
                report = await get_store_profit_loss(report_month=month, store_id=7, db=db, user=user)
                rows = {row.key: row for row in report.rows}
                for key, amount in changes.items():
                    self.assertEqual(rows[key].manual_value.current, None if amount is None else D(amount))
                for period, adjustment in zip(periods, adjustments):
                    self.assertEqual(getattr(rows['net_profit'].value, period),
                                     getattr(rows['operating_profit'].value, period) + D(adjustment))

                export = await export_financial_attachments(report_date=date(2026, 9, 30), report_month=month,
                                                             store_id=7, db=db, user=user)
                content = b''.join([chunk async for chunk in export.body_iterator])
                book = load_workbook(BytesIO(content), data_only=True)
                try:
                    detail = book[FINANCIAL_ATTACHMENT_SHEET]
                    summary = book[STORE_PROFIT_LOSS_SUMMARY_SHEET]
                    store_column = next(cell.column for cell in summary[1] if cell.value == report.store_name)
                    for number, row in enumerate(report.rows, start=2):
                        if row.key in (*manual_keys, 'net_profit'):
                            self.assertEqual(detail.cell(number, 1).value, row.label)
                            self.assertEqual(summary.cell(number, 1).value, row.label)
                            for column, period in enumerate(periods, start=2):
                                amount = getattr(row.value, period)
                                self.assertAlmostEqual(detail.cell(number, column).value,
                                                       None if amount is None else float(amount), places=10)
                            self.assertAlmostEqual(summary.cell(number, store_column).value,
                                                   float(row.value.current or 0), places=10)
                finally:
                    book.close()

    async def test_old_ledger_fulfillment_selection_does_not_override_or_break_auto_fee(self):
        self.session.add(FinancialExpenseSource(store_id=7,report_month=date(2026,9,1),
            category='fulfillment_cost',mode='ledger',updated_by=1))
        self.session.commit()
        with patch.dict(os.environ,{'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            response=await _store_profit_loss_response(self.db,NS(role='admin'),self.session.get(Store,7),date(2026,9,1),None)
        fee=next(row for row in response.rows if row.key=='fulfillment_cost')
        self.assertEqual(fee.value.current,D('2.4'))

    async def test_missing_month_end_does_not_fall_back_to_saved_daily_rate(self):
        with patch('services.report_exchange_rates.get_cny_rates',new=AsyncMock(side_effect=ExchangeRateError('missing'))):
            auto=await self.auto()
        values=_build_store_profit_loss_values(auto,{})
        self.assertIsNone(auto['product_sales_revenue']['current'])
        self.assertIsNone(auto['fulfillment_cost']['current'])
        self.assertEqual(auto['product_purchase_cost']['current'],D(40))
        self.assertIsNone(values['net_profit']['current'])
        self.assertEqual(self.session.get(SettlementEntry,1).exchange_rate_to_cny,D('.01'))

    async def test_legacy_compensation_linked_to_sale_is_not_fulfillment(self):
        self.session.get(SettlementEntry,1).transaction_type='物流赔付'
        self.session.commit()
        auto=await self.auto()
        self.assertEqual(auto['_fulfillment_counts']['current'],0)
        self.assertEqual(auto['fulfillment_cost']['current'],D(0))

    async def test_january_ytd_excludes_december_but_previous_keeps_own_rate(self):
        for key, occurred in ((2,datetime(2025,12,20)),(3,datetime(2026,1,20))):
            self.session.add(Sale(id=key,product_id=10,user_id=1,store_id=7,order_no=f'YEAR-{key}',quantity=1,
                selling_price=100,total_cost=0,platform_fee=0,profit=100,sold_at=occurred))
            self.session.flush()
            entry=self.entry(key+6,net=100,total=100,quantity=1,currency='USD',rate='.01',occurred=occurred)
            entry.sale_id=key
        self.session.commit()
        async def rates(requirements):
            return {(currency,day):{'rate':D('.8') if day.year==2025 else D('.2'),
                'rate_date':day,'source':'year-fixture'} for currency,day in requirements}
        with patch('services.report_exchange_rates.get_cny_rates',new=rates):
            auto=await _store_profit_loss_auto_values(self.db,7,date(2026,1,1))
        self.assertEqual(auto['product_sales_revenue'],{'current':D(20),'previous':D(80),'ytd':D(20)})

    async def test_zero_revenue_shipment_keeps_fulfillment_in_unallocated_product_line(self):
        from routers.reports import _product_line_profit_data
        entry=self.session.get(SettlementEntry,1);entry.net_product_sales=0;entry.settlement_total=0
        sale=self.session.get(Sale,1);sale.selling_price=0;sale.platform_fee=0
        self.session.commit()
        with patch('routers.reports._sale_provision_adjustments',new=AsyncMock(return_value={})), \
             patch('routers.reports._load_inventory_provision_data',new=AsyncMock(return_value=([],[],None))):
            lines=await _product_line_profit_data(self.db,{7},date(2026,9,1),date(2026,9,30))
        fee=next(row for row in lines if row['name']=='未分配产品线（代发成本）')
        self.assertEqual(fee['current']['cost'],D('2.4'))
