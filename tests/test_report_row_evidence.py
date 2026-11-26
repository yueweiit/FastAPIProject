"""07/12真实SQLite取数、同次查询依据、离线版本和权限隔离回归。"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO
import json
import os
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from openpyxl import load_workbook
from sqlalchemy import event

from models import (Product, InventoryBatch, Sale, SaleCostDetail, SettlementEntry, StoreProfitLossReport,
                    BatchImpairmentRule, BatchImpairmentRuleStore, ProductImpairmentRule,
                    FinancialExpense, FinancialExpenseSource, InventoryCostAdjustment, InventoryCostAdjustmentLine)
from routers import reports as api
from services import report_row_evidence as evidence
from services.report_row_evidence_export import append_row_evidence, CALC, DETAIL, GUIDE, INVENTORY_COLUMNS, PRODUCT_COLUMNS
from services.financial_versions import collect_payload, version_workbook
from tests import test_profit_loss_evidence as previous

DAY = date(2026, 9, 30)


class ReportRowEvidenceTests(unittest.IsolatedAsyncioTestCase):
    setUp = previous.ProfitLossEvidenceTests.setUp
    tearDown = previous.ProfitLossEvidenceTests.tearDown
    entry = previous.ProfitLossEvidenceTests.entry
    request = previous.ProfitLossEvidenceTests.request
    report = previous.ProfitLossEvidenceTests.report
    business_state = previous.ProfitLossEvidenceTests.business_state

    async def inventory(self, store=None):
        return await api._load_inventory_impairment_report_rows(self.db, DAY, store, with_evidence=True)

    async def products(self, stores=None):
        return await api._product_line_profit_data(self.db, stores or {7}, self.month, DAY, with_evidence=True)

    def assert_sources(self, row):
        ev = row['_evidence']
        for key, field in ev['fields'].items():
            if field['value'] is None or field['inputs']:
                continue
            self.assertEqual(field['status'], 'complete', (key, field))
            total = sum((D(r['amount']) for r in ev['sources'].get(key, [])), D(0))
            self.assertAlmostEqual(total, D(field['value']), places=10, msg=key)

    async def test_evidence_does_not_change_amounts_or_write_business_data(self):
        before = self.business_state()
        statements = []
        def capture(_conn, _cursor, sql, *_): statements.append(sql.lstrip().split()[0].upper())
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            plain_inventory = await api._load_inventory_impairment_report_rows(self.db, DAY)
            plain_products = await api._product_line_profit_data(self.db, {7}, self.month, DAY)
            inventory, products = await self.inventory(), await self.products()
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(plain_inventory, [{k:v for k,v in r.items() if k != '_evidence'} for r in inventory])
        self.assertEqual(plain_products, [{k:v for k,v in r.items() if k != '_evidence'} for r in products])
        for row in inventory + products: self.assert_sources(row)
        self.assertTrue(all(s == 'SELECT' for s in statements), statements)
        self.assertEqual(before, self.business_state())

    async def test_inventory_replay_and_sku_sum_show_daily_accrual_and_saved_fifo(self):
        self.session.get(Product, 10).product_type = 'new'
        self.session.add(ProductImpairmentRule(product_id=10, product_type='new', safe_stock_quantity=0,
                                              effective_date=date(2026, 8, 1)))
        self.session.get(InventoryBatch, 1).arrived_at = datetime(2026, 8, 1)
        self.session.get(SaleCostDetail, 1).unit_cost = D('9.50')
        self.session.commit()
        rows = await self.inventory()
        row, ev = rows[0], rows[0]['_evidence']
        self.assert_sources(row)
        self.assertEqual(D(ev['fields']['sold_cost_amount']['value']), 38)
        self.assertGreater(len(ev['sources']['monthly_total_impairment_amount']), 1)
        self.assertGreater(D(ev['fields']['monthly_total_impairment_amount']['value']), D(ev['fields']['monthly_impairment_amount']['value']))
        operands = ev['fields']['impairment_amount']['inputs']
        calculated = sum(D(i['value']) * (-1 if i['operator'] == '−' else 1) for i in operands)
        self.assertEqual(calculated, row['impairment_amount'])
        summary = api._summarize_inventory_impairment_rows(rows)[0]
        self.assert_sources(summary)
        self.assertEqual(summary['_evidence']['fields']['book_value']['value'], ev['fields']['book_value']['value'])

    async def test_inventory_shared_scope_contains_no_foreign_order_or_source_ids(self):
        self.session.get(Sale, 1).store_id = 8
        self.session.get(Sale, 1).order_no = 'FOREIGN-ORDER-SECRET'
        self.session.get(SettlementEntry, 1).source_file = 'FOREIGN-FILE-SECRET.xlsx'
        self.session.commit()
        rows = await self.inventory()
        text = json.dumps(evidence.serial(rows), ensure_ascii=False)
        self.assertNotIn('FOREIGN-', text)
        self.assertNotIn('销售ID', text)
        self.assertNotIn('结算行ID', text)
        self.assertEqual(rows[0]['sold_quantity'], 4)
        for row in rows: self.assert_sources(row)

    async def test_scoped_inventory_transfers_rounding_and_owned_product_writeoff(self):
        self.session.get(InventoryBatch, 1).unit_cost = D('0.0137')
        self.session.get(Product, 10).product_type = 'new'
        self.session.add(ProductImpairmentRule(product_id=10, product_type='new', safe_stock_quantity=0, effective_date=self.month))
        self.session.add_all([BatchImpairmentRule(id=1, batch_id=1, effective_date=self.month, is_explicit=True),
                              BatchImpairmentRule(id=2, batch_id=1, effective_date=date(2026,9,15), is_explicit=True)])
        self.session.flush()
        self.session.add_all([BatchImpairmentRuleStore(rule_id=1, store_id=7), BatchImpairmentRuleStore(rule_id=1, store_id=8),
                              BatchImpairmentRuleStore(rule_id=2, store_id=7)])
        self.session.commit()
        row = (await self.inventory(7))[0]
        self.assert_sources(row)
        self.assertGreater(row['monthly_transfer_in_amount'], 0)
        self.assertEqual(row['_evidence']['fields']['original_inventory_amount']['inputs'], [])
        products = await self.products()
        for item in products: self.assert_sources(item)
        lines = [s for r in products for s in r['_evidence']['sources'].get('current_cost', []) if s['source'] == '显式归属准备转销份额抵减']
        self.assertTrue(lines)
        self.assertTrue(all(s['details']['授权归属店铺ID'] == 7 for s in lines))
        self.assertNotIn('销售ID', json.dumps(lines, ensure_ascii=False))

    async def test_product_monthly_rate_fulfillment_and_manual_allocation_operands(self):
        self.session.add(StoreProfitLossReport(store_id=7, report_month=self.month,
            manual_values={'shipping_revenue': {'current':'12'}, 'customs_import_tax': {'current':'6'}}))
        self.session.commit()
        rows = await self.products()
        for row in rows: self.assert_sources(row)
        ev = next(r['_evidence'] for r in rows if r['product_id'] == 10)
        allocations = [s for s in ev['sources']['current_cost'] if '分摊' in s['source']]
        self.assertTrue(allocations)
        for source in allocations:
            details = source['details']
            expected = D(details['待分摊合计']) * D(details['商品收入分子']) / D(details['店铺收入分母'])
            self.assertAlmostEqual(expected, D(source['amount']), places=12)
        fulfillment = next(s for s in allocations if s['source'] == '代发费按月收入分摊')
        self.assertEqual(fulfillment['details']['去重订单数'], len(fulfillment['details']['原订单号']))
        self.assertEqual(fulfillment['details']['报表汇率']['report_month'], '2026-09')
        self.assertNotIn('ORDER-5', str(ev))

    async def test_cross_store_original_source_redacted_in_page_and_excel(self):
        entry = self.session.get(SettlementEntry, 1)
        entry.store_id = 8; entry.source_file = 'CROSS-STORE-PRIVATE.xlsx'; entry.order_id = 'CROSS-STORE-ORDER'
        self.session.commit()
        rows = await self.products()
        ev = next(r['_evidence'] for r in rows if r['product_id'] == 10)
        self.assertEqual(ev['fields']['current_revenue']['status'], 'partial')
        self.assertEqual(ev['fields']['current_margin']['status'], 'partial')
        self.assertEqual(evidence.total_evidence(rows)['fields']['current_margin']['status'], 'partial')
        self.assertNotIn('CROSS-STORE-', json.dumps([r['_evidence'] for r in rows], ensure_ascii=False))
        self.assertIn('已隐藏', ev['warning'])
        from openpyxl import Workbook
        book = Workbook(); api._build_product_line_profit_sheet(book, rows, self.month)
        append_row_evidence(book, product_rows=rows)
        self.assertFalse(any('CROSS-STORE-' in str(c.value) for sheet in book for row in sheet for c in row))

    async def test_snapshot_is_user_bound_revalidates_role_and_store_and_expires(self):
        response = await self.request('/reports/product-line-profit?report_month=2026-09-01&include_evidence=true')
        self.assertEqual(response.status_code, 200)
        meta = response.json()[0]['evidence']
        path = f"/reports/row-evidence/{meta['token']}?row_key={meta['row_key']}&field=current_revenue"
        first = await self.request(path)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['calculation_id'], meta['calculation_id'])
        self.session.get(Sale, 1).selling_price = 999
        self.session.commit()
        self.assertEqual((await self.request(path)).json(), first.json())
        original_user = self.user
        self.user = NS(id=999, role='admin', store_id=7)
        self.assertEqual((await self.request(path)).status_code, 403)
        self.user = NS(id=original_user.id, role='operator', store_id=8)
        self.assertEqual((await self.request(path)).status_code, 403)
        self.user = original_user
        self.assertEqual((await self.request(path + '&page=0')).status_code, 422)
        self.assertEqual((await self.request(path + '&page_size=101')).status_code, 422)
        self.assertEqual((await self.request(path.replace('current_revenue', 'no_such_field'))).status_code, 404)
        evidence.CACHE.items.pop(meta['token'])
        self.assertEqual((await self.request(path)).status_code, 410)

    async def test_admin_all_store_snapshot_denied_after_demotion_and_07_keeps_shared_scope(self):
        self.user = NS(id=1, role='admin', store_id=7)
        rows = (await self.request('/reports/product-line-profit?report_month=2026-09-01&include_evidence=true')).json()
        meta = rows[0]['evidence']
        self.user = NS(id=1, role='viewer', store_id=7)
        path = f"/reports/row-evidence/{meta['token']}?row_key={meta['row_key']}"
        self.assertEqual((await self.request(path)).status_code, 403)
        self.assertEqual((await self.request('/reports/product-line-profit?report_month=2026-09-01&store_id=8')).status_code, 403)
        row = (await self.request('/reports/inventory-impairment?report_date=2026-09-30&include_evidence=true')).json()[0]
        meta = row['evidence']
        self.user = NS(id=1, role='viewer', store_id=8)
        result = await self.request(f"/reports/row-evidence/{meta['token']}?row_key={meta['row_key']}&field=sold_cost_amount")
        self.assertEqual(result.status_code, 200)  # 原全局库存权限保留，只含匿名出库。
        self.assertNotIn('销售ID', str(result.json()))

    async def test_page_excel_values_links_cached_formulas_and_precision_match(self):
        inv, prod = await self.inventory(), await self.products()
        book, cache = api._build_inventory_impairment_workbook(inv, DAY)
        api._build_product_line_profit_sheet(book, prod, self.month)
        summary = api._summarize_inventory_impairment_rows(inv)
        api._build_inventory_impairment_summary_sheet(book, summary, DAY)
        append_row_evidence(book, inventory_rows=inv, summary_rows=summary, product_rows=prod)
        result = load_workbook(api._workbook_bytes_with_formula_cache(book, cache), data_only=True)
        for title, rows, columns in ((api.INVENTORY_IMPAIRMENT_SHEET, inv, INVENTORY_COLUMNS),
                                     (api.PRODUCT_LINE_PROFIT_SHEET, prod, PRODUCT_COLUMNS)):
            for number, row in enumerate(rows, 2):
                for column, key in columns.items():
                    cell = result[title].cell(number, column)
                    value = row['_evidence']['fields'][key]['value']
                    if value is None:
                        self.assertIsNone(cell.value); self.assertIsNone(cell.hyperlink); self.assertIn('第', cell.comment.text)
                        continue
                    self.assertAlmostEqual(cell.value, float(value), places=10, msg=key)
                    address = cell.hyperlink.target.split('!')[-1]
                    line = result[CALC][address].row
                    self.assertEqual(result[CALC].cell(line, 5).value, evidence.LABELS['zh-CN'][key])
                    self.assertAlmostEqual(result[CALC].cell(line, 6).value, float(value), places=10)
                    self.assertEqual(result[CALC].cell(line, 12).hyperlink.target, f"#'{title}'!A{number}")
        self.assertEqual(result.sheetnames[-3:], [GUIDE, CALC, DETAIL])
        main = result[api.PRODUCT_LINE_PROFIT_SHEET]
        totalrow = len(prod) + 2
        target = result[CALC][main.cell(totalrow, 6).hyperlink.target.split('!')[-1]].row
        self.assertIn('合计', result[CALC].cell(target, 3).value)
        self.assertEqual(result[CALC].cell(target, 8).value, '本月毛利 ÷ 本月收入')
        self.assertIsNotNone(result[CALC].cell(target, 13).hyperlink)
        self.assertTrue(any(r[10].hyperlink for r in result[DETAIL].iter_rows(min_row=2)))

    def assert_unknown_inventory_workbook(self, content, *, legacy=False):
        cached = load_workbook(BytesIO(content), data_only=True)
        formulas = load_workbook(BytesIO(content), data_only=False)
        sheet = cached[api.INVENTORY_IMPAIRMENT_SHEET]
        for address in ('O2', 'P2', 'Q2'):
            cell = sheet[address]
            self.assertIsNone(cell.value, address)
            self.assertIsNone(cell.hyperlink, address)
            self.assertIn(CALC, cell.comment.text)
            target = int(cell.comment.text.split('第')[1].split('行')[0])
            self.assertIsNone(cached[CALC].cell(target, 6).value)
            if legacy:
                self.assertIn('该版本未保存', cached[CALC].cell(target, 7).value)
        # 未知账面仍保留原公式；已知缓存值和已知0继续可点击，不能一概移除公式链接。
        self.assertEqual(formulas[api.INVENTORY_IMPAIRMENT_SHEET]['P2'].data_type, 'f')
        self.assertIn('M2-O2', formulas[api.INVENTORY_IMPAIRMENT_SHEET]['P2'].value)
        for address, expected in (('H2', 100), ('M2', 60), ('X2', 0)):
            self.assertEqual(sheet[address].value, expected)
            self.assertIsNotNone(sheet[address].hyperlink)
        if api.INVENTORY_IMPAIRMENT_SUMMARY_SHEET in cached.sheetnames:
            column = next(i for i, (key, _) in enumerate(api.INVENTORY_IMPAIRMENT_SUMMARY_COLUMNS, 1)
                          if key == 'book_value')
            cell = cached[api.INVENTORY_IMPAIRMENT_SUMMARY_SHEET].cell(2, column)
            self.assertIsNone(cell.value)
            self.assertIsNone(cell.hyperlink)
            self.assertIsNotNone(cell.comment)

    async def test_unknown_inventory_page_and_exports_keep_blank_formula_results(self):
        self.session.get(InventoryBatch, 1).arrived_at = datetime(2026, 9, 15)  # 销售早于入库，准备和账面待核对
        self.session.commit()
        before = self.business_state()
        for endpoint in ('/reports/inventory-impairment', '/reports/inventory-impairment/summary'):
            response = await self.request(endpoint + '?report_date=2026-09-30&include_evidence=true')
            self.assertEqual(response.status_code, 200)
            row = response.json()[0]
            self.assertIsNone(row['book_value'])
            meta = row['evidence']
            detail = await self.request(f"/reports/row-evidence/{meta['token']}?row_key={meta['row_key']}&field=book_value")
            self.assertIsNone(detail.json()['fields']['book_value']['value'])
        for path in ('/reports/inventory-impairment/export?report_date=2026-09-30',
                     '/reports/financial-attachments/export?report_date=2026-09-30&report_month=2026-09-01'):
            with self.subTest(path=path):
                response = await self.request(path)
                self.assertEqual(response.status_code, 200)
                self.assert_unknown_inventory_workbook(response.content)
        response = await self.request('/reports/inventory-impairment/export?report_date=2026-09-30&view=sku')
        book = load_workbook(BytesIO(response.content), data_only=True)
        column = next(i for i, (key, _) in enumerate(api.INVENTORY_IMPAIRMENT_SUMMARY_COLUMNS, 1) if key == 'book_value')
        self.assertIsNone(book[api.INVENTORY_IMPAIRMENT_SUMMARY_SHEET].cell(2, column).value)
        self.assertIsNone(book[api.INVENTORY_IMPAIRMENT_SUMMARY_SHEET].cell(2, column).hyperlink)
        self.assertEqual(before, self.business_state())

    async def test_unknown_inventory_new_and_legacy_versions_export_without_recalculation(self):
        self.session.get(InventoryBatch, 1).arrived_at = datetime(2026, 9, 15)
        self.session.commit()
        with patch.dict(os.environ, {'FIFO_FINANCIAL_CONTROLS_ENABLED': '1'}):
            payload = await collect_payload(self.db, self.user, self.store, self.month, oa_values=self.oa, active_store_count=2)
        # 当前事实已恢复正常，历史版本仍必须导出当时的未知值。
        self.session.get(InventoryBatch, 1).arrived_at = datetime(2026, 9, 1)
        self.session.commit()
        self.db.info.clear()
        statements = []
        def capture(_conn, _cursor, sql, *_): statements.append(sql)
        event.listen(self.engine, 'before_cursor_execute', capture)
        try:
            for schema in (evidence.VERSION, None, 'future_schema'):
                with self.subTest(schema=schema):
                    saved = deepcopy(payload)
                    for row in saved['inventory'] + saved['product_lines']:
                        if schema is None:
                            row.pop('_evidence', None)
                        else:
                            row['_evidence']['schema_version'] = schema
                    before = deepcopy(saved)
                    content = version_workbook(NS(payload=saved, report_month=self.month, version=1, status='needs_review')).getvalue()
                    self.assert_unknown_inventory_workbook(content, legacy=schema != evidence.VERSION)
                    self.assertEqual(saved, before)
        finally:
            event.remove(self.engine, 'before_cursor_execute', capture)
        self.assertEqual(statements, [])

    async def assert_offset_allocation_surfaces(self, fields, source_type):
        before = self.business_state()
        plain = await api._product_line_profit_data(self.db, {7}, self.month, DAY)
        rows = await self.products()
        self.assertEqual(plain, [{k:v for k,v in row.items() if k != '_evidence'} for row in rows])
        for row in rows: self.assert_sources(row)
        page = await self.request('/reports/product-line-profit?report_month=2026-09-01&include_evidence=true')
        item = next(row for row in page.json() if row['product_id'] == 10)
        meta = item['evidence']
        response = await self.request('/reports/financial-attachments/export?report_date=2026-09-30&report_month=2026-09-01')
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.content), data_only=True)
        self.assertNotIn('OTHER-STORE-OFFSET', str([list(r) for r in book[DETAIL].values]))
        for field in fields:
            detail = (await self.request(f"/reports/row-evidence/{meta['token']}?row_key={meta['row_key']}&field={field}")).json()
            self.assertEqual(detail['fields'][field]['status'], 'complete')
            sources = [s for s in detail['items'] if s['source'] == '店铺人工直接收入/费用分摊']
            self.assertEqual(len(sources), 1, field)
            source = sources[0]
            self.assertEqual(D(source['amount']), 0)
            vouchers = [r for r in source['details']['生效凭据'] if r['source'] == source_type]
            self.assertEqual(sorted(D(r['amount']) for r in vouchers), [D(-100), D(100)])
            self.assertEqual(D(source['details']['待分摊合计']), 0)
            self.assertNotIn('OTHER-STORE-OFFSET', str(detail))
            ident = f"{meta['calculation_id']}:{field}"
            exported = [r for r in book[DETAIL].iter_rows(values_only=True)
                        if r[0] == ident and r[5] == source['source']]
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0][7], 0)
            self.assertEqual(json.loads(exported[0][8]), source['details'])
            calculated = next(r for r in book[CALC].iter_rows(values_only=True) if r[0] == ident)
            self.assertAlmostEqual(calculated[5], float(detail['fields'][field]['value']), places=10)
            if field in item:
                self.assertAlmostEqual(float(item[field]), calculated[5], places=10)
        self.assertEqual(before, self.business_state())

    async def test_offset_manual_revenue_and_cost_sources_match_page_and_excel(self):
        self.session.add_all([
            StoreProfitLossReport(store_id=7, report_month=self.month, manual_values={
                'customs_import_tax': {'current':'100'}, 'local_logistics_cost': {'current':'-100'},
                'shipping_revenue': {'current':'100'}, 'other_revenue': {'current':'-100'}}),
            StoreProfitLossReport(store_id=8, report_month=self.month, manual_values={
                'customs_import_tax': {'current':'333'}, 'local_logistics_cost': {'current':'-333'}},
                remarks={'customs_import_tax':'OTHER-STORE-OFFSET'}),
        ])
        self.session.commit()
        await self.assert_offset_allocation_surfaces(('current_cost', 'ytd_cost', 'current_revenue', 'ytd_revenue'), 'manual')

    async def test_offset_ledger_vouchers_are_kept_when_category_total_is_zero(self):
        self.session.add(StoreProfitLossReport(store_id=7, report_month=self.month,
            manual_values={'customs_import_tax': {'current':'888'}}))
        self.session.add(FinancialExpenseSource(store_id=7, report_month=self.month,
            category='customs_import_tax', mode='ledger', updated_by=1))
        self.session.add_all([FinancialExpense(id=key, request_key=f'OFFSET-{key}', store_id=7,
            expense_date=self.month, category='customs_import_tax', source_ref=ref, amount=amount,
            amount_cny=amount, currency='CNY', exchange_rate=1, status='posted', created_by=1)
            for key, amount, ref in ((1, 100, 'TAX-POSTED'), (2, -100, 'TAX-REVERSAL'))])
        self.session.commit()
        with patch.dict(os.environ, {'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            await self.assert_offset_allocation_surfaces(('current_cost', 'ytd_cost'), 'ledger')
            row = next(r for r in await self.products() if r['product_id'] == 10)
            sources = row['_evidence']['sources']['current_cost']
            offset = next(s for s in sources if s['source'] == '店铺人工直接收入/费用分摊')
            self.assertNotIn('888', json.dumps(offset))
            self.assertEqual({r['reference'] for r in offset['details']['生效凭据'] if r['source'] == 'ledger'},
                             {'TAX-POSTED', 'TAX-REVERSAL'})

    def test_offset_sources_survive_zero_and_unknown_allocation_denominators(self):
        periods = ('current', 'previous', 'ytd')
        manual = {key: {p: amount for p in periods}
                  for key, amount in (('customs_import_tax', D(100)), ('local_logistics_cost', D(-100)))}
        for denominator, unknown in ((D(0), set()), (D(100), set(periods))):
            with self.subTest(denominator=denominator, unknown=bool(unknown)):
                lines = {10: {'note':'', **{p: {'revenue':D(100), 'cost':D(50)} for p in periods}}}
                api._allocate_store_profit_loss_to_product_lines(lines,
                    {7: {10: {p: denominator for p in periods}}}, {7: manual}, {7: unknown}, with_evidence=True)
                for period in periods:
                    self.assertEqual(lines[10][period], {'revenue':D(100), 'cost':D(50)})
                    self.assertNotIn(period, lines[10].get('_unknown_cost_periods', set()))
                    sources = lines[10].get('_source_rows', {}).get(f'{period}_cost', [])
                    self.assertEqual(len(sources), 1)
                    self.assertEqual(D(sources[0]['amount']), 0)
                    self.assertEqual({D(v) for v in sources[0]['details']['参与科目'].values() if v is not None},
                                     {D(100), D(-100)})
                    self.assertNotIn('分摊比例', sources[0]['details'])

    async def test_actual_cost_layers_are_explained_without_changing_saved_fifo(self):
        self.session.add(InventoryCostAdjustment(id=1, request_key='ADJ', source_ref='FREIGHT', category='head_shipping',
            allocation_basis='quantity', amount=20, amount_cny=20, currency='CNY', exchange_rate=1,
            rate_date=DAY, document_date=DAY, created_by=1))
        self.session.flush()
        self.session.add(InventoryCostAdjustmentLine(adjustment_id=1, batch_id=1, quantity=10, allocation_weight=1, amount_cny=20))
        self.session.commit()
        before = self.business_state()
        with patch.dict(os.environ, {'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            rows, products = await self.inventory(), await self.products()
        self.assert_sources(rows[0])
        self.assertEqual(D(rows[0]['_evidence']['fields']['unit_cost']['value']), 12)
        self.assertEqual(D(rows[0]['_evidence']['fields']['sold_cost_amount']['value']), 48)
        inputs = rows[0]['_evidence']['sources']['unit_cost'][0]['details']
        self.assertEqual(inputs['补差凭据'][0]['原凭据'], 'FREIGHT')
        for row in products: self.assert_sources(row)
        self.assertEqual(self.session.get(SaleCostDetail, 1).unit_cost, 10)
        self.assertEqual(before, self.business_state())

    async def test_pending_ledger_and_missing_rates_keep_unknown_sources(self):
        self.session.add(StoreProfitLossReport(store_id=7, report_month=self.month,
            manual_values={'customs_import_tax':{'current':'888'}}))
        self.session.add(FinancialExpenseSource(store_id=7, report_month=self.month, category='customs_import_tax', mode='ledger', updated_by=1))
        self.session.add(FinancialExpense(id=1, request_key='PENDING', store_id=7, expense_date=self.month,
            category='customs_import_tax', source_ref='TAX', amount=10, amount_cny=10, currency='CNY', exchange_rate=1, status='draft', created_by=1))
        self.session.commit()
        with patch.dict(os.environ, {'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            rows = await self.products()
            ev = next(r['_evidence'] for r in rows if r['product_id']==10)
            self.assertIsNone(ev['fields']['current_cost']['value'])
            source = next(s for s in ev['sources']['current_cost'] if s['source']=='分摊输入待确认')
            self.assertIsNone(source['amount'])
            self.assertEqual(source['details']['生效凭据'][0]['reference'], 'TAX')
            self.session.get(FinancialExpense,1).status='posted';self.session.commit()
            for row in await self.products(): self.assert_sources(row)
        self.unavailable_monthly_rates=True
        self.session.get(SettlementEntry,1).currency='USD';self.session.commit()
        self.db.info.clear()
        ev = next(r['_evidence'] for r in await self.products() if r['product_id']==10)
        self.assertIsNone(ev['fields']['current_revenue']['value'])
        source = next(s for s in ev['sources']['current_revenue'] if s['source']=='销售净商品收入')
        self.assertIsNone(source['details']['报表汇率']['rate_to_cny'])

    def test_monthly_fulfillment_ytd_keeps_original_month_weights_and_rates(self):
        periods = api._month_periods(self.month)
        august = date(2026,8,1)
        lines = {key:{'current_quantity':0, 'name':str(key), 'sku_ids':{key}, 'note':'',
                       **{p:{'revenue':D(0),'cost':D(0)} for p in periods}} for key in (10,11)}
        revenues = {7:{august:{10:D(90),11:D(10)}, self.month:{10:D(10),11:D(90)}}}
        orders = {august:{(7,'A')},self.month:{(7,'B'),(7,'C'),(7,'D')}}
        rates = {(7,'MXN',august):{'rate_to_cny':D('.5')}, (7,'MXN',self.month):{'rate_to_cny':D(1)}}
        api._allocate_monthly_fulfillment_cost(lines, revenues, orders, rates, periods, set(), with_evidence=True)
        self.assertEqual(lines[10]['ytd']['cost'], D(6))
        self.assertEqual(lines[11]['ytd']['cost'], D(22))
        for key, expected in ((10,D(6)), (11,D(22))):
            sources = lines[key]['_source_rows']['ytd_cost']
            self.assertEqual(sum(D(s['amount']) for s in sources), expected)
            self.assertEqual({s['day'] for s in sources}, {'2026-08-01','2026-09-01'})
            self.assertEqual({s['details']['报表汇率']['rate_to_cny'] for s in sources}, {'0.5','1'})

    def test_percentage_expression_preserves_exact_inputs_and_rounding(self):
        for gross, expected in (('1.0005','10.01%'),('-1.0005','-10.01%')):
            item = {'name':'比例', 'product_id':10, 'current_quantity':0,
                    **{p:{'revenue':D(10),'cost':D(10)-D(gross)} for p in ('current','previous','ytd')}}
            ev = evidence.product_evidence(item, self.month)
            text = ev['fields']['current_margin']['expression']
            self.assertIn(expected,text)
            self.assertIn(f'本月毛利 ({gross})',text)
            self.assertIn('本月收入 (10)',text)
        field = {'value':'1E+28', 'format':'percent', 'inputs':[{'label':'小额收入比例', 'operator':'+', 'value':'1E+28'}]}
        self.assertIn('1000000000000000000000000000000.00%', evidence.expression_text(field))

    async def test_unknown_inventory_and_zero_revenue_remain_blank(self):
        self.session.get(InventoryBatch, 1).arrived_at = datetime(2026,10,1)
        self.session.commit()
        rows = await self.inventory()
        self.assertIsNone(rows[0]['_evidence']['fields']['monthly_writeoff_amount']['value'])
        self.assertEqual(rows[0]['_evidence']['fields']['monthly_writeoff_amount']['status'], 'unknown')
        item = {'name':'零收入', 'product_id':10, 'current_quantity':0,
                **{p:{'revenue':D(0),'cost':D(8)} for p in ('current','previous','ytd')}}
        ev = evidence.product_evidence(item, self.month)
        self.assertIsNone(ev['fields']['current_margin']['value'])
        self.assertEqual([i['value'] for i in ev['fields']['current_margin']['inputs']], ['-8', '0'])

    async def test_new_and_legacy_versions_preserve_values_and_never_use_live_sources(self):
        with patch.dict(os.environ, {'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            payload = await collect_payload(self.db, self.user, self.store, self.month, oa_values=self.oa, active_store_count=2)
            again = await collect_payload(self.db, self.user, self.store, self.month, oa_values=self.oa, active_store_count=2)
        self.assertEqual(payload, again)
        self.session.get(SettlementEntry, 1).source_file = 'LATER-NOT-SAVED.xlsx'
        self.session.commit()
        for schema in (evidence.VERSION, None, 'future_schema'):
            saved = json.loads(json.dumps(payload, sort_keys=True))
            saved['inventory'].reverse(); saved['product_lines'].reverse()
            for row in saved['inventory'] + saved['product_lines']:
                if schema is None: row.pop('_evidence', None)
                elif schema != evidence.VERSION: row['_evidence']['schema_version'] = schema
            before = deepcopy(saved)
            book = load_workbook(version_workbook(NS(payload=saved, report_month=self.month, version=1, status='confirmed')), data_only=True)
            self.assertEqual(saved, before)
            self.assertNotIn('LATER-NOT-SAVED', str([list(r) for r in book[DETAIL].values]))
            for number, row in enumerate(saved['product_lines'], 2):
                cell = book[api.PRODUCT_LINE_PROFIT_SHEET].cell(number, 3)
                expected = row['current']['revenue']
                self.assertEqual(cell.value, float(expected) if expected is not None else None)
                target = book[CALC][cell.hyperlink.target.split('!')[-1]].row
                if schema == evidence.VERSION:
                    self.assertEqual(book[CALC].cell(target, 3).value, row['name'])
                else:
                    self.assertIn('该版本未保存', book[CALC].cell(target, 7).value)

    async def test_special_characters_and_long_source_details_are_literal_and_complete(self):
        self.session.get(SettlementEntry, 1).source_file = '=HYPERLINK("x") $& <img>\x00'
        self.session.commit()
        rows = await self.products()
        row = rows[0]
        record = row['_evidence']['sources']['current_revenue'][0]
        record['details']['长凭据'] = 'A' * 40000 + 'TAIL'
        from openpyxl import Workbook
        book = Workbook(); api._build_product_line_profit_sheet(book, rows, self.month)
        append_row_evidence(book, product_rows=rows)
        data = BytesIO(); book.save(data); data.seek(0)
        book = load_workbook(data)
        texts = ''.join(str(c.value or '') for r in book[DETAIL] for c in r)
        self.assertIn('TAIL', texts); self.assertIn('[U+0000]', texts); self.assertIn('$& <img>', texts)
        self.assertTrue(all(c.data_type != 'f' for r in book[DETAIL] for c in r))
        continuations = [r for r in book[DETAIL].iter_rows(min_row=2) if r[5].value == '详细依据续行（不重复计数）']
        self.assertTrue(continuations)
        self.assertTrue(all(r[7].value is None for r in continuations))


if __name__ == '__main__':
    unittest.main()
