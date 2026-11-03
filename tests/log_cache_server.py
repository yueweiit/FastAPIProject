"""仅供浏览器缓存回归：临时端口、隔离SQLite、测试身份，不启动main或连接业务库。"""
import asyncio
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from auth import get_current_user
from routers import batches
from tests.test_operation_logs import OperationLogsTests


async def main():
    fixture=OperationLogsTests('test_combined_pagination_filters_and_original_time_markers')
    await fixture.asyncSetUp()
    app=fixture.app
    app.include_router(batches.router)
    counts={}
    server=None

    async def actor(request:Request):
        role=request.cookies.get('fixture-role')
        if role not in ('admin','operator','viewer'):raise HTTPException(401,detail='fixture anonymous')
        return SimpleNamespace(id=1,username='cache-fixture-user',role=role,store_id=7)
    app.dependency_overrides[get_current_user]=actor

    @app.middleware('http')
    async def requests(request,call_next):
        path=request.url.path
        counts[path]=counts.get(path,0)+1
        return await call_next(request)

    @app.get('/fixture')
    async def page():return HTMLResponse('<!doctype html><title>isolated log cache regression</title>')

    @app.get('/fixture/health')
    async def health():return {'ready':True}

    @app.get('/fixture/stats')
    async def stats():return dict(counts)

    @app.post('/fixture/session/{role}')
    async def session(role:str):
        response=JSONResponse({'fixture':True},headers={'Cache-Control':'no-store'})
        if role in ('admin','operator','viewer'):response.set_cookie('fixture-role',role,httponly=True)
        else:response.delete_cookie('fixture-role')
        return response

    @app.get('/fixture/cacheable')
    async def cacheable(request:Request):
        if request.cookies.get('fixture-role')!='admin':raise HTTPException(403,detail='fixture denied')
        # 易缓存对照组，证明浏览器缓存有效；故意不应用日志响应策略。
        return {'audit':'isolated-cache-control-fixture'}

    @app.post('/fixture/shutdown')
    async def shutdown():
        server.should_exit=True
        return {'stopping':True}

    listener=socket.socket()
    listener.bind(('127.0.0.1',0))
    listener.listen(128)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False,lifespan='off'))
    print(json.dumps({'fixture_port':listener.getsockname()[1]}),flush=True)
    try:await server.serve(sockets=[listener])
    finally:
        listener.close()
        await fixture.asyncTearDown()
        fixture.doCleanups()


if __name__=='__main__':asyncio.run(main())
