"""带身份上下文的事务审计；仅白名单业务对象，提交前批量读取实际落库值。"""
import copy
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models import (BusinessOperationLog, InventoryBatch, PlatformSkuComponent, PlatformSkuMapping,
                    Product, ProductLine, Sale, SettlementEntry, SettlementEntryAllocation,
                    Store, StoreProduct, StoreProfitLossReport, User)
from services.log_http import PrivateLogRoute

TABLE_NAME='business_operation_logs'
CTX='business_audit_context'; PENDING='business_audit_pending'; CHECKPOINTS='business_audit_checkpoints'
FIELDS={
    Product:('sku','name','image','product_type','safe_stock_quantity'),
    User:('username','role','is_active','store_id'),
    Store:('platform','platform_store_id','name','country_or_region','settlement_currency','timezone','legal_entity','is_active'),
    ProductLine:('code','name','is_active'),
    StoreProduct:('store_id','product_id','product_line_id','platform_product_id','platform_sku_id','store_sku','effective_from','effective_to','is_active'),
    PlatformSkuMapping:('platform','platform_sku_id','is_active'),
    PlatformSkuComponent:('platform_sku_mapping_id','product_id','quantity_per_sale'),
    InventoryBatch:('product_id','batch_no','quantity','remaining_quantity','purchase_price','unit_cost','total_amount',
        'shipping_cost','last_mile_cost','other_cost','dingtalk_order_no','shipping_cost_no','last_mile_cost_no','other_cost_no','shipping_date','arrived_at'),
    Sale:('product_id','store_id','order_no','quantity','selling_price','platform_fee','total_cost','profit','sold_at'),
    SettlementEntry:('product_id','store_id','order_id','platform_sku_id','product_name','transaction_type','quantity',
        'net_product_sales','settlement_total','currency','mapping_status','mapping_error','sale_id'),
    SettlementEntryAllocation:('settlement_entry_id','product_id','sale_id','component_name','multiplier','quantity'),
    StoreProfitLossReport:('store_id','report_month','manual_values','remarks','report_metadata'),
}
SECRET_KEYS={'password','password_hash','old_password','new_password','token','access_token','refresh_token',
    'csrf_token','client_secret','private_key','secret','session_hash','authorization','cookie','request_key','payload_hash'}


def enabled():
    return os.getenv('FIFO_OPERATION_AUDIT_ENABLED','0').strip().lower() in {'1','true','yes'}


def safe(value):
    if isinstance(value,Decimal): return format(value,'f')
    if isinstance(value,(datetime,date)): return value.isoformat()
    if isinstance(value,dict): return {str(key):safe(item) for key,item in value.items() if str(key).casefold() not in SECRET_KEYS}
    if isinstance(value,(list,tuple)): return [safe(item) for item in value]
    return value


class AuditPersistenceError(RuntimeError):
    pass


@dataclass
class Pending:
    obj: object
    before: dict
    unknown: set
    created: bool
    password_changed: bool=False


def session_for(db):
    session=getattr(db,'sync_session',None) or getattr(db,'session',None)
    if isinstance(db,Session): session=db
    if not isinstance(session,Session): raise AuditPersistenceError('audit session unavailable')
    return session


@contextmanager
def bind(db,actor,module,models,action=None):
    session=session_for(db)
    old=session.info.get(CTX)
    session.info[CTX]={'actor_id':actor.id,'actor_username':actor.username,'actor_role':actor.role,
        'module':module,'models':set(models),'action':action,'group_id':uuid4().hex}
    try: yield
    finally:
        session.info.pop(PENDING,None);session.info.pop(CHECKPOINTS,None)
        if old is None: session.info.pop(CTX,None)
        else: session.info[CTX]=old


@event.listens_for(Session,'before_flush')
def collect(session,_context,_instances):
    ctx=session.info.get(CTX)
    if not ctx or session.info.get('business_audit_writing'): return
    pending=session.info.setdefault(PENDING,{})
    for obj in set(session.new)|set(session.dirty)|set(session.deleted):
        model=type(obj)
        if model not in ctx['models']: continue
        state=inspect(obj);created=obj in session.new
        changed=[key for key in FIELDS[model] if state.attrs[key].history.has_changes()]
        password=(model is User and state.attrs.password_hash.history.has_changes())
        if not created and obj not in session.deleted and not changed and not password: continue
        if id(obj) not in pending:
            before={};unknown=set()
            if not created:
                for key in FIELDS[model]:
                    history=state.attrs[key].history
                    if history.has_changes():
                        if history.deleted: before[key]=safe(history.deleted[0])
                        else: unknown.add(key)
                    elif key in state.dict: before[key]=safe(state.dict[key])
                    else: unknown.add(key)
            pending[id(obj)]=Pending(obj,before,unknown,created)
        pending[id(obj)].password_changed |= password


def label(values,identity):
    for key in ('batch_no','order_no','order_id','username','store_sku','platform_sku_id','component_name','report_month'):
        if values.get(key): return str(values[key])[:512]
    parts=[values.get('sku') or values.get('code'),values.get('name')]
    return ' · '.join(str(part) for part in parts if part)[:512] or str(identity)


@event.listens_for(Session,'before_commit')
def write(session):
    ctx=session.info.get(CTX)
    if not ctx or session.in_nested_transaction() or session.info.get('business_audit_writing'): return
    session.flush()  # 先完成业务写入/默认值，审计读取数据库实际精度，仍在同一事务。
    pending=session.info.get(PENDING,{})
    if not pending: return
    try:
        groups={};valid=[]
        for item in pending.values():
            state=inspect(item.obj)
            if state.transient or not state.identity or (item.created and state.deleted): continue
            model=type(item.obj);identity=state.identity[0]
            valid.append((item,model,identity,state.deleted))
            if not state.deleted: groups.setdefault(model,set()).add(identity)
        after={}
        for model,ids in groups.items():
            columns=[model.id]+[getattr(model,key) for key in FIELDS[model]]
            after[model]={row[0]:{key:safe(row[index+1]) for index,key in enumerate(FIELDS[model])}
                for row in session.execute(select(*columns).where(model.id.in_(ids))).all()}
        allocation_entries={values.get('settlement_entry_id') for values in after.get(SettlementEntryAllocation,{}).values()}
        allocation_entries.update(item.before.get('settlement_entry_id') for item,model,_identity,_deleted in valid
            if model is SettlementEntryAllocation)
        allocation_entries.discard(None)
        owners={row.id:row.store_id for row in session.execute(select(SettlementEntry.id,SettlementEntry.store_id)
            .where(SettlementEntry.id.in_(allocation_entries))).all()} if allocation_entries else {}
        audit=[]
        for item,model,identity,deleted in valid:
            values={} if deleted else after.get(model,{}).get(identity)
            if values is None: raise AuditPersistenceError('business row disappeared')
            if item.password_changed and not deleted:
                values={**values,('password_set' if item.created else 'password_changed'):True}
            if not item.created and not deleted and item.before==values and not item.unknown: continue
            data=values or item.before
            store_id=data.get('store_id')
            if model is Store: store_id=identity
            if model is SettlementEntryAllocation: store_id=owners.get(data.get('settlement_entry_id'))
            audit.append(BusinessOperationLog(module=ctx['module'],action='delete' if deleted else ctx['action'] or ('create' if item.created else 'update'),
                entity_type=model.__tablename__,entity_id=identity,entity_label=label(data,identity),
                product_id=identity if model is Product else data.get('product_id'),store_id=store_id,
                actor_id=ctx['actor_id'],actor_username=ctx['actor_username'],actor_role=ctx['actor_role'],group_id=ctx['group_id'],
                before_values=item.before,after_values=values,unknown_before_fields=sorted(item.unknown)))
        session.info['business_audit_writing']=True
        session.add_all(audit);session.flush()
    except Exception as error:
        raise AuditPersistenceError('business audit persistence failed') from error
    finally:
        session.info.pop('business_audit_writing',None)


@event.listens_for(Session,'after_transaction_create')
def checkpoint(session,transaction):
    if transaction.nested and session.info.get(CTX):
        session.info.setdefault(CHECKPOINTS,{})[transaction]={key:Pending(item.obj,copy.deepcopy(item.before),set(item.unknown),item.created,item.password_changed)
            for key,item in session.info.get(PENDING,{}).items()}


@event.listens_for(Session,'after_soft_rollback')
def restore(session,transaction):
    if transaction.nested:
        session.info[PENDING]=session.info.get(CHECKPOINTS,{}).pop(transaction,{})


@event.listens_for(Session,'after_transaction_end')
def clear(session,transaction):
    if transaction.parent is None:
        session.info.pop(PENDING,None);session.info.pop(CHECKPOINTS,None)


def policy(path,methods):
    if not set(methods or ()) & {'POST','PUT','PATCH','DELETE'}: return None
    if path.startswith('/products') and path not in ('/products/import/preview','/products/import/result'):
        return 'products',(Product,),'import_create' if path=='/products/import' else None
    if path.startswith('/auth/users') or path=='/auth/me/password':
        action={'/password':'change_password' if path=='/auth/me/password' else 'reset_password',
            '/status':'change_status','/role':'change_role','/store':'bind_store'}
        return 'accounts',(User,),next((value for suffix,value in action.items() if path.endswith(suffix)),None)
    if path.startswith('/finance-masters'): return 'masters',(Store,ProductLine,StoreProduct,PlatformSkuMapping,PlatformSkuComponent),None
    if path=='/batches' and 'POST' in methods: return 'batches',(InventoryBatch,),None
    if path=='/sales' and 'POST' in methods: return 'sales',(Sale,),None
    if path=='/sales/pending-confirmations/retry': return 'sales',(SettlementEntry,SettlementEntryAllocation),'process_pending'
    if path=='/reports/store-profit-loss' and 'PUT' in methods: return 'reports',(StoreProfitLossReport,),None
    return None


class BusinessAuditRoute(PrivateLogRoute):
    def __init__(self,path,endpoint,**kwargs):
        config=policy(path,kwargs.get('methods'))
        if config and not getattr(endpoint,'_business_audited',False):
            original=endpoint
            @wraps(original)
            async def audited(*args,**params):
                actor=params.get('user') or params.get('current_user');db=params.get('db')
                if not enabled() or actor is None or db is None: return await original(*args,**params)
                try:
                    # 写业务前检查结构，防止缺表到提交时才发现；不建表、不读取凭据。
                    session=session_for(db)
                    with session.no_autoflush:
                        await db.execute(select(BusinessOperationLog.id).limit(0))
                except (AuditPersistenceError,SQLAlchemyError) as error:
                    await db.rollback()
                    raise HTTPException(503,detail={'code':'operation_audit_unavailable',
                        'message':'操作日志保存条件未就绪，本次业务未提交；请完成审计表准备或联系管理员。'}) from error
                try:
                    with bind(db,actor,*config): return await original(*args,**params)
                except AuditPersistenceError as error:
                    await db.rollback()
                    raise HTTPException(503,detail={'code':'operation_audit_storage_failed',
                        'message':'操作日志保存失败，本次业务已回滚，请重试或联系管理员。'}) from error
            audited._business_audited=True
            endpoint=audited
        super().__init__(path,endpoint,**kwargs)
