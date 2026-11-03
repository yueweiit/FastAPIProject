"""统一读取既有历史与新增业务审计；查询不修改来源、不补造历史。"""
from datetime import datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import String, case, cast, column, func, literal, or_, select, table, union_all

from models import (BatchChangeLog, BusinessOperationLog, FinancialAuditEvent, InventoryBatch, InventoryStockCount,
                    Product, SalesImportBatch, Store, User)
from services import business_audit
from services.finance_config import financial_controls_enabled
from services.inventory_activity import business_today

BATCH_FIELDS = {'product_id','batch_no','quantity','remaining_quantity','purchase_price','total_amount',
    'unit_cost','dingtalk_order_no','shipping_cost','shipping_cost_no','last_mile_cost','last_mile_cost_no',
    'other_cost','other_cost_no','shipping_date','arrived_at','impairment_store_ids','impairment_store_names',
    'impairment_store_history','voided_at','voided_by','void_reason'}
# 按真实实体逐项允许业务字段，不能用近似字段名或开放整份审计JSON。
FINANCE_FIELDS = {
    'inventory_cost_adjustments': {'id','source_ref','category','allocation_basis','amount','currency',
        'exchange_rate','rate_date','document_date','amount_cny','is_final','note','reverses_id'},
    'financial_expenses': {'id','store_id','expense_date','category','source_ref','amount','currency',
        'exchange_rate','amount_cny','status','settlement_entry_id','reverses_id','note','posted_at'},
    'financial_expense_sources': {'id','store_id','report_month','category','mode'},
    'financial_receipts': {'id','store_id','payout_id','receipt_date','source_ref','amount','currency',
        'exchange_rate','amount_cny','note','reverses_id'},
    'settlement_classifications': {'id','settlement_entry_id','breakdown','note'},
    'financial_report_versions': {'id','store_id','report_month','version','calculation_version','status','note'},
}


def fail(code, message, status=403):
    raise HTTPException(status, detail={'code':code,'message':message})


BUSINESS_SOURCES={'products':'products','accounts':'accounts','masters':'masters','batch_create':'batches',
                  'sales_operations':'sales','report_changes':'reports'}


async def audit_available(db):
    try: session=business_audit.session_for(db)
    except business_audit.AuditPersistenceError: return False
    if 'operation_audit_table_available' not in session.info:
        dialect=session.get_bind().dialect.name
        if dialect=='mysql':
            tables=table('tables',column('table_schema'),column('table_name'),schema='information_schema')
            stmt=select(func.count()).select_from(tables).where(tables.c.table_schema==func.database(),tables.c.table_name==business_audit.TABLE_NAME)
        elif dialect=='sqlite':
            tables=table('sqlite_master',column('type'),column('name'))
            stmt=select(func.count()).select_from(tables).where(tables.c.type=='table',tables.c.name==business_audit.TABLE_NAME)
        else: return False
        session.info['operation_audit_table_available']=bool(await db.scalar(stmt))
    return session.info['operation_audit_table_available']


def allowed_sources(user,audit_ready=None):
    if user.role!='admin': return []
    result = ['batch','stock_count','sales_import','sales_rollback']
    if financial_controls_enabled(): result.append('finance')
    if audit_ready is None: audit_ready=business_audit.enabled()
    if audit_ready:
        result += ['products','masters']
        result += ['accounts','batch_create','sales_operations','report_changes']
    return result


def authorize(user, source, store_id=None,audit_ready=None):
    if user.role!='admin':
        fail('operation_logs_admin_required','仅管理员可查看操作日志。')
    if source and source not in allowed_sources(user,audit_ready):
        fail('operation_logs_source_forbidden','当前账号无权查看该类记录。')
    if store_id is not None and user.role != 'admin' and store_id != user.store_id:
        fail('operation_logs_store_forbidden','无权查看其他店铺的记录。')


def literal_text(value):
    return literal(value, type_=String)


def source_query(source, user):
    """投影仅含列表必要字段；来源权限在计数和分页前统一生效。"""
    store_id = literal(None)
    store_name = literal_text(None)
    product_id, sku, product_name = literal(None), literal_text(None), literal_text(None)
    group_id,entity_type=literal_text(None),literal_text(None)
    if source == 'batch':
        model = BatchChangeLog
        product_id = func.coalesce(InventoryBatch.product_id,
            model.after_values['product_id'].as_integer(), model.before_values['product_id'].as_integer())
        sku, product_name = Product.sku, Product.name
        stamp, actor_id, actor_name, reference = model.created_at, model.actor_id, model.actor_username, model.batch_no
        action, basis = model.action, 'utc'
        related_id = model.batch_id
        stmt = select().select_from(model).outerjoin(InventoryBatch, InventoryBatch.id == model.batch_id).outerjoin(Product, Product.id == product_id)
    elif source == 'stock_count':
        model = InventoryStockCount
        stamp, actor_id, actor_name = model.created_at, model.created_by, User.username
        product_id, sku, product_name = model.product_id, Product.sku, Product.name
        reference, action, basis = Product.sku, literal_text('count_saved'), 'utc'
        related_id = model.product_id
        stmt = select().select_from(model).outerjoin(Product, Product.id == model.product_id).outerjoin(User, User.id == model.created_by)
        if user.role != 'admin': stmt = stmt.where(model.count_date == business_today())
    elif source in ('sales_import','sales_rollback'):
        model = SalesImportBatch
        rollback = source == 'sales_rollback'
        stamp = model.rolled_back_at if rollback else model.imported_at
        actor_id = model.rolled_back_by_user_id if rollback else model.user_id
        actor_name, reference = User.username, model.file_name
        action = literal_text('rollback') if rollback else case((model.file_type=='manual_settlement',literal_text('manual_settlement')),else_=literal_text('import'))
        basis = 'original'
        related_id = model.id
        store_id, store_name = model.store_id, Store.name
        stmt = select().select_from(model).outerjoin(User, User.id == actor_id).outerjoin(Store, Store.id == model.store_id)
        if rollback: stmt = stmt.where(model.rolled_back_at.is_not(None))
        if user.role != 'admin': stmt = stmt.where(model.store_id == user.store_id)
    elif source in BUSINESS_SOURCES:
        model=BusinessOperationLog
        stamp,actor_id,actor_name=model.recorded_at,model.actor_id,model.actor_username
        reference,action,basis=model.entity_label,model.action,'utc'
        related_id,product_id,store_id=model.entity_id,model.product_id,model.store_id
        group_id,entity_type=model.group_id,model.entity_type
        sku=case((model.entity_type=='products',func.coalesce(model.after_values['sku'].as_string(),model.before_values['sku'].as_string())),else_=Product.sku)
        product_name=case((model.entity_type=='products',func.coalesce(model.after_values['name'].as_string(),model.before_values['name'].as_string())),else_=Product.name)
        store_name=Store.name
        stmt=select().select_from(model).outerjoin(Product,Product.id==model.product_id).outerjoin(Store,Store.id==model.store_id).where(model.module==BUSINESS_SOURCES[source])
        if source in ('sales_operations','report_changes') and user.role!='admin': stmt=stmt.where(model.store_id==user.store_id)
    else:
        model = FinancialAuditEvent
        stamp, actor_id, actor_name = model.created_at, model.actor_id, User.username
        reference, action, basis = model.entity_type, model.action, 'original'
        related_id = model.entity_id
        store_id, store_name = model.store_id, Store.name
        # 与原按店铺审计入口一致：不增加无店铺的全局审计访问范围。
        stmt = select().select_from(model).join(Store, Store.id == model.store_id).outerjoin(User, User.id == model.actor_id)
    return stmt.with_only_columns(literal_text(source).label('source'),model.id.label('id'),
        stamp.label('recorded_at'),literal_text(basis).label('time_basis'),
        actor_id.label('actor_id'),actor_name.label('actor_name'),reference.label('reference'),
        action.label('action'),product_id.label('product_id'),sku.label('sku'),product_name.label('product_name'),
        store_id.label('store_id'),store_name.label('store_name'),related_id.label('related_id'),
        group_id.label('group_id'),entity_type.label('entity_type'))


def filtered_query(user, source=None, store_id=None, start=None, end=None, keyword=None, actor=None, action=None,audit_ready=None,group_id=None):
    authorize(user,source,store_id,audit_ready)
    sources = [source] if source else allowed_sources(user,audit_ready)
    if not sources: return None
    statements=[]
    for name in sources:
        stmt=source_query(name,user)
        columns=stmt.selected_columns
        # 日期按原记录的自然日筛选，UTC来源显式标注；不猜测旧数据库/主机时区。
        if start: stmt=stmt.where(columns.recorded_at >= datetime.combine(start,time.min))
        if end: stmt=stmt.where(columns.recorded_at < datetime.combine(end+timedelta(days=1),time.min))
        if store_id is not None: stmt=stmt.where(columns.store_id == store_id)
        if actor: stmt=stmt.where(columns.actor_name.contains(actor,autoescape=True))
        if action: stmt=stmt.where(columns.action == action)
        if group_id: stmt=stmt.where(columns.group_id==group_id)
        if keyword:
            conditions=[value.contains(keyword,autoescape=True) for value in (columns.reference,columns.sku,columns.product_name)]
            if name in BUSINESS_SOURCES:
                conditions += [cast(value,String).contains(keyword,autoescape=True) for value in (BusinessOperationLog.before_values,BusinessOperationLog.after_values)]
            stmt=stmt.where(or_(*conditions))
        statements.append(stmt)
    return union_all(*statements).subquery('operation_log_sources')


async def list_logs(db,user,*,source=None,store_id=None,start=None,end=None,keyword=None,actor=None,action=None,page=1,page_size=20,group_id=None):
    authorize(user,None)
    ready=await audit_available(db) if source is None or source in BUSINESS_SOURCES else False
    if business_audit.enabled() and not ready and (source is None or source in BUSINESS_SOURCES):
        fail('operation_logs_source_unavailable','扩展操作审计已启用，但审计表尚未就绪；请完成结构准备或单独查询既有分类。',503)
    query=filtered_query(user,source,store_id,start,end,keyword,actor,action,ready,group_id)
    if query is None: return {'items':[],'total':0,'page':1,'page_size':page_size}
    total=await db.scalar(select(func.count()).select_from(query))
    page=min(page,max(1,(total+page_size-1)//page_size))
    rows=(await db.execute(select(query).order_by(query.c.recorded_at.desc(),query.c.source,query.c.id.desc())
        .offset((page-1)*page_size).limit(page_size))).mappings().all()
    items=[dict(row) for row in rows]
    await add_summaries(db,items)
    return {'items':items,'total':total,'page':page,'page_size':page_size}


def safe_values(values, allowed):
    return {key:value for key,value in (values or {}).items() if key in allowed}


def finance_values(entity_type, values):
    result=business_audit.safe(safe_values(values,FINANCE_FIELDS.get(entity_type,set())))
    if isinstance(result.get('breakdown'),dict):
        from services.financial_controls import BREAKDOWN_LABELS
        result['breakdown']=safe_values(result['breakdown'],BREAKDOWN_LABELS)
    return result


async def add_summaries(db,items):
    """按已授权的一页ID批量补摘要，避免为每条日志读取一次详情。"""
    models={'batch':BatchChangeLog,'stock_count':InventoryStockCount,'sales_import':SalesImportBatch,
        'sales_rollback':SalesImportBatch,'finance':FinancialAuditEvent}
    models.update({source:BusinessOperationLog for source in BUSINESS_SOURCES})
    groups={}
    for item in items: groups.setdefault(models[item['source']],set()).add(item['id'])
    records={}
    for model,ids in groups.items():
        records[model]={row.id:row for row in (await db.execute(select(model).where(model.id.in_(ids)))).scalars().all()}
    for item in items:
        source=item['source'];row=records[models[source]].get(item['id'])
        if row is None: continue
        if source in ('batch','finance') or source in BUSINESS_SOURCES:
            if source=='finance':
                before,after=finance_values(row.entity_type,row.before_values),finance_values(row.entity_type,row.after_values)
            else:
                fields=BATCH_FIELDS if source=='batch' else business_fields(row.entity_type)
                before,after=safe_values(row.before_values,fields),safe_values(row.after_values,fields)
            item['summary']={'changed_fields':[key for key in sorted(before.keys()|after.keys()) if before.get(key)!=after.get(key)]}
        elif source=='stock_count':
            item['summary']={'count_date':row.count_date,'revision':row.revision,'difference':row.actual_quantity-row.system_quantity}
        else:
            item['summary']={key:getattr(row,key) for key in ('settlement_rows','sales_created','pending_confirmation','status')}


def business_fields(entity_type):
    fields=next((fields for model,fields in business_audit.FIELDS.items() if model.__tablename__==entity_type),())
    return set(fields)|{'password_set','password_changed'}


async def detail(db,user,source,row_id):
    authorize(user,None)
    ready=await audit_available(db) if source in BUSINESS_SOURCES else False
    if source in BUSINESS_SOURCES and business_audit.enabled() and not ready:
        fail('operation_logs_source_unavailable','扩展操作审计已启用，但审计表尚未就绪。',503)
    authorize(user,source,audit_ready=ready)
    query=filtered_query(user,source,audit_ready=ready)
    item=(await db.execute(select(query).where(query.c.id==row_id))).mappings().first()
    if item is None: fail('operation_log_not_found','记录不存在或不在当前可见范围。',404)
    item=dict(item)
    before,after,reason,kind={}, {}, None, 'snapshot'
    if source=='batch':
        row=await db.get(BatchChangeLog,row_id)
        before,after=safe_values(row.before_values,BATCH_FIELDS),safe_values(row.after_values,BATCH_FIELDS)
        reason=after.get('void_reason') if row.action=='void' else None
        kind='change'
        item['actor_role']=row.actor_role
        item['related_id']=row.batch_id
    elif source=='stock_count':
        row=await db.get(InventoryStockCount,row_id)
        # 盘点是不可覆盖版本，不伪造成一次修改的前后值。
        after={'count_date':row.count_date,'revision':row.revision,'system_quantity':row.system_quantity,
            'actual_quantity':row.actual_quantity,'difference':row.actual_quantity-row.system_quantity,'note':row.note}
        reason=row.note
    elif source in ('sales_import','sales_rollback'):
        row=await db.get(SalesImportBatch,row_id)
        after={key:getattr(row,key) for key in ('file_name','file_type','sheet_name','total_rows','settlement_rows',
            'sales_created','skipped_duplicates','pending_confirmation','status','imported_at','rolled_back_at')}
        item['related_id']=row.id
        kind='business_history'
    elif source in BUSINESS_SOURCES:
        row=await db.get(BusinessOperationLog,row_id)
        fields=business_fields(row.entity_type)
        before=business_audit.safe(safe_values(row.before_values,fields))
        after=business_audit.safe(safe_values(row.after_values,fields))
        item.update(actor_role=row.actor_role,unknown_before_fields=row.unknown_before_fields)
        reason=after.get('note')
        kind='change'
    else:
        row=await db.get(FinancialAuditEvent,row_id)
        before,after=finance_values(row.entity_type,row.before_values),finance_values(row.entity_type,row.after_values)
        reason=after.get('note')
        item.update(entity_type=row.entity_type,related_id=row.entity_id)
        kind='change'
    changes=[{'field':key,'before':before.get(key),'after':after.get(key),'before_known':key not in item.get('unknown_before_fields',[])}
        for key in sorted(before.keys()|after.keys()) if before.get(key)!=after.get(key)] if kind=='change' else []
    return {**item,'record_kind':kind,'before_values':before,'after_values':after,'changes':changes,'reason':reason}
