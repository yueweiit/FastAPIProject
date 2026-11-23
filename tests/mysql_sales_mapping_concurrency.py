"""独立临时MySQL容器验证销售/归属锁序；不读取业务库连接，不启动main。

显式运行：python -m tests.mysql_sales_mapping_concurrency --run-isolated
使用已有mysql:8.0.46镜像、随机回环端口和tmpfs；结束只停止本脚本创建的容器。
"""
import argparse
import asyncio
import io
import os
import secrets
import subprocess
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from database import Base
from models import InventoryBatch, Product, Sale, SettlementEntry, Store, User
from routers.batches import update_batch_impairment_stores
from routers.sales import _import_sales_file
from schemas import BatchImpairmentUpdate, PendingRetryRequest
from services.fifo import fifo_sell
from services.sales_pending import retry_pending
from tests.test_sales_name_import import source_file


def docker(*args, env=None):
    result = subprocess.run(['docker', *args], check=True, text=True, capture_output=True, env=env)
    return result.stdout.strip()


def upload(code, order):
    raw = source_file([{'sku': '', '本地SKU': code, 'order': order, 'quantity': 1}], ('本地SKU',))
    return UploadFile(io.BytesIO(raw), filename='orders.csv')


def result_data(value):
    return value.model_dump() if hasattr(value, 'model_dump') else value


async def seed(sessions, number):
    store_id, product_id, batch_id = 100 + number, 200 + number, 300 + number
    async with sessions() as db:
        db.add(Store(id=store_id, name=f'Test {number}', platform_store_id=str(store_id),
                     country_or_region='CN', settlement_currency='CNY', is_active=True))
        db.add(Product(id=product_id, sku=f'SKU-{number}', name='测试商品', specification='标准'))
        await db.flush()
        batch = InventoryBatch(id=batch_id, product_id=product_id, batch_no=f'B-{number}', quantity=20,
                               remaining_quantity=20, purchase_price=10, unit_cost=10, arrived_at=datetime(2026, 1, 1))
        db.add(batch)
        await db.flush()
        await db.refresh(batch)
        version = batch.edit_version
        await db.commit()
    return store_id, product_id, batch_id, version


async def concurrent_case(engine, sessions, actor, number, *, pending=False, manual=False):
    store_id, product_id, batch_id, version = await seed(sessions, number)
    code, order = f'SKU-{number}', f'ORDER-{number}'
    entry_id = None
    if pending:
        async with sessions() as db:
            result = result_data(await _import_sales_file(upload('UNKNOWN', order), db, actor, store_id))
            assert result['pending_confirmation'] == 1, result
            entry_id = await db.scalar(select(SettlementEntry.id).where(SettlementEntry.store_id == store_id))

    store_held, contender_waiting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class PausedImportSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            lock = getattr(statement, '_for_update_arg', None)
            entities = [c.get('entity') for c in getattr(statement, 'column_descriptions', [])]
            if lock is not None and not lock.read and Store in entities and not store_held.is_set():
                store_held.set()
                await release.wait()
            return result

    class ContenderSession(AsyncSession):
        async def execute(self, statement, *args, **kwargs):
            lock = getattr(statement, '_for_update_arg', None)
            entities = [c.get('entity') for c in getattr(statement, 'column_descriptions', [])]
            if lock is not None and lock.read and Store in entities:
                contender_waiting.set()
            return await super().execute(statement, *args, **kwargs)

    async def import_task():
        async with PausedImportSession(engine, expire_on_commit=False) as db:
            if pending:
                return await retry_pending(db, actor, PendingRetryRequest(entry_ids=[entry_id],
                    components=[{'product_id': product_id, 'multiplier': 2}]))
            return await _import_sales_file(upload(code + '*2', order), db, actor, store_id)

    async def contender_task():
        async with ContenderSession(engine, expire_on_commit=False) as db:
            if manual:
                return await fifo_sell(db, product_id, f'MANUAL-{number}', 1, Decimal(20),
                                       sold_at=datetime(2026, 9, 1), user_id=actor.id, store_id=store_id)
            try:
                return await update_batch_impairment_stores(batch_id, BatchImpairmentUpdate(
                    edit_version=version, impairment_store_ids=[store_id]), db, actor)
            except HTTPException as exc:
                # 先完成的销售改变了批次版本；既有乐观版本保护应要求刷新。
                assert exc.status_code == 409 and exc.detail['code'] == 'batch_edit_conflict', exc.detail
                return 'refresh_required'

    first = asyncio.create_task(import_task())
    second = None
    try:
        await asyncio.wait_for(store_held.wait(), 10)
        second = asyncio.create_task(contender_task())
        await asyncio.wait_for(contender_waiting.wait(), 10)
        # 店铺锁正在被占用；编辑/单笔销售此时必须尚未锁库存，导入才能继续。
        await asyncio.sleep(0.1)
        assert not second.done(), 'Contender must wait for the store lock'
        release.set()
        result, contender_result = await asyncio.wait_for(asyncio.gather(first, second), 15)
        result = result_data(result)
        assert result['sales_created'] == 1, result
        if not manual:
            assert contender_result == 'refresh_required', contender_result
    finally:
        release.set()
        for task in (first, second):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)

    async with sessions() as db:
        remaining = await db.scalar(select(InventoryBatch.remaining_quantity).where(InventoryBatch.id == batch_id))
        quantities = (await db.execute(select(Sale.quantity).where(Sale.store_id == store_id))).scalars().all()
        assert (remaining, sum(quantities)) == ((17, 3) if manual else (18, 2)), (remaining, quantities)
        if not manual:
            batch = await db.get(InventoryBatch, batch_id)
            edited = await update_batch_impairment_stores(batch_id, BatchImpairmentUpdate(
                edit_version=batch.edit_version, impairment_store_ids=[store_id]), db, actor)
            assert edited.impairment_store_ids == [store_id]
    if not pending:
        async with sessions() as db:
            duplicate = result_data(await _import_sales_file(upload(code + ' + ' + code, order), db, actor, store_id))
            assert duplicate['skipped_duplicates'] == 1 and duplicate['sales_created'] == 0, duplicate
    print(f'PASS: {"pending retry" if pending else "import"} + {"manual sale" if manual else "batch assignment"}; no duplicate deduction')


async def check(port, password):
    url = URL.create('mysql+aiomysql', username='root', password=password, host='127.0.0.1', port=port,
                     database='inventory_fifo_mapping_validation')
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            version, storage, isolation = (await connection.execute(text(
                'SELECT VERSION(), @@default_storage_engine, @@transaction_isolation'))).one()
            assert storage == 'InnoDB' and isolation == 'REPEATABLE-READ'
            await connection.run_sync(Base.metadata.create_all)
        print(f'Isolated MySQL {version}; {storage}; {isolation}')
        async with sessions() as db:
            db.add(User(id=1, username='isolated-admin', password_hash='test', role='admin'))
            await db.commit()
        actor = SimpleNamespace(id=1, username='isolated-admin', role='admin', store_id=None)
        await concurrent_case(engine, sessions, actor, 1)
        await concurrent_case(engine, sessions, actor, 2, pending=True)
        await concurrent_case(engine, sessions, actor, 3, manual=True)
    finally:
        await engine.dispose()


async def main():
    password = secrets.token_hex(24)
    env = {**os.environ, 'MYSQL_ROOT_PASSWORD': password}
    name = 'fifo-mapping-regression-' + uuid4().hex[:12]
    container = None
    try:
        docker('image', 'inspect', 'mysql:8.0.46')  # 不自动拉镜像或访问业务容器。
        container = docker('run', '--detach', '--rm', '--name', name, '--label', 'fifo.test=sales-mapping',
            '--mount', 'type=tmpfs,destination=/var/lib/mysql', '-p', '127.0.0.1::3306',
            '-e', 'MYSQL_ROOT_PASSWORD', '-e', 'MYSQL_ROOT_HOST=%',
            '-e', 'MYSQL_DATABASE=inventory_fifo_mapping_validation', 'mysql:8.0.46',
            '--innodb-lock-wait-timeout=5', env=env)
        for _ in range(60):
            ready = subprocess.run(['docker', 'exec', '-e', 'MYSQL_PWD', container, 'mysql',
                '--protocol=tcp', '-h127.0.0.1', '-uroot', '-Nse', 'SELECT 1', 'inventory_fifo_mapping_validation'],
                capture_output=True, env={**os.environ, 'MYSQL_PWD': password})
            if ready.returncode == 0 and ready.stdout.strip() == b'1':
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError('Isolated MySQL did not become ready')
        port = int(docker('port', container, '3306/tcp').rsplit(':', 1)[1])
        with patch.dict(os.environ, {'FIFO_OPERATION_AUDIT_ENABLED': '0', 'FIFO_FINANCIAL_CONTROLS_ENABLED': '0'}):
            await check(port, password)
    finally:
        if container:
            assert docker('inspect', '--format', '{{.Name}}', container) == '/' + name
            docker('stop', '--time', '5', container)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-isolated', action='store_true', required=True)
    parser.parse_args()
    asyncio.run(main())
