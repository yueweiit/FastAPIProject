"""库存盘点/跌价：隔离SQLite及真实ASGI，业务数据库不连接。"""
import unittest
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from unittest.mock import AsyncMock
from fastapi import HTTPException
from routers.products import delete_product
from routers.auth import delete_user

from models import BatchChangeLog, InventoryBatch, InventoryStockCount, Product, SaleCostDetail
from tests import test_inventory_workspace as workspace


class AssessmentTests(unittest.IsolatedAsyncioTestCase):
    setUp=workspace.InventoryWorkspaceTests.setUp
    asyncTearDown=workspace.InventoryWorkspaceTests.asyncTearDown
    record_query=workspace.InventoryWorkspaceTests.record_query
    add_sale=workspace.InventoryWorkspaceTests.add_sale

    async def page(self, as_of='2026-09-30', **params):
        response=await self.client.get('/inventory/assessment',params={'as_of':as_of,**params})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def row(self,data,product=1):
        return next(row for row in data['items'] if row['product_id']==product)

    def payload(self,row,actual=65,key='stock-count-request-0001'):
        return {'product_id':row['product_id'],'count_date':row['as_of'],'actual_quantity':actual,
                'ledger_version':row['ledger_version'],'previous_id':row['count']['id'] if row['count'] else None,
                'note':'测试盘点','request_key':key}

    async def test_fields_batchs_and_valuation_reuse_global_rules(self):
        data=await self.page(keyword='SKU-1')
        row=self.row(data)
        self.assertEqual((data['total'],row['month_inbound'],row['month_outbound'],row['system_quantity']),(1,150,30,70))
        self.assertEqual(Decimal(str(row['inventory_amount'])),700)
        self.assertEqual(Decimal(str(row['book_value'])),700)
        self.assertEqual(row['batches'][0]['age_days'],29)
        self.assertEqual(len(row['batches']),1)
        self.assertIsNone(row['count'])
        self.assertIsNone(row['monthly_total_impairment_amount']) # 已作废批次历史持有费用不能填0
        self.assertTrue(set(self.queries)<={'SELECT'},self.queries)

    async def test_zero_count_negative_difference_snapshot_and_idempotence(self):
        self.actor.id=7
        row=self.row(await self.page())
        payload=self.payload(row,0)
        before=self.session.get(InventoryBatch,1).remaining_quantity
        first=await self.client.post('/inventory/stock-counts',json=payload)
        self.assertEqual(first.status_code,200,first.text)
        result=first.json()
        self.assertEqual((result['system_quantity'],result['actual_quantity'],result['difference']),(70,0,-70))
        again=await self.client.post('/inventory/stock-counts',json=payload)
        self.assertEqual(again.json()['id'],result['id'])
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,before)
        self.assertEqual(len(self.session.scalars(select(InventoryStockCount)).all()),1)
        history=await self.client.get('/inventory/stock-counts?product_id=1&count_date=2026-09-30')
        self.assertEqual(history.json()['items'][0]['difference'],-70)

    async def test_multi_batch_cost_and_global_safe_stock_match_holding_replay(self):
        batch=self.session.get(InventoryBatch,2)
        batch.voided_at=None
        self.session.commit()
        row=self.row(await self.page())
        self.assertEqual(len(row['batches']),2)
        self.assertEqual(row['system_quantity'],120)
        self.assertEqual(Decimal(str(row['inventory_amount'])),1300)
        self.assertEqual(Decimal(str(row['impairment_amount'])),63)
        self.assertEqual(Decimal(str(row['monthly_total_impairment_amount'])),65)
        self.assertEqual(Decimal(str(row['book_value'])),1237)

    async def test_offsetting_later_batch_corrections_do_not_confirm_wrong_historical_amount(self):
        first=self.session.get(InventoryBatch,1)
        second=self.session.get(InventoryBatch,2)
        second.voided_at=None
        self.session.commit()
        baseline=self.row(await self.page())
        self.assertEqual(Decimal(str(baseline['inventory_amount'])),1300)
        first.quantity,first.remaining_quantity=120,90
        second.quantity,second.remaining_quantity=30,30
        for batch,before,after in ((first,100,120),(second,50,30)):
            self.session.add(BatchChangeLog(batch_id=batch.id,batch_no=batch.batch_no,
                actor_id=7,actor_username='U7',actor_role='operator',before_values={'quantity':before},
                after_values={'quantity':after},created_at=datetime(2026,10,5)))
        self.session.commit();self.session.info.clear()
        data=await self.page()
        row=self.row(data)
        self.assertEqual(row['system_quantity'],120) # 两批更正抵消，SKU总数相等不能证明成本可信。
        self.assertIn('inventory_assessment_quantity_mismatch',row['warnings'])
        for field in ('inventory_amount','average_unit_cost','impairment_amount','monthly_total_impairment_amount','book_value'):
            self.assertIsNone(row[field],field)
        for batch in row['batches']:
            for field in ('quantity','inventory_amount','impairment_amount','monthly_total_impairment_amount'):
                self.assertIsNone(batch[field],field)
            self.assertTrue(all(owner['impairment_amount'] is None for owner in batch['store_allocations']))
        self.assertEqual(Decimal(str(self.row(data,2)['inventory_amount'])),30) # 不污染无关商品。
        exported=await self.client.get('/inventory/assessment/export?as_of=2026-09-30')
        self.assertEqual(exported.status_code,200)
        book=load_workbook(BytesIO(exported.content),data_only=True)
        values=next(r for r in book.active.iter_rows(values_only=True) if r[0]=='SKU-1')
        self.assertEqual(values[4],120)
        self.assertTrue(all(values[index] is None for index in range(5,10)))
        self.assertIn('逐批数量',values[16])
        self.assertTrue(all(book.active.cell(book.active.max_row,col).value is None for col in range(7,11)))
        self.assertTrue(all(row[5].value is None and row[7].value is None
            for row in book['批次明细'].iter_rows(min_row=2) if row[0].value=='SKU-1'))
        # 更正日之后的逐批余量一致，仍能正常显示有效成本。
        self.session.info.clear()
        current=self.row(await self.page('2026-10-10'))
        self.assertEqual(Decimal(str(current['inventory_amount'])),1260)
        self.assertNotIn('inventory_assessment_quantity_mismatch',current['warnings'])

    async def test_viewer_receipts_exclude_voided_batches_without_changing_count_basis(self):
        self.actor.id=7
        with patch('routers.inventory.business_today',return_value=date(2026,9,30)):
            original=self.row(await self.page())
            saved=await self.client.post('/inventory/stock-counts',json=self.payload(original,70,'stock-count-role-basis-0001'))
            self.assertEqual(saved.status_code,200,saved.text)
            for role,expected in (('admin',150),('operator',150),('viewer',100)):
                with self.subTest(role=role):
                    self.actor.role,self.actor.store_id=role,7
                    row=self.row(await self.page())
                    daily=await self.client.get('/inventory/daily?month=2026-09-01&product_id=1')
                    allowed=sum(day['inbound'] for day in daily.json()['items'][0]['days'])
                    self.assertEqual(row['month_inbound'],expected)
                    self.assertEqual(row['month_inbound'],allowed)
                    self.assertEqual(row['system_quantity'],70)
                    self.assertEqual(row['ledger_version'],original['ledger_version'])
                    self.assertFalse(row['count_basis_changed'])
                    self.assertNotIn('days',row)
                    self.assertFalse(any(key.startswith('_') for key in row))
                    exported=await self.client.get('/inventory/assessment/export?as_of=2026-09-30')
                    self.assertEqual(exported.status_code,200)
                    sheet=load_workbook(BytesIO(exported.content),data_only=True).active
                    values=next(r for r in sheet.iter_rows(values_only=True) if r[0]=='SKU-1')
                    self.assertEqual(values[2],expected)
                    self.assertEqual(sheet.cell(sheet.max_row,3).value,expected)

    async def test_hidden_untrusted_voided_receipt_does_not_mask_visible_viewer_inbound(self):
        self.session.add(BatchChangeLog(batch_id=2,batch_no='B2',actor_id=7,actor_username='U7',
            actor_role='operator',before_values={'quantity':50},after_values={'quantity':60},created_at=datetime(2026,9,12)))
        self.session.commit()
        self.actor.id,self.actor.role,self.actor.store_id=7,'viewer',7
        with patch('routers.inventory.business_today',return_value=date(2026,9,30)):
            row=self.row(await self.page())
        self.assertEqual(row['month_inbound'],100)
        self.assertIsNone(row['system_quantity']) # 内部全局数量异常仍不伪造账面数。

    async def test_zero_balances_of_future_or_voided_batches_do_not_mask_known_cost(self):
        self.session.add_all([
            InventoryBatch(id=4,product_id=1,user_id=7,batch_no='FUTURE',quantity=10,remaining_quantity=10,
                purchase_price=10,unit_cost=10,arrived_at=datetime(2026,10,5)),
            InventoryBatch(id=5,product_id=1,user_id=7,batch_no='CANCELLED-FUTURE',quantity=10,remaining_quantity=10,
                purchase_price=10,unit_cost=10,arrived_at=datetime(2026,10,5),voided_at=datetime(2026,10,4)),
        ])
        self.session.commit()
        row=self.row(await self.page())
        self.assertEqual(row['system_quantity'],70)
        self.assertEqual(Decimal(str(row['inventory_amount'])),700)
        self.assertNotIn('inventory_assessment_quantity_mismatch',row['warnings'])
        self.assertEqual([batch['batch_id'] for batch in row['batches']],[1])

    async def test_count_history_protects_referenced_product_user_and_image(self):
        self.actor.id=7
        row=self.row(await self.page(),3)
        response=await self.client.post('/inventory/stock-counts',json=self.payload(row,0))
        self.assertEqual(response.status_code,200,response.text)
        db=workspace.InventoryReadSession(self.session)
        with patch('pathlib.Path.unlink') as unlink, self.assertRaises(HTTPException) as error:
            await delete_product(3,db,self.actor)
        self.assertEqual(error.exception.status_code,409)
        unlink.assert_not_called()
        self.actor.id=8
        with self.assertRaises(HTTPException) as error:
            await delete_user(7,db,self.actor)
        self.assertEqual(error.exception.status_code,409)
        self.assertEqual(error.exception.detail['code'],'inventory_count_user_referenced')
        self.assertEqual(len(self.session.scalars(select(InventoryStockCount)).all()),1)

    async def test_product_delete_conflict_keeps_image_and_cleanup_failure_keeps_success(self):
        product=self.session.get(Product,3)
        product.image='/uploads/test-count-delete-fixture.png'
        self.session.commit()
        db=workspace.InventoryReadSession(self.session)
        with patch.object(db,'commit',new=AsyncMock(side_effect=IntegrityError('test',{},Exception('fk')))), patch('pathlib.Path.unlink') as unlink:
            with self.assertRaises(HTTPException) as error:
                await delete_product(3,db,self.actor)
            self.assertEqual(error.exception.status_code,409)
            unlink.assert_not_called()
        self.assertIsNotNone(self.session.get(Product,3))
        with patch('pathlib.Path.exists',return_value=True), patch('pathlib.Path.unlink',side_effect=OSError('fixture cleanup failure')):
            with self.assertLogs('routers.products',level='WARNING'):
                result=await delete_product(3,db,self.actor)
        self.assertEqual(result,{'ok':True,'warning':'product_image_cleanup_failed'})
        self.assertIsNone(self.session.get(Product,3))

    async def test_new_revision_preserves_old_and_stale_editor_is_rejected(self):
        self.actor.id=7
        row=self.row(await self.page())
        payload=self.payload(row)
        self.assertEqual((await self.client.post('/inventory/stock-counts',json=payload)).status_code,200)
        stale={**payload,'request_key':'stock-count-request-stale'}
        self.assertEqual((await self.client.post('/inventory/stock-counts',json=stale)).status_code,409)
        current=self.row(await self.page())
        response=await self.client.post('/inventory/stock-counts',json=self.payload(current,75,'stock-count-request-0002'))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((response.json()['revision'],response.json()['difference']),(2,5))
        records=(await self.client.get('/inventory/stock-counts?product_id=1&count_date=2026-09-30&page_size=1')).json()
        self.assertEqual((records['total'],len(records['items'])),(2,1))
        self.assertEqual(self.session.scalars(select(InventoryStockCount).order_by(InventoryStockCount.id)).first().actual_quantity,65)

    async def test_later_sales_do_not_change_old_count_or_invalidate_its_basis(self):
        self.actor.id=7
        row=self.row(await self.page())
        self.assertEqual((await self.client.post('/inventory/stock-counts',json=self.payload(row))).status_code,200)
        self.add_sale(3,1,5,datetime(2026,10,2),7)
        self.session.get(InventoryBatch,1).remaining_quantity-=5
        self.session.commit()
        after=self.row(await self.page())
        self.assertEqual(after['count']['difference'],-5)
        self.assertEqual(after['system_quantity'],70)
        self.assertFalse(after['count_basis_changed'])
        # 回填盘点日前出库时，旧差异保留并标记需复核。
        self.add_sale(4,1,1,datetime(2026,9,20),7)
        self.session.get(InventoryBatch,1).remaining_quantity-=1
        self.session.commit()
        after=self.row(await self.page())
        self.assertTrue(after['count_basis_changed'])
        self.assertEqual(after['count']['difference'],-5)
        self.assertEqual(after['count']['system_quantity'],70)

    async def test_changed_stock_untrusted_and_invalid_inputs_do_not_write(self):
        self.actor.id=7
        row=self.row(await self.page())
        payload=self.payload(row)
        for updates in ({'actual_quantity':-1},{'actual_quantity':1.5},{'actual_quantity':'2'},
                        {'actual_quantity':2147483648},{'note':'x'*1001},{'ledger_version':'bad'}):
            self.assertEqual((await self.client.post('/inventory/stock-counts',json={**payload,**updates})).status_code,422)
        self.session.get(InventoryBatch,1).remaining_quantity=69
        self.session.commit()
        pending=self.row(await self.page())
        self.assertIsNone(pending['system_quantity'])
        self.assertIsNone(pending['batches'][0]['quantity'])
        response=await self.client.post('/inventory/stock-counts',json=payload)
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.session.scalars(select(InventoryStockCount)).all(),[])

    async def test_roles_dates_and_store_outbound_scope(self):
        with patch('routers.inventory.business_today',return_value=date(2026,9,30)):
            for role in ('operator','viewer'):
                self.actor.role,self.actor.store_id,self.actor.id=role,7,7
                data=await self.page()
                row=self.row(data)
                self.assertEqual(row['month_outbound'],20)
                self.assertEqual(row['system_quantity'],70)
                self.assertEqual(data['can_write'],role=='operator')
                self.assertNotIn('days',row)
                self.assertTrue(all(owner['store_id']==7 for b in row['batches'] for owner in b['store_allocations']))
                self.assertEqual((await self.client.get('/inventory/assessment?as_of=2026-09-29')).status_code,403)
                self.assertEqual((await self.client.get('/inventory/stock-counts?product_id=1&count_date=2026-09-29')).status_code,403)
                response=await self.client.post('/inventory/stock-counts',json=self.payload(row,key=f'stock-count-{role}-request'))
                self.assertEqual(response.status_code,200 if role=='operator' else 403,response.text)
                self.actor.store_id=None
                self.assertEqual((await self.client.get('/inventory/assessment?as_of=2026-09-30')).status_code,403)
            self.actor.role='admin'
            self.assertEqual((await self.client.get('/inventory/assessment?as_of=2026-10-01')).status_code,422)
            self.assertEqual((await self.client.get('/inventory/assessment?as_of=0001-01-01')).status_code,422)

    async def test_export_all_results_snapshot_values_text_safety_and_null_totals(self):
        self.actor.id=7
        row=self.row(await self.page())
        payload=self.payload(row,75);payload['note']='=SUM(1,2)'
        self.assertEqual((await self.client.post('/inventory/stock-counts',json=payload)).status_code,200)
        response=await self.client.get('/inventory/assessment/export?as_of=2026-09-30&page=2&page_size=1')
        self.assertEqual(response.status_code,200,response.text if response.status_code!=200 else '')
        book=load_workbook(BytesIO(response.content),data_only=True)
        self.assertEqual(book.sheetnames,['库存盘点与跌价','批次明细','店铺跌价分摊','说明'])
        self.assertEqual(book.active.max_row,5) # 3商品及总计
        exported=next(r for r in book.active if r[0].value=='SKU-1')
        self.assertEqual([exported[c].value for c in (10,11,12)],[70,75,5])
        self.assertEqual(exported[16].data_type,'s')
        self.assertTrue(exported[16].value.startswith('=SUM'))
        self.assertIsNone(book.active.cell(5,9).value)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,70)

    async def test_missing_schema_is_explicit_and_query_does_not_create_it(self):
        InventoryStockCount.__table__.drop(self.engine)
        response=await self.client.get('/inventory/assessment?as_of=2026-09-30')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['detail']['code'],'inventory_count_schema_required')

    async def test_backdated_stock_change_conflicts_and_failed_commit_rolls_back_count(self):
        self.actor.id=7
        row=self.row(await self.page())
        payload=self.payload(row)
        self.add_sale(3,1,1,datetime(2026,9,25),7)
        self.session.get(InventoryBatch,1).remaining_quantity-=1
        self.session.commit()
        response=await self.client.post('/inventory/stock-counts',json=payload)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail']['code'],'inventory_count_conflict')
        row=self.row(await self.page())
        with patch.object(workspace.InventoryReadSession,'commit',new=AsyncMock(side_effect=IntegrityError('test',{},Exception('unique')))):
            response=await self.client.post('/inventory/stock-counts',json=self.payload(row))
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.session.scalars(select(InventoryStockCount)).all(),[])
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,69)

    async def test_source_and_export_limits_reject_before_expensive_generation(self):
        async def capped(db, statement):
            if 'FROM sale_cost_details' in str(statement):
                return 10001
            return db.session.scalar(statement)
        with patch.object(workspace.InventoryReadSession,'scalar',new=capped), patch(
                'services.inventory_assessment.load_inventory_provision_data',new=AsyncMock()) as replay:
            response=await self.client.get('/inventory/assessment?as_of=2026-09-30')
        self.assertEqual(response.status_code,422)
        replay.assert_not_awaited()
        with patch('routers.inventory.assessment_rows',new=AsyncMock(return_value=[{'batches':[None]*10001}])):
            response=await self.client.get('/inventory/assessment/export?as_of=2026-09-30')
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['detail']['code'],'inventory_export_sources_too_large')


if __name__=='__main__':unittest.main()
