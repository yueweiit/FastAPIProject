"""历史日线字段、币种、日期边界和月末报表接入；网络用固定样本隔离。"""
import os
import unittest
from datetime import date
from decimal import Decimal as D
from http.client import BadStatusLine, HTTPResponse, IncompleteRead, RemoteDisconnected
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services import exchange_rates as fx
from services.report_exchange_rates import monthly_report_rates, rate_warnings
from tests import test_sales_import_store as import_fixtures


def payload(*records, code="MXNCNY", keys=None):
    return {"status": "0", "data": [{"code": code, "tab": {"newMarketData": {
        "keys": keys or ["time", "open", "close"], "marketData": ";".join(records),
    }}}]}


def truncated_response(*args, **kwargs):
    # Use the real HTTP parser: a successful status with an incomplete body.
    wire = BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n{"status":"0","data":[')
    response = HTTPResponse(SimpleNamespace(makefile=lambda *args, **kwargs: wire))
    response.begin()
    return response


class BaiduExchangeRatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_body_marks_report_rate_missing_without_fallback(self):
        with patch.dict(os.environ, {"FIFO_FX_PROVIDER": "baidu"}), \
             patch.object(fx.urllib.request, "urlopen", side_effect=truncated_response), \
             patch.object(fx, "_fetch_rate_range", side_effect=AssertionError("no silent fallback")), \
             patch("services.report_exchange_rates.report_today", return_value=date(2026, 11, 19)):
            with self.assertRaises(fx.ExchangeRateError) as caught:
                await fx.get_cny_rates({("MXN", date(2026, 9, 30))})
            self.assertIsInstance(caught.exception.__cause__, IncompleteRead)
            rates = await monthly_report_rates(SimpleNamespace(info={}), {
                ("MXN", date(2026, 9, 1)), ("CNY", date(2026, 9, 1)),
            })
        self.assertIsNone(rates["MXN", date(2026, 9, 1)]["rate_to_cny"])
        self.assertEqual(rates["MXN", date(2026, 9, 1)]["status"], "missing")
        self.assertEqual(rates["CNY", date(2026, 9, 1)]["rate_to_cny"], D(1))
        self.assertEqual(len(rate_warnings(rates)), 1)
        self.assertIn("2026-09 MXN", rate_warnings(rates)[0])

    def test_connection_and_protocol_errors_are_normalized_on_open_and_read(self):
        for stage in ("open", "read"):
            for error in (ConnectionResetError("reset"), RemoteDisconnected("closed"),
                          BadStatusLine("invalid status"), TimeoutError("timed out")):
                with self.subTest(stage=stage, error=type(error).__name__):
                    response = MagicMock()
                    response.__enter__.return_value = response
                    response.read.side_effect = error
                    with patch.object(fx.urllib.request, "urlopen", return_value=response,
                                      side_effect=error if stage == "open" else None):
                        with self.assertRaises(fx.ExchangeRateError) as caught:
                            fx._fetch_baidu_rates("MXN")
                    self.assertIs(caught.exception.__cause__, error)
                    self.assertEqual(str(caught.exception), "无法获取 MXN 百度历史收盘汇率")

    async def test_close_rate_and_weekend_month_end_flow_into_report_metadata(self):
        sample = payload("2026-09-30,0.3715,0.3711", "2026-10-30,0.3531,0.3542", "2026-11-02,0.9,0.8")
        with patch.dict(os.environ, {"FIFO_FX_PROVIDER": "baidu"}), \
             patch.object(fx, "_fetch_baidu_rates", return_value=sample) as fetch, \
             patch.object(fx, "_fetch_rate_range", side_effect=AssertionError("no fallback")), \
             patch("services.report_exchange_rates.report_today", return_value=date(2026, 11, 19)):
            rates = await monthly_report_rates(SimpleNamespace(info={}), {
                ("MXN", date(2026, 9, 1)), ("MXN", date(2026, 10, 1)), ("CNY", date(2026, 9, 1)),
            })
        fetch.assert_called_once_with("MXN")
        september = rates["MXN", date(2026, 9, 1)]
        self.assertEqual(september["rate_to_cny"], D(".3711"))
        self.assertEqual(september["source"], fx.BAIDU_FX_SOURCE)
        self.assertEqual(september["requested_date"], date(2026, 9, 30))
        october = rates["MXN", date(2026, 10, 1)]
        self.assertEqual(october["rate_to_cny"], D(".3542"))
        self.assertEqual(october["rate_date"], date(2026, 10, 30))
        self.assertEqual(rates["CNY", date(2026, 9, 1)]["rate_to_cny"], 1)

    def test_named_columns_and_exact_pair_prevent_open_or_inverse_conversion(self):
        sample = payload("0.9,2026-09-30,0.3711", keys=["open", "time", "close"])
        self.assertEqual(fx._resolve_baidu_rates("MXN", {date(2026, 9, 30)}, sample)["MXN", date(2026, 9, 30)]["rate"], D(".3711"))
        for sample in (payload("2026-09-30,1,2", code="CNYMXN"), {"status": "1", "data": []},
                       None, [], payload("2026-09-30,1", keys=["time", "open"])):
            with self.subTest(sample=sample), self.assertRaises(fx.ExchangeRateError):
                fx._resolve_baidu_rates("MXN", {date(2026, 9, 30)}, sample)

    def test_missing_invalid_conflicting_or_stale_prices_cannot_be_used(self):
        cases = [payload(f"2026-09-30,1,{value}") for value in ("NaN", "Infinity", "-1", "0", "--")]
        cases += [payload("2026-10-01,1,.4"), payload("2026-09-01,1,.4"),
                  payload("2026-09-30,1,.4", "2026-09-30,1,.5")]
        for sample in cases:
            with self.subTest(sample=sample), self.assertRaises(fx.ExchangeRateError):
                fx._resolve_baidu_rates("MXN", {date(2026, 9, 30)}, sample)
        with self.assertRaises(fx.ExchangeRateError):
            fx._resolve_baidu_rates("MXN", {date(2026, 9, 30), date(2026, 10, 31)}, payload("2026-09-30,1,.4"))

    async def test_failure_does_not_switch_provider_and_masks_affected_report_rate(self):
        with patch.dict(os.environ, {"FIFO_FX_PROVIDER": "baidu"}), \
             patch.object(fx, "_fetch_baidu_rates", side_effect=fx.ExchangeRateError("unavailable")), \
             patch.object(fx, "_fetch_rate_range", side_effect=AssertionError("no silent fallback")), \
             patch("services.report_exchange_rates.report_today", return_value=date(2026, 11, 19)):
            with self.assertRaises(fx.ExchangeRateError):
                await fx.get_cny_rates({("MXN", date(2026, 9, 30))})
            rates = await monthly_report_rates(SimpleNamespace(info={}), {("MXN", date(2026, 9, 1))})
            self.assertIsNone(rates["MXN", date(2026, 9, 1)]["rate_to_cny"])

    async def test_frankfurter_default_cny_and_invalid_configuration(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(fx, "_fetch_rate_range", return_value={"rates": {"2026-09-30": {"CNY": ".36989"}}}), \
             patch.object(fx, "_fetch_baidu_rates", side_effect=AssertionError("default unchanged")):
            rates = await fx.get_cny_rates({("mxn", date(2026, 9, 30)), ("CNY", date(2026, 9, 30))})
            self.assertEqual(rates["MXN", date(2026, 9, 30)]["source"], fx.FX_SOURCE)
            self.assertEqual(rates["CNY", date(2026, 9, 30)]["rate"], 1)
        with patch.dict(os.environ, {"FIFO_FX_PROVIDER": "invalid"}), self.assertRaises(fx.ExchangeRateError):
            await fx.get_cny_rates({("MXN", date(2026, 9, 30))})


class BaiduImportFailureTests(unittest.IsolatedAsyncioTestCase):
    setUp = import_fixtures.ImportStoreTests.setUp
    tearDown = import_fixtures.ImportStoreTests.tearDown
    request = import_fixtures.ImportStoreTests.request

    async def test_truncated_fx_response_rejects_import_without_inventory_or_sales_writes(self):
        from sqlalchemy import select
        from models import InventoryBatch, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation

        source = import_fixtures.order_file().decode("utf-8-sig").replace(",CNY,", ",MXN,").encode("utf-8-sig")
        before = {batch.id: batch.remaining_quantity for batch in self.session.scalars(select(InventoryBatch))}
        with patch.dict(os.environ, {"FIFO_FX_PROVIDER": "baidu"}), \
             patch.object(fx.urllib.request, "urlopen", side_effect=truncated_response), \
             patch.object(fx, "_fetch_rate_range", side_effect=AssertionError("no silent fallback")):
            response = await self.request("POST", "/sales/import", data={"store_id": "7"},
                                          files={"file": ("orders.csv", source, "text/csv")})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], "无法获取 MXN 百度历史收盘汇率")
        for model in (Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation):
            self.assertEqual(self.session.scalars(select(model)).all(), [], model.__name__)
        self.assertEqual({batch.id: batch.remaining_quantity for batch in self.session.scalars(select(InventoryBatch))}, before)


if __name__ == "__main__":
    unittest.main()
