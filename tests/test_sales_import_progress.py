"""Streaming import uses isolated SQLite, never the configured business database."""
import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from sqlalchemy import select

from models import InventoryBatch, Sale, SalesImportBatch, SettlementEntry
from services.import_progress import ImportProgress, _running_imports
from tests import test_sales_import_store as fixtures
from tests.test_sales_name_import import source_file


class ProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_viewer_coalesces_and_does_not_block_writer(self):
        progress = ImportProgress()
        for i in range(200):
            await progress.update('writing', i, 200)
        self.assertEqual(progress.queue.qsize(), 1)
        self.assertEqual((await progress.queue.get())['processed'], 199)

    async def test_disconnected_view_does_not_cancel_accepted_import(self):
        progress = ImportProgress()
        finish, completed = asyncio.Event(), asyncio.Event()

        async def run(p):
            await p.update('writing', 1, 2)
            await finish.wait()
            p.publish({'type':'result', 'status':200, 'data':{}})
            completed.set()

        stream = progress.stream(run)
        self.assertEqual(json.loads(await anext(stream))['type'], 'progress')
        await stream.aclose()
        finish.set()
        await asyncio.wait_for(completed.wait(), 1)
        await asyncio.sleep(0)
        self.assertEqual(len(_running_imports), 0)


class StreamingImportTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.ImportStoreTests.setUp
    tearDown = fixtures.ImportStoreTests.tearDown
    request = fixtures.ImportStoreTests.request

    async def upload(self, rows=None, store='7', content=None):
        closed = []

        @asynccontextmanager
        async def session():
            try:
                yield self.db
            except BaseException:
                await self.db.rollback()
                raise
            finally:
                closed.append(True)

        with patch('routers.sales.async_session', session):
            response = await self.request('POST', '/sales/import-stream', data={'store_id':store},
                files={'file':('orders.csv', content if content is not None else source_file(rows or [{'name':'商品'}]), 'text/csv')})
        self.assertEqual(closed, [True])
        self.assertEqual(response.headers['content-type'], 'application/x-ndjson')
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[-1]['type'], 'result')
        return events

    async def test_result_only_after_commit_and_new_session_closes(self):
        committed = False
        commit = self.db.commit

        async def checked_commit():
            nonlocal committed
            await commit()
            committed = True

        publish = ImportProgress.publish

        def checked_publish(progress, event):
            if event['type'] == 'result' and event['status'] == 200:
                self.assertTrue(committed)
            publish(progress, event)

        with patch.object(self.db, 'commit', checked_commit), patch.object(ImportProgress, 'publish', checked_publish):
            events = await self.upload()
        self.assertEqual(events[-1]['status'], 200)
        self.assertEqual(events[-1]['data']['sales_created'], 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 8)
        for event in events[:-1]:
            if event.get('total'):
                self.assertLessEqual(event['processed'], event['total'])
        retry = (await self.upload())[-1]
        self.assertEqual(retry['data']['skipped_duplicates'], 1)
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 8)

    async def test_row_errors_and_bad_store_do_not_write(self):
        for events in (await self.upload(content=b'bad'), await self.upload(store='9')):
            self.assertEqual(events[-1]['status'], 400)
        self.assertEqual(self.session.scalars(select(SalesImportBatch)).all(), [])
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)

    async def test_operator_store_boundary_and_viewer_forbidden(self):
        self.actor.role, self.actor.store_id = 'operator', 7
        self.assertEqual((await self.upload(store='8'))[-1]['status'], 403)
        self.actor.role = 'viewer'
        with patch('routers.sales.async_session') as factory:
            response = await self.request('POST', '/sales/import-stream', data={'store_id':'7'},
                files={'file':('orders.csv', source_file([{}]), 'text/csv')})
        self.assertEqual(response.status_code, 403)
        factory.assert_not_called()

    async def test_commit_failure_rolls_back_stock_sales_and_sources(self):
        with patch.object(self.db, 'commit', side_effect=RuntimeError('private failure')):
            events = await self.upload()
        self.assertEqual(events[-1]['status'], 500)
        self.assertNotIn('private failure', json.dumps(events))
        for model in (Sale, SalesImportBatch, SettlementEntry):
            self.assertEqual(self.session.scalars(select(model)).all(), [])
        self.assertEqual(self.session.get(InventoryBatch, 1).remaining_quantity, 10)

    async def test_file_dates_keep_september_goods_and_october_adjustment(self):
        rows = [{'name':'商品', 'order':'MONTH', 'ordered_on':'2026-09-30', 'settled_on':'2026-10-07'},
                {'type':'物流赔付', 'name':'物流赔付', 'order':'COMP', 'quantity':0, 'net':0, 'total':5,
                 'ordered_on':'2026-09-29', 'settled_on':'2026-10-07'}]
        self.assertEqual((await self.upload(rows))[-1]['status'], 200)
        september = (await self.request('GET', '/sales?month=2026-09')).json()
        october = (await self.request('GET', '/sales?month=2026-10')).json()
        self.assertEqual(len(september), 1)
        self.assertEqual(september[0]['record_type'], 'sale')
        self.assertEqual(len(october), 1)
        self.assertEqual(october[0]['record_type'], 'compensation_income')
