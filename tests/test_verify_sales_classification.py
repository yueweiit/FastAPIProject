"""专项核验工具回归；所有数据库会话及报表读取均替换，不连接业务库。"""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from migrations import verify_sales_classification as verifier


class ClassificationVerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.opening = [(1087, 108), (1127, 30), (1129, 34), (1131, 54),
                        (1118, 1), (1119, 1), (1120, 6), (1090, 215)]
        self.entries = [
            SimpleNamespace(id=21863, store_id=1, platform_sku_id="1736307185018307714",
                mapping_status="confirmed", currency="MXN", sale_id=None, product_id=None,
                net_product_sales=Decimal("34"), settlement_total=Decimal("15.44"),
                raw_data={"_settlement_only_reason": "non_inventory"}),
            SimpleNamespace(id=22309, store_id=1, platform_sku_id="1736253253365040258",
                mapping_status="confirmed", currency="MXN", sale_id=None, product_id=None,
                net_product_sales=Decimal("39"), settlement_total=Decimal("23.41"),
                raw_data={"_settlement_only_reason": "non_inventory"}),
        ]

    async def run_verification(self, *, opening=None, entries=None, rules=2, allocations=0,
                               query_error=None, replay_error=None, expected_error=None):
        sessions = [AsyncMock() for _ in range(3)]
        for db in sessions:
            db.__aenter__.return_value = db
        opening_result, entry_result = MagicMock(), MagicMock()
        opening_result.all.return_value = self.opening if opening is None else opening
        entry_result.all.return_value = self.entries if entries is None else entries
        sessions[0].execute.side_effect = [None, query_error or opening_result, entry_result]
        sessions[0].scalar.side_effect = [rules, allocations]
        values = {"inventory_impairment": {"current": None},
                  "inventory_writeoff": {"current": None}, "_warnings": ["归属待核对"]}
        output = io.StringIO()
        with patch.object(verifier, "async_session", side_effect=sessions) as factory, \
                patch.object(verifier, "engine", SimpleNamespace(dispose=AsyncMock())) as engine, \
                patch.object(verifier, "_store_provision_financial_values", new_callable=AsyncMock,
                             return_value=values, side_effect=replay_error), redirect_stdout(output):
            if expected_error:
                with self.assertRaises(expected_error) as caught:
                    await verifier.verify()
                if expected_error is SystemExit:
                    self.assertEqual(str(caught.exception), "CLASSIFICATION_VERIFICATION_FAILED")
            else:
                await verifier.verify()
            engine.dispose.assert_awaited_once()
            for db in sessions[:factory.call_count]:
                self.assertEqual(str(db.execute.await_args_list[0].args[0]), "START TRANSACTION READ ONLY")
                for call in db.execute.await_args_list[1:] + db.scalar.await_args_list:
                    self.assertTrue(call.args[0].is_select)
                db.rollback.assert_awaited_once()
                db.commit.assert_not_awaited()
                db.flush.assert_not_awaited()
                db.__aexit__.assert_awaited_once()
        return output.getvalue().splitlines()

    async def test_valid_manifest_keeps_unknown_financial_values_and_warnings(self):
        lines = await self.run_verification()
        summary = json.loads(lines[0])
        self.assertTrue(summary["ok"])
        self.assertEqual((summary["opening_rows"], summary["opening_units"]), (8, 449))
        self.assertEqual(summary["non_inventory_original_sales_mxn"], "73")
        self.assertEqual(summary["non_inventory_original_settlement_mxn"], "38.85")
        reports = [json.loads(line) for line in lines[1:3]]
        self.assertEqual([row["month"] for row in reports], ["2026-09-01", "2026-10-01"])
        self.assertTrue(all(row["inventory_impairment"]["current"] is None for row in reports))
        self.assertTrue(all(row["_warnings"] == ["归属待核对"] for row in reports))
        self.assertEqual(lines[-1], "CLASSIFICATION_VERIFIED_REPORTS_REQUIRE_REVIEW")

    async def test_wrong_opening_record_or_quantity_fails_even_when_totals_match(self):
        for opening in ([*self.opening[:-1], (9999, 215)],
                        [(1087, 107), (1127, 31), *self.opening[2:]], []):
            with self.subTest(opening=opening):
                lines = await self.run_verification(opening=opening, expected_error=SystemExit)
                self.assertFalse(json.loads(lines[0])["ok"])
                self.assertFalse(any(line.startswith("CLASSIFICATION_VERIFIED") for line in lines))

    async def test_invalid_settlement_and_missing_amounts_report_failure_without_crashing(self):
        for field, value in (("raw_data", None), ("raw_data", []), ("raw_data", {}),
                             ("net_product_sales", None), ("settlement_total", None),
                             ("store_id", 2), ("platform_sku_id", "OTHER"),
                             ("mapping_status", "pending_confirmation"), ("currency", "CNY"),
                             ("sale_id", 123), ("product_id", 1)):
            with self.subTest(field=field, value=value):
                entries = copy.deepcopy(self.entries)
                setattr(entries[0], field, value)
                lines = await self.run_verification(entries=entries, expected_error=SystemExit)
                summary = json.loads(lines[0])
                self.assertFalse(summary["ok"])
                if field in ("net_product_sales", "settlement_total"):
                    key = "non_inventory_original_sales_mxn" if field == "net_product_sales" else "non_inventory_original_settlement_mxn"
                    self.assertIsNone(summary[key])

    async def test_missing_records_rules_or_unexpected_allocations_do_not_pass(self):
        for kwargs in ({"entries": []}, {"entries": self.entries[:1]}, {"rules": 1}, {"allocations": 1}):
            with self.subTest(kwargs=kwargs):
                lines = await self.run_verification(**kwargs, expected_error=SystemExit)
                self.assertFalse(json.loads(lines[0])["ok"])

    async def test_query_failure_rolls_back_and_disposes_without_success(self):
        lines = await self.run_verification(query_error=RuntimeError("query failed"), expected_error=RuntimeError)
        self.assertEqual(lines, [])

    async def test_replay_failure_rolls_back_and_disposes_without_success(self):
        lines = await self.run_verification(replay_error=RuntimeError("replay failed"), expected_error=RuntimeError)
        self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
