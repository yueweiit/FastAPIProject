"""非实物结算与期初扣减：隔离SQLite/ASGI，不启动应用生命周期或连接业务库。"""
import os
import unittest
from datetime import date, datetime
from decimal import Decimal as D
from unittest.mock import patch

from sqlalchemy import delete, select, text
from fastapi import HTTPException

from models import InventoryBatch, NonInventorySkuRule, Product, Sale, SaleCostDetail, SettlementEntry
from routers.reports import _load_inventory_impairment_report_rows, _month_periods, _store_provision_financial_values
from services.financial_facts import unlinked_settlement_events
from services.inventory_provision_data import load_inventory_provision_data
from services.inventory_provision_movements import ProvisionSale, replay_inventory_provisions
from services.inventory_impairment import InventoryLayer
from services.report_exchange_rates import fulfillment_orders
from tests import test_sales_import_store as fixtures, test_sales_name_import as names, test_inventory_workspace as inventory


class NonInventoryTests(unittest.IsolatedAsyncioTestCase):
    request = fixtures.ImportStoreTests.request
    upload = names.NameImportTests.upload
    tearDown = fixtures.ImportStoreTests.tearDown

    def setUp(self):
        flag = patch.dict(os.environ, {"FIFO_OPERATION_AUDIT_ENABLED": "0", "FIFO_FINANCIAL_CONTROLS_ENABLED": "0"})
        flag.start(); self.addCleanup(flag.stop)
        fixtures.ImportStoreTests.setUp(self)

    def rule(self, sku="NON-STOCK", store=7, active=True):
        self.session.add(NonInventorySkuRule(store_id=store, platform_sku_id=sku,
            is_active=active, reason="经业务确认的非实物", created_by=1))
        self.session.commit()

    def stock(self):
        return [(b.id,b.quantity,b.remaining_quantity,b.unit_cost) for b in self.session.scalars(select(InventoryBatch))]

    async def test_missing_rule_table_fails_closed_before_stock_or_settlement_writes(self):
        from services.sales_classification import non_inventory_skus
        self.session.execute(text('DROP TABLE non_inventory_sku_rules'))
        before=self.stock()
        with self.assertRaises(HTTPException) as caught:
            await non_inventory_skus(self.db,{7})
        self.assertEqual(caught.exception.status_code,503)
        self.assertEqual(caught.exception.detail['code'],'sales_classification_schema_missing')
        self.assertEqual(self.stock(),before)
        self.assertEqual(self.session.scalars(select(SettlementEntry)).all(),[])

    def test_non_inventory_classifier_rejects_non_finite_source_amounts(self):
        from routers.sales import _order_settlement_only_reason
        for net,total in ((D('NaN'),D(1)),(D(1),D('Infinity'))):
            with self.assertRaises(ValueError):
                _order_settlement_only_reason({},net,total,non_inventory=True)

    async def test_referenced_store_cannot_be_deleted(self):
        from routers.finance_masters import delete_store
        self.rule(store=8)
        with self.assertRaises(HTTPException) as caught:
            await delete_store(8,self.db,self.actor)
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(caught.exception.detail['code'],'store_has_non_inventory_rules')

    async def test_positive_revenue_is_confirmed_without_product_stock_or_fulfillment(self):
        self.rule()
        before = self.stock()
        rows = [{"sku":"NON-STOCK", "name":"礼品卡非实物", "quantity":1, "net":"34", "total":"15.44"}]
        result = await self.upload(rows)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual((result.json()["sales_created"],result.json()["pending_confirmation"]),(0,0))
        entry = self.session.scalar(select(SettlementEntry))
        self.assertEqual(entry.raw_data["_settlement_only_reason"],"non_inventory")
        self.assertEqual(entry.mapping_status,"confirmed")
        self.assertIsNone(entry.sale_id); self.assertIsNone(entry.product_id)
        self.assertEqual(self.stock(),before)
        self.assertEqual(len(self.session.scalars(select(Product)).all()),1)
        self.assertEqual(self.session.scalars(select(Sale)).all(),[])
        events = await unlinked_settlement_events(self.db,{7},date(2026,9,1),date(2026,10,1))
        self.assertEqual([(e.original_revenue,e.original_expense) for e in events],[(D(34),D("18.56"))])
        again = await self.upload(rows)
        self.assertEqual(again.json()["skipped_duplicates"],1)
        self.assertEqual(self.stock(),before)

    async def test_rule_is_store_sku_scoped_and_inactive_rule_does_not_bypass_mapping(self):
        self.rule(store=8)
        self.rule(sku="INACTIVE",active=False)
        response = await self.upload([{"sku":"NON-STOCK","name":"礼品卡非实物"},
            {"sku":"OTHER","name":"礼品卡非实物"},{"sku":"INACTIVE","name":"礼品卡非实物"}])
        self.assertEqual(response.json()["pending_confirmation"],3)
        self.assertEqual(self.session.scalars(select(Sale)).all(),[])

    async def test_retry_reuses_exact_source_amounts_and_is_idempotent(self):
        response=await self.upload([{"sku":"NON-STOCK","name":"礼品卡非实物","net":"39","total":"23.41","quantity":1}])
        self.assertEqual(response.json()["pending_confirmation"],1)
        entry=self.session.scalar(select(SettlementEntry))
        original=(entry.id,entry.source_key,entry.net_product_sales,entry.settlement_total,entry.exchange_rate_to_cny)
        before=self.stock(); self.rule()
        retry=await self.request("POST","/sales/pending-confirmations/retry",json={"entry_ids":[entry.id]})
        self.assertEqual(retry.status_code,200,retry.text)
        self.assertEqual((retry.json()["confirmed_rows"],retry.json()["sales_created"]),(1,0))
        self.assertEqual((entry.id,entry.source_key,entry.net_product_sales,entry.settlement_total,entry.exchange_rate_to_cny),original)
        self.assertIn("_pending_resolution_history",entry.raw_data)
        self.assertEqual(self.stock(),before)
        again=await self.request("POST","/sales/pending-confirmations/retry",json={"entry_ids":[entry.id]})
        self.assertEqual(again.json()["skipped_rows"],1)
        self.assertEqual(len(await unlinked_settlement_events(self.db,{7},None,None)),1)


class OpeningDeductionTests(unittest.IsolatedAsyncioTestCase):
    record_query = inventory.InventoryWorkspaceTests.record_query
    add_sale = inventory.InventoryWorkspaceTests.add_sale
    asyncTearDown = inventory.InventoryWorkspaceTests.asyncTearDown

    def setUp(self):
        flag=patch.dict(os.environ,{"FIFO_OPERATION_AUDIT_ENABLED":"0"})
        flag.start();self.addCleanup(flag.stop)
        inventory.InventoryWorkspaceTests.setUp(self)
        self.session.execute(delete(SaleCostDetail));self.session.execute(delete(Sale))
        self.session.execute(delete(InventoryBatch).where(InventoryBatch.id!=3))
        self.session.get(Product,2).product_type="new"
        batch=self.session.get(InventoryBatch,3)
        batch.remaining_quantity=4
        self.add_sale(30,3,4,datetime(2026,8,1),None,user_id=7)
        self.session.get(Sale,30).business_metadata={"kind":"opening_deduction","reason":"confirmed"}
        self.add_sale(31,3,2,datetime(2026,9,2),7)
        self.session.commit()

    async def test_quantity_and_provision_replays_agree_without_modifying_costs(self):
        before=[(b.id,b.quantity,b.remaining_quantity,b.unit_cost,b.arrived_at) for b in self.session.scalars(select(InventoryBatch))]
        db=inventory.InventoryReadSession(self.session)
        _batches,_sales,replay=await load_inventory_provision_data(db,date(2026,9,30),product_ids={2})
        self.assertEqual(replay.balances[date(2026,9,30)][3].quantity,4)
        self.assertIsNotNone(replay.balances[date(2026,9,30)][3].impairment_units)
        self.assertEqual({x.sale.sale_id for x in replay.writeoffs},{31})
        daily=await self.client.get("/inventory/daily",params={"month":"2026-08-01","product_id":2})
        self.assertEqual(daily.status_code,200,daily.text)
        item=daily.json()["items"][0]
        self.assertNotIn("inventory_history_inconsistent",item["warnings"])
        arrival=next(x for x in item["days"] if x["date"]=="2026-08-10")
        self.assertEqual((arrival["inbound"],arrival["outbound"],arrival["adjustment"],arrival["closing_quantity"]),(10,0,-4,6))
        provision=await _store_provision_financial_values(db,7,_month_periods(date(2026,9,1)))
        self.assertIsNotNone(provision["inventory_impairment"]["current"])
        self.assertIsNotNone(provision["inventory_writeoff"]["ytd"])
        rows=await _load_inventory_impairment_report_rows(db,date(2026,9,30))
        self.assertEqual((rows[0]["quantity"],rows[0]["sold_quantity"]),(4,2))
        self.assertIn("期初扣减4件",rows[0]["comparison_note"])
        self.assertEqual([(b.id,b.quantity,b.remaining_quantity,b.unit_cost,b.arrived_at) for b in self.session.scalars(select(InventoryBatch))],before)

    async def test_unclassified_real_sale_before_arrival_stays_unknown(self):
        self.session.get(Sale,30).business_metadata=None
        self.session.commit()
        _b,_s,replay=await load_inventory_provision_data(inventory.InventoryReadSession(self.session),date(2026,9,30))
        self.assertIsNone(replay.balances[date(2026,9,30)][3].impairment_units)

    async def test_opening_does_not_select_future_batch_or_count_fulfillment(self):
        db=inventory.InventoryReadSession(self.session)
        batches,_sales,_replay=await load_inventory_provision_data(db,date(2026,8,1))
        self.assertEqual(batches,[])
        sales=self.session.scalars(select(Sale).order_by(Sale.id)).all()
        orders=fulfillment_orders(sales,{})
        self.assertEqual(sum(map(len,orders.values())),1)
        self.assertEqual(set(orders),{date(2026,9,1)})

    async def test_sales_ledger_summary_and_breakdown_exclude_opening_but_keep_original_record(self):
        from routers.sales import router
        self.app.include_router(router)
        ledger=await self.client.get("/sales")
        self.assertEqual(ledger.status_code,200,ledger.text)
        self.assertEqual([row["id"] for row in ledger.json()],[31])
        summary=await self.client.get("/sales/monthly-summary")
        self.assertEqual(summary.status_code,200,summary.text)
        self.assertEqual(sum(row["sold_quantity"] for row in summary.json()),2)
        self.assertEqual((await self.client.get("/sales/30/cost-breakdown")).status_code,404)
        archive=await self.client.get("/sales/30")
        self.assertEqual(archive.status_code,200,archive.text)
        self.assertEqual(archive.json()["business_kind"],"opening_deduction")
        self.assertEqual((self.session.get(Sale,30).quantity,self.session.get(Sale,30).total_cost),(4,D(12)))

    def test_invalid_opening_quantity_is_rejected_instead_of_known_zero(self):
        layer=InventoryLayer(1,1,7,date(2026,8,10),10,"new",0)
        sale=ProvisionSale(1,1,1,datetime(2026,8,1),11,None,True)
        with self.assertRaisesRegex(ValueError,"opening_deduction_quantity_invalid"):
            replay_inventory_provisions([layer],[sale],date(2026,9,30))
