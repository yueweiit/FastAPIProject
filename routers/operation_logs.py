"""统一操作日志，只读聚合业务历史和扩展审计。"""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import OperationalError, ProgrammingError

from auth import RequireAdmin
from database import get_db
from models import Store, User
from services import operation_logs as logs
from services.log_http import PrivateLogRoute

router=APIRouter(prefix='/operation-logs',tags=['操作日志'],route_class=PrivateLogRoute)
Source=Literal['batch','stock_count','sales_import','sales_rollback','finance','products','accounts','masters','batch_create','sales_operations','report_changes']


@router.get('/options')
async def options(db:AsyncSession=Depends(get_db),user:User=Depends(RequireAdmin)):
    stores=select(Store.id,Store.name).order_by(Store.id)
    try:
        ready=await logs.audit_available(db)
        return {'sources':logs.allowed_sources(user,ready),'stores':[dict(row._mapping) for row in (await db.execute(stores)).all()],
                'audit_enabled':logs.business_audit.enabled(),'audit_available':ready}
    except (OperationalError,ProgrammingError) as error:
        raise HTTPException(503,detail={'code':'operation_logs_source_unavailable',
            'message':'日志分类暂不可用，请核对数据库结构后重试。'}) from error


@router.get('')
async def list_operation_logs(source:Source|None=None,store_id:int|None=Query(None,gt=0),
    start:date|None=None,end:date|None=None,keyword:str|None=Query(None,max_length=255),
    actor:str|None=Query(None,max_length=64),action:str|None=Query(None,max_length=32),
    group_id:str|None=Query(None,min_length=32,max_length=32,pattern=r'^[0-9a-f]+$'),
    page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),
    db:AsyncSession=Depends(get_db),user:User=Depends(RequireAdmin)):
    if (start and start.year<1900) or (end and (end.year<1900 or end.year>=9999)) or (start and end and start>end):
        raise HTTPException(422,detail={'code':'operation_logs_invalid_dates','message':'请填写有效的开始、结束日期，且结束日期不早于开始日期。'})
    try:
        return await logs.list_logs(db,user,source=source,store_id=store_id,start=start,end=end,
            keyword=(keyword or '').strip(),actor=(actor or '').strip(),action=(action or '').strip(),page=page,page_size=page_size,group_id=group_id)
    except (OperationalError,ProgrammingError) as error:
        raise HTTPException(503,detail={'code':'operation_logs_source_unavailable',
            'message':'所选日志来源暂不可用，请核对数据库结构或切换单独分类重试。'}) from error


@router.get('/{source}/{row_id}')
async def operation_log_detail(source:Source,row_id:int,db:AsyncSession=Depends(get_db),user:User=Depends(RequireAdmin)):
    if row_id<=0: raise HTTPException(422,detail={'code':'operation_log_invalid_id','message':'记录编号无效。'})
    try:
        return await logs.detail(db,user,source,row_id)
    except (OperationalError,ProgrammingError) as error:
        raise HTTPException(503,detail={'code':'operation_logs_source_unavailable',
            'message':'日志来源暂不可用，请核对数据库结构后重试。'}) from error
