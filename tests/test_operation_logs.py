"""统一日志：隔离SQLite/ASGI，权限、计数、详情、隐私和只读验证。"""
import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.dialects import mysql

from auth import get_current_user
from database import Base, get_db
from models import (BatchChangeLog, BusinessOperationLog, FinancialAuditEvent, InventoryBatch, InventoryStockCount,
                    Product, SalesImportBatch, SettlementEntry, Store, User)
from routers.operation_logs import router
from services.operation_logs import filtered_query


class ReadSession:
    def __init__(self,session):self.session=session
    async def execute(self,stmt):return self.session.execute(stmt)
    async def scalar(self,stmt):return self.session.scalar(stmt)
    async def get(self,model,key):return self.session.get(model,key)


class OperationLogsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flag=patch.dict(os.environ,{'FIFO_FINANCIAL_CONTROLS_ENABLED':'0','FIFO_OPERATION_AUDIT_ENABLED':'0'});self.flag.start();self.addCleanup(self.flag.stop)
        today=patch('services.operation_logs.business_today',return_value=date(2026,11,2));today.start();self.addCleanup(today.stop)
        self.engine=create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.session=Session(self.engine)
        self.session.add_all([Store(id=i,platform_store_id=str(i),name=f'店铺{i}',country_or_region='CN',settlement_currency='CNY') for i in (7,8)])
        self.session.flush()
        self.session.add_all([User(id=i,username=f'user{i}',password_hash='fixture',role=role,store_id=store)
            for i,role,store in ((1,'admin',None),(2,'operator',7),(3,'viewer',7),(4,'operator',None))])
        self.session.add(Product(id=1,sku='SKU-1',name='中文商品'))
        self.session.flush()
        self.session.add(InventoryBatch(id=1,product_id=1,batch_no='B%12',quantity=100,remaining_quantity=100,
            unit_cost=1,purchase_price=1,arrived_at=datetime(2026,9,1),user_id=2))
        self.session.add_all([
            BatchChangeLog(id=1,batch_id=1,batch_no='B%12',action='update',actor_id=2,actor_username='操作人旧名',actor_role='operator',
                before_values={'product_id':1,'quantity':100,'token':'must-not-leak'},
                after_values={'product_id':1,'quantity':120,'password':'must-not-leak','impairment_store_names':['店铺7','店铺8']},created_at=datetime(2026,11,2,10)),
            BatchChangeLog(id=2,batch_id=999,batch_no='已删除批次',action='void',actor_id=999,actor_username='历史用户',actor_role='admin',
                before_values={'product_id':1,'quantity':50},after_values={'product_id':1,'quantity':50,'void_reason':'重复入库'},created_at=datetime(2026,11,2,11))])
        self.session.add_all([InventoryStockCount(id=i,product_id=1,count_date=day,revision=1,system_quantity=100,
            actual_quantity=90+i,ledger_version='a'*64,request_key=f'count-key-{i}',created_by=2,note=f'盘点备注{i}',created_at=datetime(2026,11,i+0,12))
            for i,day in ((1,date(2026,11,1)),(2,date(2026,11,2)))])
        self.session.add_all([SalesImportBatch(id=1,user_id=2,store_id=7,file_name='来源.xlsx',file_type='order_detail',
            total_rows=20,settlement_rows=18,sales_created=23,pending_confirmation=2,status='rolled_back',
            imported_at=datetime(2026,11,1,10),rolled_back_at=datetime(2026,11,2,9),rolled_back_by_user_id=1),
            SalesImportBatch(id=2,user_id=1,store_id=8,file_name='另一店.csv',file_type='legacy_csv',status='completed',imported_at=datetime(2026,11,2,9))])
        self.session.add(FinancialAuditEvent(id=1,store_id=7,entity_type='financial_expenses',entity_id=10,action='confirm',actor_id=1,
            before_values={'amount':'1','payload':{'secret':'hidden'}},after_values={'amount':'2','note':'财务备注','request_key':'hidden'},created_at=datetime(2026,11,2,13)))
        self.session.commit()
        self.statements=[]
        event.listen(self.engine,'before_cursor_execute',lambda _a,_b,sql,*_rest:self.statements.append(sql))
        self.actor=NS(id=1,role='admin',store_id=None)
        self.app=FastAPI();self.app.include_router(router)
        async def db():yield ReadSession(self.session)
        self.app.dependency_overrides[get_db]=db
        self.app.dependency_overrides[get_current_user]=lambda:self.actor
        self.client=AsyncClient(transport=ASGITransport(app=self.app),base_url='http://fixture')

    async def asyncTearDown(self):
        await self.client.aclose();self.session.close();self.engine.dispose()

    async def records(self,params=None):
        response=await self.client.get('/operation-logs',params=params)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def test_combined_pagination_filters_and_original_time_markers(self):
        data=await self.records({'page_size':2,'page':999})
        self.assertEqual((data['total'],data['page'],len(data['items'])),(7,4,1))
        queries=len(self.statements)
        all_rows=(await self.records())['items']
        self.assertLessEqual(len(self.statements)-queries,6)  # 两次分页查询+按来源批量补摘要
        self.assertEqual(len({(row['source'],row['id']) for row in all_rows}),7)
        self.assertEqual({row['time_basis'] for row in all_rows},{'utc','original'})
        self.assertIn('quantity',next(row for row in all_rows if row['source']=='batch' and row['id']==1)['summary']['changed_fields'])
        self.assertNotIn('token',str(all_rows));self.assertNotIn('password',str(all_rows))
        self.assertEqual((await self.records({'keyword':'%'}))['total'],1)
        self.assertEqual((await self.records({'keyword':'中文商品'}))['total'],4)
        self.assertEqual((await self.records({'actor':'操作人旧名'}))['total'],1)
        self.assertEqual((await self.records({'action':'void'}))['total'],1)
        self.assertEqual((await self.records({'source':'stock_count','start':'2026-11-02','end':'2026-11-02'}))['total'],1)
        self.assertEqual((await self.records({'store_id':7}))['total'],2)
        self.assertEqual((await self.records({'keyword':"%' OR 1=1 --"}))['total'],0)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in self.statements))
        self.assertFalse(any('financial_audit_events' in sql for sql in self.statements))
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,100)
        query=filtered_query(self.actor,keyword='SKU-1',start=date(2026,11,1))
        self.assertIn('UNION ALL',str(select(query).compile(dialect=mysql.dialect())))

    async def test_log_success_denials_validation_and_source_errors_all_disable_http_cache(self):
        paths=('/operation-logs/options','/operation-logs?page_size=1','/operation-logs/batch/1')
        for path in paths:
            response=await self.client.get(path)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.headers.get('cache-control'),'private, no-store')
        for path,status in (('/operation-logs/batch/999',404),('/operation-logs?page_size=0',422)):
            response=await self.client.get(path)
            self.assertEqual(response.status_code,status)
            self.assertEqual(response.headers.get('cache-control'),'private, no-store')
        for role in ('operator','viewer'):
            self.actor=NS(id=2,role=role,store_id=7)
            for path in paths:
                response=await self.client.get(path)
                self.assertEqual(response.status_code,403)
                self.assertEqual(response.headers.get('cache-control'),'private, no-store')
        async def anonymous():raise HTTPException(401,detail='fixture anonymous')
        self.app.dependency_overrides[get_current_user]=anonymous
        for path in paths:
            response=await self.client.get(path)
            self.assertEqual(response.status_code,401)
            self.assertEqual(response.headers.get('cache-control'),'private, no-store')
        self.app.dependency_overrides[get_current_user]=lambda:self.actor
        self.actor=NS(id=1,role='admin',store_id=None)
        InventoryStockCount.__table__.drop(self.engine)
        response=await self.client.get('/operation-logs')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.headers.get('cache-control'),'private, no-store')

    async def test_details_preserve_snapshots_and_exclude_unrelated_private_fields(self):
        row=(await self.client.get('/operation-logs/batch/1')).json()
        self.assertEqual(row['actor_name'],'操作人旧名')
        self.assertEqual(row['record_kind'],'change')
        quantity=next(change for change in row['changes'] if change['field']=='quantity')
        self.assertEqual((quantity['before'],quantity['after']),(100,120))
        self.assertNotIn('must-not-leak',str(row))
        deleted=(await self.client.get('/operation-logs/batch/2')).json()
        self.assertEqual(deleted['reason'],'重复入库');self.assertEqual(deleted['sku'],'SKU-1')
        count=(await self.client.get('/operation-logs/stock_count/2')).json()
        self.assertEqual(count['record_kind'],'snapshot');self.assertEqual(count['changes'],[])
        self.assertEqual(count['after_values']['difference'],-8)
        imported=(await self.client.get('/operation-logs/sales_import/1')).json()
        rolled=(await self.client.get('/operation-logs/sales_rollback/1')).json()
        self.assertEqual(imported['record_kind'],'business_history')
        self.assertEqual(imported['actor_id'],2);self.assertEqual(rolled['actor_id'],1)
        self.assertEqual(imported['after_values']['status'],'rolled_back')

    async def test_non_admin_cannot_read_any_log_endpoint_or_call_query_services_directly(self):
        from services import operation_logs as logs
        paths=['/operation-logs/options','/operation-logs','/operation-logs?source=batch&group_id='+'a'*32,
            *[f'/operation-logs/{source}/1' for source in ('batch','stock_count','sales_import','sales_rollback',
                'finance','products','accounts','masters','batch_create','sales_operations','report_changes')]]
        for role in ('operator','viewer'):
            for store_id in (None,7,8):
                self.actor=NS(id=2,role=role,store_id=store_id)
                queries=len(self.statements)
                for path in paths:self.assertEqual((await self.client.get(path)).status_code,403,(role,store_id,path))
                for call in (logs.list_logs(None,self.actor),logs.detail(None,self.actor,'batch',1)):
                    with self.assertRaises(HTTPException) as raised:await call
                    self.assertEqual(raised.exception.status_code,403)
                    self.assertEqual(raised.exception.detail['code'],'operation_logs_admin_required')
                self.assertEqual(len(self.statements),queries)
                self.assertEqual(logs.allowed_sources(self.actor,True),[])

    async def test_finance_flag_admin_only_metadata_and_stable_invalid_requests(self):
        self.assertNotIn('finance',(await self.client.get('/operation-logs/options')).json()['sources'])
        with patch.dict(os.environ,{'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            self.assertEqual((await self.records({'source':'finance'}))['total'],1)
            row=(await self.client.get('/operation-logs/finance/1')).json()
            self.assertNotIn('hidden',str(row));self.assertEqual(row['after_values']['amount'],'2')
            self.actor=NS(id=2,role='operator',store_id=7)
            self.assertEqual((await self.client.get('/operation-logs/options')).status_code,403)
            self.assertEqual((await self.client.get('/operation-logs',params={'source':'finance'})).status_code,403)
        self.actor=NS(id=1,role='admin',store_id=None)
        for params in ({'start':'2026-11-02','end':'2026-11-01'},{'page_size':101},{'end':'9999-12-31'},{'source':'unknown'}):
            self.assertEqual((await self.client.get('/operation-logs',params=params)).status_code,422)
        self.assertEqual((await self.client.get('/operation-logs/batch/-1')).status_code,422)
        self.assertEqual((await self.client.get('/operation-logs/sales_import/999')).status_code,404)

    async def test_actual_expense_source_change_keeps_mode_in_summary_and_detail(self):
        from tests.test_business_audit import Facade
        from schemas import ExpenseSourceRequest
        from services.financial_controls import set_expense_source
        db=Facade(self.session)
        with patch.dict(os.environ,{'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            for mode in ('manual','ledger'):
                await set_expense_source(db,self.actor,ExpenseSourceRequest(
                    store_id=7,report_month=date(2026,9,1),category='advertising',mode=mode))
                await db.commit()
            rows=(await self.records({'source':'finance','action':'source_selection'}))['items']
            event=next(row for row in rows if row['summary']['changed_fields']==['mode'])
            row=(await self.client.get(f"/operation-logs/finance/{event['id']}")).json()
            self.assertEqual(row['before_values']['mode'],'manual')
            self.assertEqual(row['after_values']['mode'],'ledger')
            self.assertEqual([(change['field'],change['before'],change['after']) for change in row['changes']],
                [('mode','manual','ledger')])

    async def test_actual_settlement_classification_preserves_breakdown_and_filters_unrelated_data(self):
        from tests.test_business_audit import Facade
        from schemas import SettlementClassificationRequest
        from services.financial_controls import classify_settlement
        db=Facade(self.session)
        self.session.add(SettlementEntry(id=1,source_key='finance-log-source',source_row_number=1,order_id='O',
            platform_sku_id='P',store_id=7,quantity=1,net_product_sales=30,settlement_total=20,raw_data={}))
        self.session.commit()
        with patch.dict(os.environ,{'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}):
            for values in ({'commission':'8','advertising':'2'},{'commission':'3','advertising':'7'}):
                await classify_settlement(db,self.actor,1,SettlementClassificationRequest(breakdown=values))
                await db.commit()
            events=(await self.records({'source':'finance','action':'classify'}))['items']
            latest=max(events,key=lambda row:row['id'])
            self.assertEqual(latest['summary']['changed_fields'],['breakdown'])
            row=(await self.client.get(f"/operation-logs/finance/{latest['id']}")).json()
            self.assertEqual(row['before_values']['breakdown'],{'commission':'8.0000','advertising':'2.0000'})
            self.assertEqual(row['after_values']['breakdown'],{'commission':'3.0000','advertising':'7.0000'})
            event=self.session.get(FinancialAuditEvent,latest['id'])
            event.after_values={**event.after_values,'mode':'must-hide-wrong-entity','payload':{'secret':'must-hide'},
                'breakdown':{**event.after_values['breakdown'],'password':'must-hide','internal':'must-hide'}}
            self.session.commit()
            row=(await self.client.get(f"/operation-logs/finance/{latest['id']}")).json()
            self.assertNotIn('must-hide',str(row))

    async def test_actual_report_version_exposes_number_but_never_payload_or_digest(self):
        from tests.test_business_audit import Facade
        from schemas import FinancialVersionRequest
        from services.financial_versions import save_version
        db=Facade(self.session)
        with patch.dict(os.environ,{'FIFO_FINANCIAL_CONTROLS_ENABLED':'1'}), \
             patch('services.financial_versions.today',return_value=date(2026,11,3)), \
             patch('services.financial_versions.collect_payload',new=AsyncMock(return_value={'warnings':[],'secret':'must-hide-payload'})):
            version=await save_version(db,self.actor,FinancialVersionRequest(
                request_key='operation-log-version',store_id=7,report_month=date(2026,9,1)))
            await db.commit()
            latest=(await self.records({'source':'finance','action':'capture_version'}))['items'][0]
            row=(await self.client.get(f"/operation-logs/finance/{latest['id']}")).json()
            self.assertEqual(row['after_values']['version'],version.version)
            self.assertEqual(row['after_values']['calculation_version'],version.calculation_version)
            self.assertIn('version',latest['summary']['changed_fields'])
            self.assertNotIn('must-hide',str(row))
            self.assertTrue({'payload','source_digest','request_key'}.isdisjoint(row['after_values']))

    async def test_missing_source_is_not_silently_reported_as_empty_or_auto_created(self):
        InventoryStockCount.__table__.drop(self.engine)
        response=await self.client.get('/operation-logs')
        self.assertEqual(response.status_code,503)
        self.assertEqual(response.json()['detail']['code'],'operation_logs_source_unavailable')
        self.assertEqual((await self.records({'source':'batch'}))['total'],2)

    async def test_manual_settlement_is_identified_without_faking_an_upload(self):
        self.session.add(SalesImportBatch(id=3,user_id=2,store_id=7,file_name='手工补录',file_type='manual_settlement',
            sales_created=0,settlement_rows=1,imported_at=datetime(2026,11,2,10)))
        self.session.commit()
        data=await self.records({'source':'sales_import','action':'manual_settlement'})
        self.assertEqual(data['total'],1)
        self.assertEqual(data['items'][0]['action'],'manual_settlement')
        self.assertEqual(data['items'][0]['summary']['settlement_rows'],1)

    def add_audit(self,**values):
        row=BusinessOperationLog(module='products',action='update',entity_type='products',entity_id=999,
            entity_label='已删除商品',product_id=999,actor_id=999,actor_username='已删除操作人',actor_role='admin',
            group_id='a'*32,before_values={'sku':'OLD-SKU','name':'旧中文'},after_values={'sku':'NEW-SKU','name':'更名'},
            unknown_before_fields=['name'],recorded_at=datetime(2026,11,2,15))
        for key,value in values.items():setattr(row,key,value)
        self.session.add(row);self.session.commit();return row.id

    async def test_new_sources_retain_deleted_snapshots_group_and_historical_reads_when_capture_is_off(self):
        key=self.add_audit(after_values={'sku':'NEW-SKU','name':'更名','password_hash':'hidden'})
        opts=(await self.client.get('/operation-logs/options')).json()
        self.assertFalse(opts['audit_enabled']);self.assertTrue(opts['audit_available']);self.assertIn('accounts',opts['sources'])
        result=await self.records({'source':'products','keyword':'OLD-SKU','group_id':'a'*32})
        self.assertEqual(result['total'],1);row=result['items'][0]
        self.assertEqual((row['sku'],row['product_name'],row['actor_name']),('NEW-SKU','更名','已删除操作人'))
        detail=(await self.client.get(f'/operation-logs/products/{key}')).json()
        self.assertEqual(detail['group_id'],'a'*32);self.assertNotIn('hidden',str(detail))
        self.assertFalse(next(value for value in detail['changes'] if value['field']=='name')['before_known'])
        self.assertEqual((await self.records({'group_id':'b'*32}))['total'],0)
        self.assertEqual((await self.client.get('/operation-logs',params={'group_id':'invalid'})).status_code,422)

    async def test_new_source_permissions_match_list_counts_details_and_group_filter(self):
        account=self.add_audit(module='accounts',entity_type='users',product_id=None,after_values={'username':'target','password_changed':True})
        store7=self.add_audit(module='sales',entity_type='settlement_entries',store_id=7)
        store8=self.add_audit(module='reports',entity_type='store_profit_loss_reports',store_id=8)
        batch=self.add_audit(module='batches',entity_type='inventory_batches')
        self.add_audit(module='masters',entity_type='product_lines')
        self.actor=NS(id=1,role='admin',store_id=7)
        self.assertEqual((await self.records({'group_id':'a'*32}))['total'],5)
        for source,key in (('accounts',account),('sales_operations',store7),('report_changes',store8),('batch_create',batch)):
            self.assertEqual((await self.client.get(f'/operation-logs/{source}/{key}')).status_code,200)
        for role in ('operator','viewer'):
            self.actor=NS(id=2,role=role,store_id=7)
            self.assertEqual((await self.client.get('/operation-logs',params={'group_id':'a'*32})).status_code,403)
            self.assertEqual((await self.client.get(f'/operation-logs/accounts/{account}')).status_code,403)
            self.assertEqual((await self.client.get(f'/operation-logs/sales_operations/{store7}')).status_code,403)
            self.assertEqual((await self.client.get(f'/operation-logs/report_changes/{store8}')).status_code,403)
        self.assertEqual((await self.client.get(f'/operation-logs/batch_create/{batch}')).status_code,403)
        self.actor.store_id=None
        self.assertEqual((await self.client.get('/operation-logs',params={'group_id':'a'*32})).status_code,403)
        self.assertEqual((await self.client.get(f'/operation-logs/sales_operations/{store7}')).status_code,403)

    async def test_optional_audit_table_absence_is_compatible_when_off_and_explicit_failure_when_on(self):
        BusinessOperationLog.__table__.drop(self.engine);self.session.info.clear()
        opts=(await self.client.get('/operation-logs/options')).json()
        self.assertFalse(opts['audit_available']);self.assertNotIn('products',opts['sources'])
        self.assertEqual((await self.records())['total'],7)
        with patch.dict(os.environ,{'FIFO_OPERATION_AUDIT_ENABLED':'1'}):
            self.assertEqual((await self.client.get('/operation-logs',params={'source':'products'})).status_code,503)
            self.assertEqual((await self.records({'source':'batch'}))['total'],2)


if __name__=='__main__':unittest.main()
