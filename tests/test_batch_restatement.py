"""实际批次路由的已售成本更正；隔离SQLite，保留原FIFO和不可变月度版本。"""
import copy
import os
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, select

from auth import get_current_user
from database import get_db
from models import (InventoryBatch, InventoryCostAdjustment, InventoryCostAdjustmentLine,
                    BatchChangeLog, FinancialAuditEvent, Sale, SaleCostDetail, User)
from routers import batches, financial_control, reports
from schemas import FinancialVersionRequest, FinanceReversalRequest
from services.cost_layers import sale_cost_context
from services.financial_versions import save_version, compare_version
from services.financial_controls import reverse_cost_adjustment
from services.fifo import fifo_sell
from tests import test_financial_facts as facts
from tests.test_financial_controls import WritableFacade


class BatchRestatementTests(unittest.IsolatedAsyncioTestCase):
    entry = facts.FinancialFactsTests.entry
    zero_provisions = facts.FinancialFactsTests.zero_provisions

    def setUp(self):
        facts.FinancialFactsTests.setUp(self)
        self.flag = patch.dict(os.environ, {'FIFO_FINANCIAL_CONTROLS_ENABLED': '1'})
        self.flag.start()
        self.db = WritableFacade(self.session)
        self.actor = self.session.get(User, 1)
        self.app = FastAPI()
        self.app.include_router(batches.router)
        self.app.include_router(financial_control.router)
        async def actor(): return self.actor
        async def db():
            self.session.info.clear()
            try:
                yield self.db
            finally:
                self.session.rollback()
        self.app.dependency_overrides[get_current_user] = actor
        self.app.dependency_overrides[get_db] = db
        for name, value in (('_store_provision_financial_values', self.zero_provisions()),
                            ('office_space_totals_by_application_date', {}),
                            ('shared_admin_totals_by_application_date', {}),
                            ('china_salary_totals_by_store_and_application_date', {})):
            patcher = patch.object(reports, name, new=AsyncMock(return_value=value))
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.flag.stop()
        facts.FinancialFactsTests.tearDown(self)

    async def request(self, method, path, payload=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
            return await client.request(method, path, json=payload)

    async def opened(self):
        response = await self.request('GET', '/batches?page=1')
        self.assertEqual(response.status_code, 200, response.text)
        return next(row for row in response.json()['items'] if row['id'] == 1)

    async def save(self, **changes):
        row = await self.opened()
        costs = row['edit_values'] or row
        total = costs['total_amount']
        if total is None:
            total = str(Decimal(costs['purchase_price']) * row['quantity'])
        payload = {key: row[key] for key in ('product_id', 'quantity', 'edit_version', 'arrived_at', 'shipping_date')}
        payload.update({key: costs[key] for key in ('shipping_cost', 'last_mile_cost', 'other_cost')})
        payload['total_amount'] = total
        payload['cost_edit_mode'] = 'restate'
        payload.update(changes)
        return await self.request('PUT', '/batches/1', payload)

    def logs(self):
        return self.session.scalars(select(BatchChangeLog).order_by(BatchChangeLog.id)).all()

    def assert_same_cost_document(self, actual, expected):
        # 首次响应与从DECIMAL列读取的字符串小数位可不同，比较财务数值和单据身份。
        actual, expected = actual.copy(), expected.copy()
        for field in ('amount', 'amount_cny', 'exchange_rate'):
            actual[field], expected[field] = Decimal(actual[field]), Decimal(expected[field])
        self.assertEqual(actual, expected)

    async def test_sold_shipping_cost_restates_sale_month_without_rewriting_fifo(self):
        response = await self.save(shipping_cost='20')
        self.assertEqual(response.status_code, 200, response.text)
        batch = self.session.get(InventoryBatch, 1)
        self.assertEqual((batch.quantity, batch.remaining_quantity, batch.unit_cost), (10, 6, Decimal(10)))
        self.assertEqual(batch.cost_basis['quantity'], 10)
        self.assertEqual(self.session.get(SaleCostDetail, 1).unit_cost, Decimal(10))
        self.assertEqual(self.session.get(Sale, 1).total_cost, Decimal(40))
        context = await sale_cost_context(self.db, [self.session.get(Sale, 1)])
        self.assertEqual(context[1]['adjustment'], Decimal(8))
        values = await reports._store_profit_loss_auto_values(self.db, 7, date(2026, 9, 1))
        self.assertEqual(values['head_logistics_cost']['current'], Decimal(8))
        self.assertEqual(Decimal(response.json()['unit_cost']), Decimal(12))
        self.assertEqual(Decimal(response.json()['unit_cost']) * batch.remaining_quantity, Decimal(72))
        self.assertEqual(self.logs()[0].after_values['shipping_cost'], '20.0000')

    async def test_quantity_change_keeps_sold_count_and_frozen_cost_basis(self):
        response = await self.save(quantity=12)
        self.assertEqual(response.status_code, 200, response.text)
        batch = self.session.get(InventoryBatch, 1)
        self.assertEqual((batch.quantity, batch.remaining_quantity), (12, 8))
        self.assertEqual(batch.cost_basis['quantity'], 10)
        self.assertEqual(batch.unit_cost, Decimal(10))
        effective = Decimal(response.json()['unit_cost'])
        self.assertEqual(effective.quantize(Decimal('.0001')), Decimal('8.3333'))
        second = await self.save(quantity=4)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual((batch.quantity, batch.remaining_quantity), (4, 0))
        self.assertEqual(batch.cost_basis['quantity'], 10)
        self.assertEqual(Decimal(second.json()['unit_cost']).quantize(Decimal('.0001')), Decimal(25))
        self.assertEqual(self.session.get(SaleCostDetail, 1).quantity, 4)
        self.assertEqual(self.session.get(SaleCostDetail, 1).unit_cost, Decimal(10))

    async def test_quantity_and_total_change_keeps_zero_difference_sources_for_review(self):
        response = await self.save(quantity=20, total_amount='200')
        self.assertEqual(response.status_code, 200, response.text)
        headers = self.session.scalars(select(InventoryCostAdjustment)).all()
        self.assertEqual(len(headers), 4)
        self.assertTrue(all(row.amount_cny == 0 and not row.is_final for row in headers))
        self.assertEqual(Decimal(response.json()['unit_cost']), Decimal(10))
        self.assertEqual(response.json()['cost_status'], 'unverified')

    async def test_below_sold_quantity_is_rejected(self):
        response = await self.save(quantity=3)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['detail']['code'], 'batch_quantity_below_sold')
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 6)
        self.assertEqual(self.logs(), [])

    async def test_arrival_after_existing_sale_and_later_ticket_edit_are_allowed(self):
        original_sale = self.session.get(Sale, 1)
        original_detail = self.session.get(SaleCostDetail, 1)
        sale_date = original_sale.sold_at
        fifo_unit_cost = original_detail.unit_cost

        response = await self.save(arrived_at='2026-09-11T12:00:00')
        self.assertEqual(response.status_code, 200, response.text)
        batch = self.session.get(InventoryBatch, 1)
        self.assertGreater(batch.arrived_at.date(), sale_date.date())
        self.assertEqual((batch.quantity, batch.remaining_quantity), (10, 6))
        self.assertEqual(original_detail.unit_cost, fifo_unit_cost)
        self.assertEqual(self.logs()[0].after_values['arrived_at'][:10], '2026-09-11')

        response = await self.save(shipping_cost_no='CORRECTED-TICKET')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(batch.shipping_cost_no, 'CORRECTED-TICKET')
        self.assertEqual(len(self.logs()), 2)
        self.assertEqual(self.session.scalars(select(InventoryCostAdjustment)).all(), [])

    async def test_cost_restatement_still_works_after_arrival_moved_past_sale(self):
        changed_date = await self.save(arrived_at='2026-09-11T12:00:00')
        self.assertEqual(changed_date.status_code, 200, changed_date.text)
        response = await self.save(shipping_cost='20')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 6)
        self.assertEqual(self.session.get(SaleCostDetail, 1).unit_cost, Decimal(10))
        context = await sale_cost_context(self.db, [self.session.get(Sale, 1)])
        self.assertEqual(context[1]['adjustment'], Decimal(8))

    async def test_fully_sold_batch_can_edit_without_resurrecting_inventory(self):
        batch = self.session.get(InventoryBatch, 1)
        batch.remaining_quantity = 0
        self.session.get(SaleCostDetail, 1).quantity = 10
        self.session.get(Sale, 1).quantity = 10
        self.session.commit()
        response = await self.save(shipping_cost='20', dingtalk_order_no='CORRECTED')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(batch.remaining_quantity, 0)
        self.assertEqual(batch.dingtalk_order_no, 'CORRECTED')
        self.assertEqual((await self.request('DELETE', '/batches/1')).status_code, 400)

    async def test_other_operator_can_restate_but_viewer_cannot(self):
        self.session.add(User(id=2, username='second', role='operator', store_id=7, password_hash='test'))
        self.session.commit()
        self.actor = self.session.get(User, 2)
        response = await self.save(shipping_cost='20', dingtalk_order_no='OTHER-EDITOR')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.session.get(InventoryBatch, 1).user_id, 1)
        self.assertEqual(self.logs()[0].actor_id, 2)
        self.actor.role = 'viewer'
        self.session.commit()
        self.assertEqual((await self.save(shipping_cost='30')).status_code, 403)

    async def test_repeated_edit_and_stale_form_do_not_duplicate_adjustments(self):
        opened = await self.opened()
        first = await self.save(shipping_cost='20')
        self.assertEqual(first.status_code, 200, first.text)
        stale = await self.save(shipping_cost='30', edit_version=opened['edit_version'])
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(len(self.session.scalars(select(InventoryCostAdjustment)).all()), 1)
        second = await self.save(shipping_cost='30')
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(self.session.scalars(select(InventoryCostAdjustment)).all()), 2)
        self.assertEqual(sum(self.session.scalars(select(InventoryCostAdjustmentLine.amount_cny)).all()), Decimal(30))
        self.assertEqual(len(self.logs()), 2)
        self.assertEqual(self.logs()[1].before_values, self.logs()[0].after_values)

    async def test_cost_decrease_and_zero_target_do_not_make_component_negative(self):
        first = await self.save(shipping_cost='20', other_cost='10')
        self.assertEqual(first.status_code, 200, first.text)
        second = await self.save(total_amount='0', shipping_cost='0', other_cost='0')
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(Decimal(second.json()['unit_cost']), Decimal(0))
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 6)
        self.assertTrue(all(Decimal(value) >= 0 for value in second.json()['edit_values'].values()))

    async def test_nondivisible_cost_can_be_cleared_and_saved_again(self):
        batch = self.session.get(InventoryBatch, 1)
        batch.quantity, batch.remaining_quantity = 7, 3
        batch.purchase_price, batch.total_amount = Decimal('115.1586'), Decimal('806.11')
        batch.shipping_cost, batch.last_mile_cost = Decimal('778.68'), Decimal('664.63')
        batch.other_cost, batch.unit_cost = Decimal('503.95'), Decimal('393.3386')
        self.session.get(SaleCostDetail, 1).unit_cost = batch.unit_cost
        self.session.commit()

        response = await self.save(last_mile_cost='0')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(Decimal(response.json()['edit_values']['last_mile_cost']), 0)
        self.assertEqual((batch.quantity, batch.remaining_quantity), (7, 3))
        self.assertEqual(self.session.get(SaleCostDetail, 1).unit_cost, Decimal('393.3386'))
        self.assertEqual(self.logs()[0].after_values['last_mile_cost'], '0.0000')

        # 再次保存及零额核对也不能被此前除法留下的极小负尾差挡住。
        again = await self.save(dingtalk_order_no='ZERO-COST-CHECKED')
        self.assertEqual(again.status_code, 200, again.text)
        payload = {'request_key': 'zero-after-nondivisible', 'source_ref': 'zero-tail-check',
                   'category': 'last_mile', 'amount': '0', 'rate_date': '2026-09-01',
                   'document_date': '2026-09-01', 'allocations': [{'batch_id': 1}]}
        checked = await self.request('POST', '/financial-control/cost-adjustments', payload)
        self.assertEqual(checked.status_code, 200, checked.text)
        count = len(self.session.scalars(select(InventoryCostAdjustment)).all())
        payload.update(request_key='negative-after-zero', source_ref='negative-tail-check', amount='-0.0001')
        rejected = await self.request('POST', '/financial-control/cost-adjustments', payload)
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertEqual(rejected.json()['detail']['code'], 'negative_component_cost')
        self.assertEqual(len(self.session.scalars(select(InventoryCostAdjustment)).all()), count)

    async def test_cost_retry_after_quantity_edit_keeps_original_allocation_and_audit(self):
        payload = {'request_key': 'retry-before-quantity', 'source_ref': 'freight-before-quantity',
                   'category': 'head_shipping', 'amount': '200', 'rate_date': '2026-09-01',
                   'document_date': '2026-09-01', 'allocations': [{'batch_id': 1}]}
        original = await self.request('POST', '/financial-control/cost-adjustments', payload)
        self.assertEqual(original.status_code, 200, original.text)
        row_id = original.json()['id']
        response = await self.save(quantity=12)
        self.assertEqual(response.status_code, 200, response.text)
        allocations = self.session.scalars(select(InventoryCostAdjustmentLine).where(
            InventoryCostAdjustmentLine.adjustment_id == row_id)).all()
        self.assertEqual([(x.quantity, x.allocation_weight, x.amount_cny) for x in allocations],
                         [(10, Decimal(10), Decimal(200))])
        counts = {model: len(self.session.scalars(select(model)).all()) for model in
                  (InventoryCostAdjustment, InventoryCostAdjustmentLine, FinancialAuditEvent, BatchChangeLog)}
        before = await self.opened()
        repeated = await self.request('POST', '/financial-control/cost-adjustments', copy.deepcopy(payload))
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assert_same_cost_document(repeated.json(), original.json())
        self.assertEqual(await self.opened(), before)
        for model, count in counts.items():
            self.assertEqual(len(self.session.scalars(select(model)).all()), count)
        self.assertEqual([(x.quantity, x.allocation_weight, x.amount_cny) for x in allocations],
                         [(10, Decimal(10), Decimal(200))])

        # 真正改变请求内容或批次集合仍冲突，不能借重试绕过原创建人权限。
        self.session.add(InventoryBatch(product_id=10, batch_no='RETRY-OTHER', quantity=5,
            remaining_quantity=5, purchase_price=10, unit_cost=10, user_id=1,
            arrived_at=self.session.get(InventoryBatch, 1).arrived_at))
        self.session.commit()
        second_id = self.session.scalar(select(InventoryBatch.id).where(InventoryBatch.batch_no == 'RETRY-OTHER'))
        for change in ({'amount': '201'}, {'note': 'changed'}, {'allocation_basis': 'weight',
                       'allocations': [{'batch_id': 1, 'weight': '10'}]},
                       {'allocations': [{'batch_id': 1}, {'batch_id': second_id}]}):
            with self.subTest(change=change):
                rejected = await self.request('POST', '/financial-control/cost-adjustments', {**payload, **change})
                self.assertEqual(rejected.status_code, 409, rejected.text)
                self.assertEqual(rejected.json()['detail']['code'], 'idempotency_conflict')
        self.session.add(User(id=2, username='retry-other-operator', password_hash='test', role='operator', store_id=7))
        self.session.commit()
        self.actor = self.session.get(User, 2)
        denied = await self.request('POST', '/financial-control/cost-adjustments', payload)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()['detail']['code'], 'batch_forbidden')
        for model, count in counts.items():
            self.assertEqual(len(self.session.scalars(select(model)).all()), count)

    async def test_explicit_cost_weight_still_matters_after_quantity_edit(self):
        for index, basis in enumerate(('weight', 'volume', 'value')):
            with self.subTest(basis=basis):
                payload = {'request_key': f'retry-explicit-{basis}', 'source_ref': f'explicit-{basis}',
                           'category': 'head_shipping', 'amount': '20', 'allocation_basis': basis,
                           'rate_date': '2026-09-01', 'document_date': '2026-09-01',
                           'allocations': [{'batch_id': 1, 'weight': '5'}]}
                original = await self.request('POST', '/financial-control/cost-adjustments', payload)
                self.assertEqual(original.status_code, 200, original.text)
                changed = await self.save(quantity=12 + index)
                self.assertEqual(changed.status_code, 200, changed.text)
                repeat = await self.request('POST', '/financial-control/cost-adjustments', payload)
                self.assertEqual(repeat.status_code, 200, repeat.text)
                self.assert_same_cost_document(repeat.json(), original.json())
                rejected = await self.request('POST', '/financial-control/cost-adjustments',
                    {**payload, 'allocations': [{'batch_id': 1, 'weight': '6'}]})
                self.assertEqual(rejected.status_code, 409, rejected.text)
                self.assertEqual(rejected.json()['detail']['code'], 'idempotency_conflict')

    async def test_reversal_after_quantity_edit_uses_original_allocation_quantity(self):
        self.assertEqual((await self.save(shipping_cost='20')).status_code, 200)
        original = self.session.scalar(select(InventoryCostAdjustment))
        self.assertEqual((await self.save(quantity=20)).status_code, 200)
        # 当前每件头程仅1元；原凭证按10件分配，每件冲回2元，应拒绝负成本。
        with self.assertRaises(HTTPException) as failure:
            await reverse_cost_adjustment(self.db, self.actor, original.id,
                FinanceReversalRequest(request_key='reverse-after-quantity', note='更正原运费'))
        self.assertEqual(failure.exception.detail['code'], 'negative_component_cost')

    async def test_reversal_cannot_round_away_negative_cost_after_quantity_increase(self):
        batch = self.session.get(InventoryBatch, 1)
        batch.quantity, batch.remaining_quantity = 1, 0
        self.session.get(SaleCostDetail, 1).quantity = 1
        self.session.get(Sale, 1).quantity = 1
        self.session.commit()
        payload = {'request_key': 'small-freight-before-quantity', 'source_ref': 'small-freight',
                   'category': 'head_shipping', 'amount': '0.0001', 'rate_date': '2026-09-01',
                   'document_date': '2026-09-01', 'allocations': [{'batch_id': 1}]}
        original = await self.request('POST', '/financial-control/cost-adjustments', payload)
        self.assertEqual(original.status_code, 200, original.text)
        changed = await self.save(quantity=10, shipping_cost='0.0008')
        self.assertEqual(changed.status_code, 200, changed.text)
        count = len(self.session.scalars(select(InventoryCostAdjustment)).all())
        rejected = await self.request('POST', f"/financial-control/cost-adjustments/{original.json()['id']}/reverse",
            {'request_key': 'reverse-small-freight', 'note': '核对原运费'})
        # 当前每件0.00008，冲回原每件0.0001将使当前10件合计为-0.0002，必须拒绝。
        self.assertEqual(rejected.status_code, 400, rejected.text)
        self.assertEqual(rejected.json()['detail']['code'], 'negative_component_cost')
        self.assertEqual(len(self.session.scalars(select(InventoryCostAdjustment)).all()), count)

    async def test_log_failure_rolls_back_quantity_basis_adjustments_and_audit(self):
        def reject(_mapper, _connection, _row): raise RuntimeError('reject batch log')
        event.listen(BatchChangeLog, 'before_insert', reject)
        try:
            with self.assertRaisesRegex(RuntimeError, 'reject batch log'):
                await self.save(quantity=12, shipping_cost='20')
        finally:
            event.remove(BatchChangeLog, 'before_insert', reject)
        batch = self.session.get(InventoryBatch, 1)
        self.assertEqual((batch.quantity, batch.remaining_quantity), (10, 6))
        self.assertIsNone(batch.cost_basis)
        for model in (InventoryCostAdjustment, InventoryCostAdjustmentLine, FinancialAuditEvent, BatchChangeLog):
            self.assertEqual(self.session.scalars(select(model)).all(), [])

    async def test_closed_finance_flag_allows_metadata_but_rejects_cost_restatement(self):
        os.environ['FIFO_FINANCIAL_CONTROLS_ENABLED'] = '0'
        response = await self.save(shipping_cost_no='HEAD-ORDER')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.session.get(InventoryBatch, 1).shipping_cost_no, 'HEAD-ORDER')
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 6)
        self.assertEqual(self.session.scalars(select(InventoryCostAdjustment)).all(), [])
        self.assertEqual(self.logs()[0].after_values['shipping_cost_no'], 'HEAD-ORDER')
        response = await self.save(shipping_cost='20')
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()['detail']['code'], 'batch_cost_controls_required')
        self.assertEqual(len(self.logs()), 1)
        self.assertIsNone(self.session.get(InventoryBatch, 1).cost_basis)

    async def test_legacy_client_cannot_silently_restate_costs(self):
        response = await self.save(shipping_cost='20', cost_edit_mode=None)
        self.assertEqual(response.status_code, 428, response.text)
        self.assertEqual(response.json()['detail']['code'], 'batch_restatement_mode_required')
        self.assertEqual(self.logs(), [])
        self.assertIsNone(self.session.get(InventoryBatch, 1).cost_basis)

    async def test_derived_adjustment_overflow_is_rejected_and_rolled_back(self):
        batch = self.session.get(InventoryBatch, 1)
        batch.unit_cost = batch.purchase_price = Decimal('1000000')
        self.session.get(SaleCostDetail, 1).unit_cost = Decimal('1000000')
        self.session.commit()
        response = await self.save(quantity=2000000, total_amount='100')
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()['detail']['code'], 'batch_cost_out_of_range')
        self.assertEqual(batch.quantity, 10)
        self.assertIsNone(batch.cost_basis)
        self.assertEqual(self.logs(), [])

    async def test_future_fifo_uses_original_base_and_adjusts_exactly_once(self):
        response = await self.save(shipping_cost='20')
        self.assertEqual(response.status_code, 200, response.text)
        new, _ = await fifo_sell(self.db, product_id=10, user_id=1, store_id=7, order_no='AFTER-EDIT',
            quantity=1, selling_price=Decimal(25))
        detail = self.session.scalar(select(SaleCostDetail).where(SaleCostDetail.sale_id == new.id))
        self.assertEqual(detail.unit_cost, Decimal(10))
        self.session.info.clear()
        context = await sale_cost_context(self.db, [new])
        self.assertEqual(new.total_cost + context[new.id]['adjustment'], Decimal(12))
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 5)

    async def test_old_month_version_stays_unchanged_and_new_version_restates(self):
        self.actor.role = 'admin'
        self.session.commit()
        old = await save_version(self.db, self.actor, FinancialVersionRequest(
            request_key='before-batch-edit', store_id=7, report_month=date(2026, 9, 1)))
        self.session.commit()
        frozen = copy.deepcopy(old.payload)
        response = await self.save(shipping_cost='20')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(old.payload, frozen)
        compared = await compare_version(self.db, self.actor, old)
        change = next(row for row in compared['changes'] if row['key'] == 'head_logistics_cost' and row['period'] == 'current')
        self.assertEqual(change['difference'], Decimal(8))
        new = await save_version(self.db, self.actor, FinancialVersionRequest(
            request_key='after-batch-edit', store_id=7, report_month=date(2026, 9, 1)))
        self.session.commit()
        self.assertEqual((old.version, new.version), (1, 2))
        self.assertEqual(old.payload, frozen)
