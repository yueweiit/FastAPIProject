"""Frankfurter历史汇率、旧配置兼容和月末报表；隔离网络与业务数据库。"""
import json
import os
import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from http.client import BadStatusLine, HTTPResponse, IncompleteRead, RemoteDisconnected
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services import exchange_rates as fx
from services.report_exchange_rates import monthly_report_rates, rate_warnings
from tests import test_sales_import_store as import_fixtures


MALFORMED_PAYLOADS = (
    None, [], "invalid", 1, {}, {"rates": None}, {"rates": []},
    {"rates": [{"CNY": ".36989"}]}, {"rates": "invalid"}, {"rates": 1},
)


def truncated_response(*args, **kwargs):
    # Use the real HTTP parser: a successful status with an incomplete body.
    wire = BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 200\r\n\r\n{"rates":{')
    response = HTTPResponse(SimpleNamespace(makefile=lambda *args, **kwargs: wire))
    response.begin()
    return response


class ExchangeRatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_body_marks_report_rate_missing_without_fallback(self):
        with patch.object(fx.urllib.request, "urlopen", side_effect=truncated_response), \
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
                            fx._fetch_rate_range("MXN", date(2026, 9, 16), date(2026, 9, 30))
                    self.assertIs(caught.exception.__cause__, error)
                    self.assertTrue(str(caught.exception).startswith("无法获取 MXN 历史汇率:"))

    async def test_weekend_month_end_flow_into_report_metadata(self):
        sample = {"rates": {"2026-09-30": {"CNY": ".36989"},
                            "2026-10-30": {"CNY": ".3542"}, "2026-11-02": {"CNY": ".8"}}}
        with patch.object(fx, "_fetch_rate_range", return_value=sample) as fetch, \
             patch("services.report_exchange_rates.report_today", return_value=date(2026, 11, 19)):
            rates = await monthly_report_rates(SimpleNamespace(info={}), {
                ("MXN", date(2026, 9, 1)), ("MXN", date(2026, 10, 1)), ("CNY", date(2026, 9, 1)),
            })
        fetch.assert_called_once_with("MXN", date(2026, 9, 16), date(2026, 10, 31))
        september = rates["MXN", date(2026, 9, 1)]
        self.assertEqual(september["rate_to_cny"], D(".36989"))
        self.assertEqual(september["source"], "Frankfurter/ECB")
        self.assertEqual(september["requested_date"], date(2026, 9, 30))
        october = rates["MXN", date(2026, 10, 1)]
        self.assertEqual(october["rate_to_cny"], D(".3542"))
        self.assertEqual(october["rate_date"], date(2026, 10, 30))
        self.assertEqual(rates["CNY", date(2026, 9, 1)]["rate_to_cny"], 1)

    async def test_request_uses_frankfurter_and_correct_currency_direction(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"rates":{"2026-09-30":{"CNY":0.36989}}}'
        with patch.object(fx.urllib.request, "urlopen", return_value=response) as opened:
            rates = await fx.get_cny_rates({("MXN", date(2026, 9, 30))})
        opened.assert_called_once()
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://api.frankfurter.dev/v1/2026-09-16..2026-09-30?base=MXN&symbols=CNY")
        self.assertEqual(opened.call_args.kwargs["timeout"], 20)
        self.assertEqual(rates["MXN", date(2026, 9, 30)], {
            "rate": D(".36989"), "rate_date": date(2026, 9, 30), "source": "Frankfurter/ECB",
        })

    def test_missing_invalid_or_future_rates_cannot_be_used(self):
        cases = [{"rates": {"2026-09-30": {"CNY": value}}}
                 for value in ("NaN", "Infinity", "-1", "0", "--")]
        cases += [{"rates": {}}, {"rates": {"2026-10-01": {"CNY": ".4"}}}]
        for sample in cases:
            with self.subTest(sample=sample), self.assertRaises(fx.ExchangeRateError):
                fx._resolve_rate_payload("MXN", {date(2026, 9, 30)}, sample)

    def test_lookback_includes_day_14_and_rejects_day_15(self):
        target = date(2026, 9, 30)
        for age in (0, 1, 14, 15, 31):
            rate_date = target - timedelta(days=age)
            sample = {"rates": {rate_date.isoformat(): {"CNY": ".36989"}}}
            with self.subTest(age=age):
                if age > 14:
                    with self.assertRaises(fx.ExchangeRateError):
                        fx._resolve_rate_payload("MXN", {target}, sample)
                else:
                    result = fx._resolve_rate_payload("MXN", {target}, sample)
                    self.assertEqual(result["MXN", target]["rate_date"], rate_date)
                    self.assertEqual(result["MXN", target]["rate"], D(".36989"))

    async def test_cross_month_partial_response_does_not_use_stale_rate_in_reports(self):
        sample = {"rates": {"2026-09-30": {"CNY": ".36989"}}}
        with patch.object(fx, "_fetch_rate_range", return_value=sample), \
             patch("services.report_exchange_rates.report_today", return_value=date(2026, 11, 19)):
            with self.assertRaises(fx.ExchangeRateError):
                await fx.get_cny_rates({("MXN", date(2026, 9, 30)), ("MXN", date(2026, 10, 31))})
            rates = await monthly_report_rates(SimpleNamespace(info={}), {
                ("MXN", date(2026, 9, 1)), ("MXN", date(2026, 10, 1)), ("CNY", date(2026, 10, 1)),
            })
        october = rates["MXN", date(2026, 10, 1)]
        self.assertEqual(october["status"], "missing")
        self.assertIsNone(october["rate_to_cny"])
        self.assertIsNone(october["rate_date"])
        self.assertTrue(any("2026-10 MXN" in warning for warning in rate_warnings(rates)))
        self.assertEqual(rates["CNY", date(2026, 10, 1)]["rate_to_cny"], D(1))

    async def test_invalid_json_shapes_raise_rate_error_and_mark_report_missing(self):
        for sample in MALFORMED_PAYLOADS:
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = json.dumps(sample).encode("utf-8")
            with self.subTest(sample=sample), \
                 patch.object(fx.urllib.request, "urlopen", return_value=response), \
                 patch("services.report_exchange_rates.report_today", return_value=date(2026, 11, 19)):
                with self.assertRaises(fx.ExchangeRateError):
                    await fx.get_cny_rates({("MXN", date(2026, 9, 30))})
                rates = await monthly_report_rates(SimpleNamespace(info={}), {
                    ("MXN", date(2026, 9, 1)), ("CNY", date(2026, 9, 1)),
                })
                self.assertEqual(rates["MXN", date(2026, 9, 1)]["status"], "missing")
                self.assertIsNone(rates["MXN", date(2026, 9, 1)]["rate_to_cny"])
                self.assertEqual(rates["CNY", date(2026, 9, 1)]["rate_to_cny"], D(1))
                self.assertEqual(len(rate_warnings(rates)), 1)

    async def test_failure_masks_affected_report_rate(self):
        with patch.object(fx, "_fetch_rate_range", side_effect=fx.ExchangeRateError("unavailable")), \
             patch("services.report_exchange_rates.report_today", return_value=date(2026, 11, 19)):
            with self.assertRaises(fx.ExchangeRateError):
                await fx.get_cny_rates({("MXN", date(2026, 9, 30))})
            rates = await monthly_report_rates(SimpleNamespace(info={}), {("MXN", date(2026, 9, 1))})
            self.assertIsNone(rates["MXN", date(2026, 9, 1)]["rate_to_cny"])

    async def test_legacy_provider_setting_does_not_change_frankfurter(self):
        for provider in (None, "frankfurter", "baidu", "invalid"):
            with self.subTest(provider=provider), patch.dict(os.environ), \
                 patch.object(fx, "_fetch_rate_range", return_value={"rates": {"2026-09-30": {"CNY": ".36989"}}}) as fetch:
                os.environ.pop("FIFO_FX_PROVIDER", None)
                if provider is not None:
                    os.environ["FIFO_FX_PROVIDER"] = provider
                rates = await fx.get_cny_rates({(" mxn ", date(2026, 9, 30)), ("CNY", date(2026, 9, 30))})
                fetch.assert_called_once_with("MXN", date(2026, 9, 16), date(2026, 9, 30))
                self.assertEqual(rates["MXN", date(2026, 9, 30)]["source"], "Frankfurter/ECB")
                self.assertEqual(rates["CNY", date(2026, 9, 30)]["rate"], 1)

    async def test_cny_and_empty_requirements_do_not_request_rates(self):
        with patch.object(fx.urllib.request, "urlopen", side_effect=AssertionError("no network")):
            self.assertEqual(await fx.get_cny_rates(set()), {})
            self.assertEqual(await fx.get_cny_rates({("CNY", date(2026, 9, 30))}), {
                ("CNY", date(2026, 9, 30)): {"rate": D(1), "rate_date": date(2026, 9, 30), "source": "CNY"},
            })


class ExchangeRateImportTests(unittest.IsolatedAsyncioTestCase):
    setUp = import_fixtures.ImportStoreTests.setUp
    tearDown = import_fixtures.ImportStoreTests.tearDown
    request = import_fixtures.ImportStoreTests.request

    async def assert_import_rejected_without_writes(self, source):
        from sqlalchemy import select
        from models import InventoryBatch, Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation

        before = {batch.id: batch.remaining_quantity for batch in self.session.scalars(select(InventoryBatch))}
        response = await self.request("POST", "/sales/import", data={"store_id": "7"},
                                      files={"file": ("orders.csv", source, "text/csv")})
        self.assertEqual(response.status_code, 502, response.text)
        for model in (Sale, SaleCostDetail, SalesImportBatch, SettlementEntry, SettlementEntryAllocation):
            self.assertEqual(self.session.scalars(select(model)).all(), [], model.__name__)
        self.assertEqual({batch.id: batch.remaining_quantity for batch in self.session.scalars(select(InventoryBatch))}, before)
        return response

    async def test_truncated_fx_response_rejects_import_without_inventory_or_sales_writes(self):
        source = import_fixtures.order_file().decode("utf-8-sig").replace(",CNY,", ",MXN,").encode("utf-8-sig")
        with patch.object(fx.urllib.request, "urlopen", side_effect=truncated_response):
            response = await self.assert_import_rejected_without_writes(source)
        self.assertTrue(response.json()["detail"].startswith("无法获取 MXN 历史汇率:"))

    async def test_malformed_fx_responses_reject_import_without_writes(self):
        source = import_fixtures.order_file().decode("utf-8-sig").replace(",CNY,", ",MXN,").encode("utf-8-sig")
        for sample in MALFORMED_PAYLOADS:
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = json.dumps(sample).encode("utf-8")
            with self.subTest(sample=sample), patch.object(fx.urllib.request, "urlopen", return_value=response):
                result = await self.assert_import_rejected_without_writes(source)
                self.assertIn("响应结构无效", result.json()["detail"])

    async def test_stale_cross_month_rates_reject_entire_import_without_writes(self):
        source = (import_fixtures.order_file().decode("utf-8-sig")
                  .replace(",CNY,", ",MXN,").replace("2026-09-02", "2026-11-02")
                  .replace("2026-09-01", "2026-09-30").replace("2026-09-30", "2026-10-31", 1)
                  .encode("utf-8-sig"))
        with patch.object(fx, "_fetch_rate_range", return_value={"rates": {"2026-09-30": {"CNY": ".36989"}}}) as fetch:
            response = await self.assert_import_rejected_without_writes(source)
        fetch.assert_called_once_with("MXN", date(2026, 9, 16), date(2026, 10, 31))
        self.assertIn("2026-10-31", response.json()["detail"])
        self.assertIn("14 天", response.json()["detail"])

    async def test_new_import_saves_frankfurter_rate_despite_legacy_provider_setting(self):
        from sqlalchemy import select
        from models import SettlementEntry

        source = import_fixtures.order_file().decode("utf-8-sig").replace(",CNY,", ",MXN,").encode("utf-8-sig")
        with patch.dict(os.environ, {"FIFO_FX_PROVIDER": "baidu"}), \
             patch.object(fx, "_fetch_rate_range", return_value={"rates": {"2026-09-01": {"CNY": ".36989"}}}) as fetch:
            response = await self.request("POST", "/sales/import", data={"store_id": "7"},
                                          files={"file": ("orders.csv", source, "text/csv")})
        self.assertEqual(response.status_code, 200, response.text)
        fetch.assert_called_once()
        entries = self.session.scalars(select(SettlementEntry)).all()
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry.exchange_rate_source, "Frankfurter/ECB")
            self.assertEqual(entry.exchange_rate_to_cny, D(".36989"))
            self.assertEqual(entry.exchange_rate_date, date(2026, 9, 1))


if __name__ == "__main__":
    unittest.main()
