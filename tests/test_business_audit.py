"""扩展审计事务与路由回归；隔离SQLite，业务和审计同事务。"""
import copy
import json
import os
import unittest
from datetime import datetime
from decimal import Decimal as D
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine,event,inspect,select
from sqlalchemy.orm import Session,load_only

from auth import RequireAdmin,RequireAnyRole,RequireOperator,get_current_user
from database import Base,get_db
from models import (BusinessOperationLog,InventoryBatch,PlatformSkuComponent,Product,Sale,SaleCostDetail,
                    SettlementEntry,SettlementEntryAllocation,Store,StoreProfitLossReport,User)
from services.business_audit import AuditPersistenceError,FIELDS,bind,policy


class Facade:
    def __init__(self,session):self.session=session;self.info=session.info
    def get_bind(self):return self.session.get_bind()
    async def execute(self,stmt):return self.session.execute(stmt)
    async def scalar(self,stmt):return self.session.scalar(stmt)
    async def get(self,*args,**kwargs):return self.session.get(*args,**kwargs)
    def add(self,row):self.session.add(row)
    def add_all(self,rows):self.session.add_all(rows)
    async def flush(self):self.session.flush()
    async def commit(self):self.session.commit()
    async def rollback(self):self.session.rollback()
    async def refresh(self,row,*args,**kwargs):self.session.refresh(row,*args,**kwargs)
    async def delete(self,row):self.session.delete(row)


class AuditTransactionTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://');Base.metadata.create_all(self.engine)
        self.session=Session(self.engine,expire_on_commit=False);self.db=Facade(self.session)
        self.actor=NS(id=1,username='actor',role='admin',store_id=None)
    def tearDown(self):self.session.close();self.engine.dispose()
    def rows(self):return list(self.session.scalars(select(BusinessOperationLog).order_by(BusinessOperationLog.id)))
    def product(self):
        row=Product(sku='P1',name='原名',product_type='stable',safe_stock_quantity=5)
        self.session.add(row);self.session.commit();return row

    def test_multiple_flushes_collapse_to_final_database_snapshot_and_delete_retains_identity(self):
        with bind(self.db,self.actor,'products',(Product,)):
            row=Product(sku='P1',name='初值');self.session.add(row);self.session.flush()
            row.name='最终值';self.session.flush();self.session.commit()
        log=self.rows()[0];self.assertEqual(log.before_values,{})
        self.assertEqual(log.after_values['name'],'最终值');self.assertEqual(log.action,'create')
        with bind(self.db,self.actor,'products',(Product,)):
            row.name='中间值';self.session.flush();row.name='修改后';self.session.commit()
        self.assertEqual(len(self.rows()),2);self.assertEqual(self.rows()[1].before_values['name'],'最终值')
        self.assertEqual(self.rows()[1].after_values['name'],'修改后')
        with bind(self.db,self.actor,'products',(Product,)):
            self.session.delete(row);self.session.commit()
        self.assertEqual(self.rows()[-1].after_values,{});self.assertEqual(self.rows()[-1].before_values['name'],'修改后')
        self.assertEqual(self.rows()[-1].actor_username,'actor')

    def test_no_context_or_unchanged_save_does_not_log_and_many_products_use_batch_snapshot_query(self):
        row=self.product();self.assertEqual(self.rows(),[])
        with bind(self.db,self.actor,'products',(Product,)):
            row.name='原名';self.session.commit()
        self.assertEqual(self.rows(),[])
        queries=[]
        def capture(_a,_b,sql,*_args):queries.append(sql)
        event.listen(self.engine,'before_cursor_execute',capture)
        with bind(self.db,self.actor,'products',(Product,),'import_create'):
            self.session.add_all([Product(sku=f'P-{i}',name=f'商品{i}') for i in range(40)])
            self.session.commit()
        snapshots=[sql for sql in queries if sql.startswith('SELECT') and 'FROM products' in sql]
        self.assertEqual(len(snapshots),1);self.assertEqual(len(self.rows()),40)
        self.assertEqual(len({log.group_id for log in self.rows()}),1)

    def test_failed_audit_insert_rolls_back_business_and_has_no_false_success_event(self):
        def deny(_a,_b,sql,*_args):
            if sql.startswith('INSERT INTO business_operation_logs'):raise RuntimeError('fixture audit unavailable')
        event.listen(self.engine,'before_cursor_execute',deny)
        with self.assertRaises(AuditPersistenceError):
            with bind(self.db,self.actor,'products',(Product,)):
                self.session.add(Product(sku='FAIL',name='不可保存'));self.session.commit()
        self.session.rollback();event.remove(self.engine,'before_cursor_execute',deny)
        self.assertEqual(list(self.session.scalars(select(Product))),[]);self.assertEqual(self.rows(),[])

    def test_savepoint_rollback_keeps_outer_change_and_discards_aborted_create(self):
        row=self.product()
        with bind(self.db,self.actor,'products',(Product,)):
            row.name='外层';self.session.flush()
            try:
                with self.session.begin_nested():
                    row.name='应撤销';self.session.add(Product(sku='GHOST',name='应撤销新建'));self.session.flush()
                    raise ValueError('rollback savepoint')
            except ValueError:pass
            self.session.commit()
        self.assertEqual(len(self.rows()),1);self.assertEqual(self.rows()[0].before_values['name'],'原名')
        self.assertEqual(self.rows()[0].after_values['name'],'外层')
        self.assertEqual(list(self.session.scalars(select(Product.sku))),['P1'])

    def test_password_is_only_a_boolean_event_and_audit_survives_user_deletion(self):
        row=User(username='target',password_hash='original-secret',role='operator')
        with bind(self.db,self.actor,'accounts',(User,)):
            self.session.add(row);self.session.commit()
        with bind(self.db,self.actor,'accounts',(User,),'reset_password'):
            row.password_hash='new-secret';self.session.commit()
        values=json.dumps([log.before_values|log.after_values for log in self.rows()])
        self.assertNotIn('original-secret',values);self.assertNotIn('new-secret',values);self.assertNotIn('password_hash',values)
        self.assertTrue(self.rows()[0].after_values['password_set']);self.assertTrue(self.rows()[1].after_values['password_changed'])
        with bind(self.db,self.actor,'accounts',(User,)):
            self.session.delete(row);self.session.commit()
        self.assertEqual(len(self.rows()),3)

    def test_unloaded_prior_values_are_marked_unknown_and_decimal_snapshots_use_persisted_precision(self):
        row=self.product();key=row.id;self.session.expunge_all()
        row=self.session.scalar(select(Product).options(load_only(Product.id,Product.sku)).where(Product.id==key))
        with bind(self.db,self.actor,'products',(Product,)):
            row.name='新名';self.session.commit()
        self.assertIn('name',self.rows()[0].unknown_before_fields)
        self.assertNotIn('name',self.rows()[0].before_values)
        with bind(self.db,self.actor,'batches',(InventoryBatch,)):
            batch=InventoryBatch(product_id=key,batch_no='B',quantity=1,remaining_quantity=1,
                purchase_price=D('1.23456789'),unit_cost=D('1.23456789'),arrived_at=datetime(2026,9,1))
            self.session.add(batch);self.session.commit()
        stored=self.session.execute(select(InventoryBatch.purchase_price).where(InventoryBatch.id==batch.id)).scalar_one()
        self.assertEqual(self.rows()[-1].after_values['purchase_price'],format(stored,'f'))

    def test_policy_excludes_existing_history_and_authentication_reads(self):
        for model,fields in FIELDS.items():
            self.assertTrue(set(fields)<=set(inspect(model).column_attrs.keys()),model.__name__)
        for path,methods in [('/sales/import-orders',{'POST'}),('/sales/import-stream',{'POST'}),('/sales/1/rollback',{'POST'}),
                             ('/batches/1',{'PUT'}),('/auth/login',{'POST'}),('/products',{'GET'})]:
            self.assertIsNone(policy(path,methods))
        self.assertIsNotNone(policy('/sales/pending-confirmations/retry',{'POST'}))


class AuditRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flag=patch.dict(os.environ,{'FIFO_OPERATION_AUDIT_ENABLED':'1'});self.flag.start();self.addCleanup(self.flag.stop)
        self.engine=create_engine('sqlite://');Base.metadata.create_all(self.engine)
        self.session=Session(self.engine,expire_on_commit=False);self.db=Facade(self.session)
        self.session.add(Store(id=7,platform_store_id='7',name='店铺7',country_or_region='CN',settlement_currency='CNY'))
        self.session.add(User(id=1,username='actor',password_hash='fixture-only',role='admin'));self.session.commit()
        self.actor=NS(id=1,username='actor',role='admin',store_id=None)
        from routers import products,auth,finance_masters,operation_logs,batches,reports
        self.app=FastAPI()
        for router in (products.router,auth.router,finance_masters.router,operation_logs.router,batches.router,reports.router):self.app.include_router(router)
        async def db():yield self.db
        self.app.dependency_overrides[get_db]=db
        for dependency in (RequireAnyRole,RequireAdmin,RequireOperator,get_current_user):self.app.dependency_overrides[dependency]=lambda:self.actor
        self.client=AsyncClient(transport=ASGITransport(app=self.app),base_url='http://fixture')
    async def asyncTearDown(self):await self.client.aclose();self.session.close();self.engine.dispose()
    def rows(self):return list(self.session.scalars(select(BusinessOperationLog)))

    async def test_actual_product_and_master_routes_keep_old_permissions_and_rollback_on_audit_failure(self):
        self.actor=NS(id=1,username='actor',role='viewer',store_id=7)
        response=await self.client.post('/products',data={'sku':'P1','name':'商品','product_type':'stable','safe_stock_quantity':'2'})
        self.assertEqual(response.status_code,200,response.text)
        key=response.json()['id'];self.assertEqual(len(self.rows()),1)
        response=await self.client.put(f'/products/{key}',data={'sku':'P1','name':'更名','product_type':'stable','safe_stock_quantity':'3'})
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(len(self.rows()),2)
        self.assertEqual(self.rows()[1].before_values['safe_stock_quantity'],2)
        self.actor=NS(id=1,username='actor',role='admin',store_id=None)
        response=await self.client.post('/finance-masters/product-lines',json={'code':'A','name':'分类'})
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(self.rows()[-1].module,'masters')
        BusinessOperationLog.__table__.drop(self.engine)
        response=await self.client.put(f'/products/{key}',data={'sku':'P1','name':'不能提交'})
        self.assertEqual(response.status_code,503,response.text)
        self.assertEqual(self.session.get(Product,key).name,'更名')

    async def test_account_route_never_captures_request_password_and_keeps_actor_snapshot(self):
        with patch('routers.auth.hash_password',return_value='hashed-sensitive-fixture'):
            response=await self.client.post('/auth/users',json={'username':'target','password':'request-sensitive','role':'operator'})
        self.assertEqual(response.status_code,200,response.text)
        values=json.dumps([row.before_values|row.after_values for row in self.rows()])
        self.assertNotIn('request-sensitive',values);self.assertNotIn('hashed-sensitive-fixture',values)
        self.assertEqual(self.rows()[0].actor_username,'actor')
        self.assertEqual(self.rows()[0].module,'accounts')

    async def test_batch_creation_and_mapping_components_share_action_group_and_keep_all_values(self):
        self.session.add(Product(id=1,sku='P',name='商品'));self.session.commit()
        response=await self.client.post('/batches',json={'product_id':1,'quantity':10,'purchase_price':'3.33','shipping_cost':'10','arrived_at':'2026-09-01T00:00:00'})
        self.assertEqual(response.status_code,200,response.text)
        log=self.rows()[0];self.assertEqual((log.module,log.entity_type),('batches','inventory_batches'))
        self.assertEqual(log.after_values['quantity'],10);self.assertEqual(log.after_values['shipping_cost'],'10.0000')
        response=await self.client.post('/finance-masters/platform-sku-mappings',json={'platform_sku_ids':['A','B'],'components':[{'product_id':1,'quantity_per_sale':2}]})
        self.assertEqual(response.status_code,201,response.text)
        records=[row for row in self.rows() if row.module=='masters']
        self.assertEqual(len(records),4);self.assertEqual(len({row.group_id for row in records}),1)
        self.assertEqual(sum(row.entity_type=='platform_sku_components' for row in records),2)
        mapping=response.json()[0]
        response=await self.client.put(f"/finance-masters/platform-sku-mappings/{mapping['id']}",json={'platform_sku_id':'A','is_active':True,'components':[{'product_id':1,'quantity_per_sale':3}]})
        self.assertEqual(response.status_code,200,response.text)
        log=self.rows()[-1];self.assertEqual(log.entity_type,'platform_sku_components')
        self.assertEqual((log.before_values['quantity_per_sale'],log.after_values['quantity_per_sale']),(2,3))

    async def test_saved_profit_values_are_logged_but_failed_audit_rolls_back_report(self):
        with patch('routers.reports._prepare_profit_loss_oa_values',new=AsyncMock(return_value=(1,{}))), \
             patch('routers.reports._store_profit_loss_response',new=AsyncMock(return_value={})):  # 只隔离响应计算和外部OA
            body={'store_id':7,'report_month':'2026-09-01','manual_values':{'advertising':{'current':'12.34'}},'remarks':{'advertising':'说明'}}
            # 响应模型校验在提交后；使用路由endpoint直接验证事务，仍执行实际保存函数。
            update_store_profit_loss=next(route.endpoint for route in self.app.routes if getattr(route,'path',None)=='/reports/store-profit-loss' and 'PUT' in route.methods)
            from schemas import StoreProfitLossUpdateRequest
            await update_store_profit_loss(StoreProfitLossUpdateRequest(**body),db=self.db,user=self.actor)
            saved=self.session.scalar(select(StoreProfitLossReport))
            self.assertEqual(self.rows()[-1].module,'reports')
            self.assertIn('manual_values',self.rows()[-1].after_values)
            def deny(_a,_b,sql,*_rest):
                if sql.startswith('INSERT INTO business_operation_logs'):raise RuntimeError('fixture')
            event.listen(self.engine,'before_cursor_execute',deny)
            body['remarks']={'advertising':'不应保存'}
            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as raised:
                await update_store_profit_loss(StoreProfitLossUpdateRequest(**body),db=self.db,user=self.actor)
            self.assertEqual(raised.exception.status_code,503)
            event.remove(self.engine,'before_cursor_execute',deny)
            self.session.refresh(saved);self.assertNotIn('不应保存',str(saved.remarks))


class AuditPendingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from tests import test_sales_pending as fixtures
        fixtures.PendingSalesTests.setUp(self)
        self.request=fixtures.PendingSalesTests.request.__get__(self)
        self.upload=fixtures.PendingSalesTests.upload.__get__(self)
        self.actor.username='admin'
        item=patch.dict(os.environ,{'FIFO_OPERATION_AUDIT_ENABLED':'1'});item.start();self.addCleanup(item.stop)
    def tearDown(self):self.session.close();self.engine.dispose()

    async def test_replacing_pending_components_logs_old_delete_and_new_snapshot_in_same_group(self):
        await self.upload([{'name':'商品*2','quantity':20}])
        entry=self.session.scalar(select(SettlementEntry))
        old=self.session.scalar(select(SettlementEntryAllocation));old_id=old.id
        response=await self.request('POST','/sales/pending-confirmations/retry',json={
            'entry_ids':[entry.id],'components':[{'product_id':1,'multiplier':1}]})
        self.assertEqual(response.status_code,200,response.text)
        logs=list(self.session.scalars(select(BusinessOperationLog)))
        components=[row for row in logs if row.entity_type=='settlement_entry_allocations']
        self.assertEqual(len(components),2)
        deleted=next(row for row in components if row.action=='delete')
        self.assertEqual(deleted.entity_id,old_id)
        self.assertEqual((deleted.before_values['multiplier'],deleted.before_values['quantity']),(2,40))
        self.assertEqual(deleted.after_values,{})
        added=next(row for row in components if row.action=='process_pending')
        self.assertEqual(added.before_values,{})
        self.assertEqual((added.after_values['multiplier'],added.after_values['quantity']),(1,20))
        self.assertEqual({row.store_id for row in logs},{7})
        self.assertEqual(len({row.group_id for row in logs}),1)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)
        self.assertFalse(list(self.session.scalars(select(Sale))))
        from routers.operation_logs import router
        self.app.include_router(router)
        visible=await self.request('GET',f'/operation-logs/sales_operations/{deleted.id}')
        self.assertEqual(visible.status_code,200,visible.text)
        self.assertEqual(visible.json()['before_values']['quantity'],40)
        for role in ('operator','viewer'):
            self.actor.role=role
            for store_id in (7,8,None):
                self.actor.store_id=store_id
                self.assertEqual((await self.request('GET',f'/operation-logs/sales_operations/{deleted.id}')).status_code,403)

    async def test_deleted_only_component_uses_source_store_when_admin_has_no_store_binding(self):
        await self.upload([{'name':'商品*2','quantity':20}])
        entry=self.session.scalar(select(SettlementEntry))
        self.session.get(Product,1).name='改名后无法按原名识别';self.session.commit()
        response=await self.request('POST','/sales/pending-confirmations/retry',json={'entry_ids':[entry.id]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(list(self.session.scalars(select(SettlementEntryAllocation))),[])
        logs=list(self.session.scalars(select(BusinessOperationLog).where(
            BusinessOperationLog.entity_type=='settlement_entry_allocations')))
        self.assertEqual(len(logs),1)
        self.assertEqual((logs[0].action,logs[0].store_id),('delete',7))
        self.assertEqual(logs[0].before_values['quantity'],40)

    async def test_replacement_audit_failure_restores_old_components_fifo_and_source_state(self):
        await self.upload([{'name':'商品*8','quantity':2}])
        entry=self.session.scalar(select(SettlementEntry))
        before=(entry.mapping_error,copy.deepcopy(entry.raw_data))
        old=self.session.scalar(select(SettlementEntryAllocation));old_id=old.id
        def deny(_a,_b,sql,*_rest):
            if sql.startswith('INSERT INTO business_operation_logs'):raise RuntimeError('fixture audit failure')
        event.listen(self.engine,'before_cursor_execute',deny)
        response=await self.request('POST','/sales/pending-confirmations/retry',json={
            'entry_ids':[entry.id],'components':[{'product_id':1,'multiplier':2}]})
        self.assertEqual(response.status_code,503,response.text)
        event.remove(self.engine,'before_cursor_execute',deny)
        self.session.refresh(entry)
        restored=list(self.session.scalars(select(SettlementEntryAllocation)))
        self.assertEqual([(row.id,row.multiplier,row.quantity) for row in restored],[(old_id,8,16)])
        self.assertEqual((entry.mapping_error,entry.raw_data),before)
        self.assertEqual(entry.mapping_status,'pending_confirmation')
        self.assertIsNone(entry.sale_id)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)
        for model in (Sale,SaleCostDetail,BusinessOperationLog):self.assertEqual(list(self.session.scalars(select(model))),[])

    async def test_unassigned_source_never_inherits_the_admin_store_for_deleted_component_logs(self):
        await self.upload([{'name':'商品*2','quantity':20}])
        entry=self.session.scalar(select(SettlementEntry))
        entry.store_id=None;self.session.commit()
        self.actor.store_id=8
        response=await self.request('POST','/sales/pending-confirmations/retry',json={'entry_ids':[entry.id]})
        self.assertEqual(response.status_code,200,response.text)
        logs=list(self.session.scalars(select(BusinessOperationLog)))
        self.assertTrue(any(row.action=='delete' for row in logs))
        self.assertEqual({row.store_id for row in logs},{None})
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)
        from routers.operation_logs import router
        self.app.include_router(router)
        self.actor.role='viewer'
        visible=await self.request('GET','/operation-logs',params={'source':'sales_operations','group_id':logs[0].group_id})
        self.assertEqual(visible.status_code,403,visible.text)

    async def test_retry_records_scoped_entry_and_components_without_duplicate_import_logs(self):
        response=await self.upload([{'name':'未识别','quantity':2}]);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(list(self.session.scalars(select(BusinessOperationLog))),[])
        entry=self.session.scalar(select(SettlementEntry))
        response=await self.request('POST','/sales/pending-confirmations/retry',json={'entry_ids':[entry.id],'components':[{'product_id':1,'multiplier':2}]})
        self.assertEqual(response.status_code,200,response.text)
        logs=list(self.session.scalars(select(BusinessOperationLog)))
        self.assertEqual({row.entity_type for row in logs},{'settlement_entries','settlement_entry_allocations'})
        self.assertEqual({row.store_id for row in logs},{7});self.assertEqual(len({row.group_id for row in logs}),1)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,6)
        response=await self.request('POST','/sales/pending-confirmations/retry',json={'entry_ids':[entry.id]})
        self.assertEqual(response.json()['skipped_rows'],1)
        self.assertEqual(len(list(self.session.scalars(select(BusinessOperationLog)))),len(logs))

    async def test_audit_failure_rolls_back_fifo_stock_sales_costs_and_pending_state(self):
        await self.upload([{'name':'未识别','quantity':2}]);entry=self.session.scalar(select(SettlementEntry))
        def deny(_a,_b,sql,*_rest):
            if sql.startswith('INSERT INTO business_operation_logs'):raise RuntimeError('fixture')
        event.listen(self.engine,'before_cursor_execute',deny)
        response=await self.request('POST','/sales/pending-confirmations/retry',json={'entry_ids':[entry.id],'components':[{'product_id':1,'multiplier':2}]})
        self.assertEqual(response.status_code,503,response.text)
        event.remove(self.engine,'before_cursor_execute',deny)
        self.assertEqual(self.session.get(InventoryBatch,1).remaining_quantity,10)
        for model in (Sale,SaleCostDetail,SettlementEntryAllocation,BusinessOperationLog):self.assertEqual(list(self.session.scalars(select(model))),[])
        self.assertEqual(self.session.get(SettlementEntry,entry.id).mapping_status,'pending_confirmation')


if __name__=='__main__':unittest.main()
