"""店铺内部损益跌价净列报：计算、导出及不可变旧版本；不连接业务库。"""
import copy
import unittest
from datetime import date
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openpyxl import Workbook, load_workbook

from routers.reports import (
    STORE_PROFIT_LOSS_POLICY, _build_store_profit_loss_values,
    _build_store_profit_loss_sheet, _build_store_profit_loss_summary_sheet,
    _store_profit_loss_response,
)
from services.financial_versions import version_workbook


PERIODS = ("current", "previous", "ytd")
MONTH = date(2026, 10, 1)
STORE = SimpleNamespace(id=7, name="Lemos", platform="tiktok_shop")


def cells(value):
    return {period: None if value is None else D(value) for period in PERIODS}


def example():
    # 截图已知毛利10637.25、转销1840.58，原始成本合计18340.08。
    return {
        "product_sales_revenue": cells("27136.75"),
        "product_purchase_cost": cells("3926.46"),
        "head_logistics_cost": cells("2294.64"),
        "fulfillment_cost": cells("1955.32"),
        "other_direct_cost": cells("10163.66"),
        "inventory_impairment": cells("22321.73"),
        "inventory_writeoff": cells("1840.58"),
    }


def reload_book(book):
    stream = BytesIO()
    book.save(stream)
    stream.seek(0)
    return load_workbook(stream, data_only=True)


def row_by_label(sheet, label):
    return next(row for row in sheet if row[0].value == label)


class StoreImpairmentPresentationTests(unittest.IsolatedAsyncioTestCase):
    def test_confirmed_example_preserves_operating_profit_in_all_periods(self):
        auto = example()
        original = copy.deepcopy(auto)
        values = _build_store_profit_loss_values(auto, {})
        for period in PERIODS:
            with self.subTest(period=period):
                self.assertEqual(values["operating_cost"][period], D("18340.08"))
                self.assertEqual(values["gross_profit"][period], D("8796.67"))
                self.assertEqual(values["gross_profit"][period] + D("1840.58"), D("10637.25"))
                self.assertEqual(values["gross_margin"][period], D("8796.67") / D("27136.75"))
                self.assertEqual(values["inventory_impairment"][period], D("22321.73"))
                self.assertEqual(values["inventory_writeoff"][period], D("1840.58"))
                self.assertEqual(values["asset_impairment_loss"][period], D("20481.15"))
                self.assertEqual(values["operating_profit"][period], D("-11684.48"))
                self.assertEqual(values["net_profit"][period], D("-11684.48"))
        self.assertEqual(auto, original)

    def test_zero_negative_and_decimal_net_effect_with_manual_expenses(self):
        for accrual, writeoff in (("0", "0"), ("125", "35"), ("40", "50"), ("0", "35"),
                                  ("10.0001", "0.0002")):
            with self.subTest(accrual=accrual, writeoff=writeoff):
                values = _build_store_profit_loss_values({
                    "product_sales_revenue": cells("600"), "product_purchase_cost": cells("400"),
                    "inventory_impairment": cells(accrual), "inventory_writeoff": cells(writeoff),
                }, {"salary": cells("20"), "advertising": cells("3"), "tax_surcharge": cells("2"),
                    "shared_admin": cells("4"), "finance_expenses": cells("5"),
                    "research_development": cells("6"), "credit_impairment_loss": cells("7")})
                for period in PERIODS:
                    self.assertEqual(values["asset_impairment_loss"][period], D(accrual) - D(writeoff))
                    self.assertEqual(values["operating_cost"][period], D(400))
                    # 旧列报：收入 - (成本 - 转销) - 各项费用 - 计提。
                    expected_profit = D(600) - (D(400) - D(writeoff)) - D(47) - D(accrual)
                    self.assertEqual(values["operating_profit"][period], expected_profit)
                    self.assertEqual(values["net_profit"][period], expected_profit)

    def test_unknown_provision_only_blanks_dependent_profit_cells(self):
        for unknown in ("inventory_impairment", "inventory_writeoff"):
            for period in PERIODS:
                with self.subTest(unknown=unknown, period=period):
                    auto = example()
                    auto[unknown][period] = None
                    auto["_warnings"] = ["归属待核对"]
                    values = _build_store_profit_loss_values(auto, {})
                    self.assertIsNone(values[unknown][period])
                    self.assertEqual(values["operating_cost"][period], D("18340.08"))
                    self.assertEqual(values["gross_profit"][period], D("8796.67"))
                    for key in ("asset_impairment_loss", "operating_profit", "net_profit"):
                        self.assertIsNone(values[key][period])
                    for other in set(PERIODS) - {period}:
                        self.assertEqual(values["net_profit"][other], D("-11684.48"))
                    self.assertEqual(values["_warnings"], ["归属待核对"])

    def test_periods_use_their_own_accrual_and_writeoff(self):
        values = _build_store_profit_loss_values({
            "inventory_impairment": dict(zip(PERIODS, map(D, ("125", "40", "165")))),
            "inventory_writeoff": dict(zip(PERIODS, map(D, ("35", "50", "85")))),
        }, {})
        self.assertEqual(values["asset_impairment_loss"], dict(zip(PERIODS, map(D, ("90", "-10", "80")))))
        self.assertEqual(values["net_profit"], dict(zip(PERIODS, map(D, ("-90", "10", "-80")))))

    def test_net_profit_respects_unknown_inputs_and_keeps_known_operating_profit(self):
        auto = example()
        auto["inventory_writeoff"]["ytd"] = None
        values = _build_store_profit_loss_values(auto, {
            "non_operating_income": cells("10"), "non_operating_expense": cells("3"),
            "income_tax_expense": cells("2"), "_unknown": {"current": ["income_tax_expense"]},
        })
        self.assertIsNone(values["income_tax_expense"]["current"])
        self.assertEqual(values["operating_profit"]["current"], D("-11684.48"))
        self.assertEqual(values["net_profit"], {"current": None, "previous": D("-11679.48"), "ytd": None})

    async def response(self, auto=None):
        with patch("routers.reports._store_profit_loss_auto_values", new=AsyncMock(return_value=auto or example())):
            return await _store_profit_loss_response(None, SimpleNamespace(role="admin"), STORE, MONTH, None)

    async def test_api_and_both_exports_keep_gross_accrual_writeoff_and_net_in_one_section(self):
        response = await self.response()
        self.assertEqual(response.calculation_policy, STORE_PROFIT_LOSS_POLICY)
        keys = [row.key for row in response.rows]
        start = keys.index("asset_impairment_loss")
        self.assertEqual(keys[start:start+4],
                         ["asset_impairment_loss", "inventory_impairment", "inventory_writeoff", "credit_impairment_loss"])
        self.assertGreater(start, keys.index("gross_profit"))
        self.assertEqual([row.label for row in response.rows[start:start+4]],
                         ["减：存货跌价净影响", "  本期存货跌价计提", "  本期销售转销", "信用减值损失"])
        values = _build_store_profit_loss_values(example(), {})
        book = Workbook()
        _build_store_profit_loss_sheet(book, STORE, MONTH, values, {})
        _build_store_profit_loss_summary_sheet(book, [(STORE, values), (STORE, values)], MONTH)
        book = reload_book(book)
        detail, summary = book["09-店铺损益表"], book["10-各店铺损益汇总表"]
        for number, row in enumerate(response.rows, 2):
            self.assertEqual(detail.cell(number, 1).value, row.label)
            self.assertEqual(summary.cell(number, 1).value, row.label)
            for column, period in enumerate(PERIODS, 2):
                value = getattr(row.value, period)
                if row.key == "gross_margin" and value is not None:
                    self.assertAlmostEqual(detail.cell(number, column).value, float(value), places=14)
                else:
                    self.assertEqual(detail.cell(number, column).value, None if value is None else float(value))
        for label, expected in (("减：存货跌价净影响", 40962.30), ("  本期销售转销", 3681.16),
                                ("毛利", 17593.34), ("二、营业利润", -23368.96)):
            self.assertEqual(row_by_label(summary, label)[3].value, expected)
        for sheet, metadata in ((detail, ["店铺", "所属平台", "核算期间"]),
                                (summary, ["店铺数量", "核算期间"])):
            self.assertEqual([row[0].value for row in sheet.iter_rows(min_row=len(response.rows) + 2)],
                             metadata)

    def test_export_unknown_writeoff_keeps_cost_and_masks_net_and_aggregate(self):
        auto = example()
        auto["inventory_writeoff"]["current"] = None
        auto["_warnings"] = ["转销归属待核对"]
        pending = _build_store_profit_loss_values(auto, {})
        book = Workbook()
        _build_store_profit_loss_sheet(book, STORE, MONTH, pending, {})
        _build_store_profit_loss_summary_sheet(book, [(STORE, pending), (STORE, _build_store_profit_loss_values(example(), {}))], MONTH)
        book = reload_book(book)
        detail, summary = book["09-店铺损益表"], book["10-各店铺损益汇总表"]
        self.assertEqual(row_by_label(detail, "毛利")[1].value, 8796.67)
        self.assertEqual(row_by_label(summary, "毛利")[3].value, 17593.34)
        for label in ("  本期销售转销", "减：存货跌价净影响", "二、营业利润", "三、净利润"):
            self.assertIsNone(row_by_label(detail, label)[1].value)
            for column in (1, 3, 4):
                self.assertIsNone(row_by_label(summary, label)[column].value)
        self.assertEqual(row_by_label(detail, "待核对")[1].value, "转销归属待核对")
        self.assertEqual(row_by_label(summary, "待核对")[1].value, "Lemos：转销归属待核对")

    async def test_frozen_legacy_versions_keep_their_own_labels_order_and_amounts(self):
        response = await self.response()
        for legacy_policy in (None, "order_created_fx_mxn8_income_only_v3"):
            with self.subTest(policy=legacy_policy):
                payload = {
                    "inventory": [], "product_lines": [], "warnings": [], "store": vars(STORE),
                    "report_date": "2026-10-31", "profit_loss": response.model_dump(mode="json"),
                }
                report = payload["profit_loss"]
                if legacy_policy is None:
                    report.pop("calculation_policy")
                else:
                    report["calculation_policy"] = legacy_policy
                rows = report["rows"]
                writeoff = next(row for row in rows if row["key"] == "inventory_writeoff")
                rows.remove(writeoff)
                rows.insert(next(i for i, row in enumerate(rows) if row["key"] == "gross_profit"), writeoff)
                for row in rows:
                    key = row["key"]
                    if key == "inventory_writeoff":
                        row["label"] = "  减：销售转销跌价准备"
                    elif key == "inventory_impairment":
                        row["label"] = "    存货跌价准备"
                    elif key == "asset_impairment_loss":
                        row["label"], row["source"] = "减：资产减值损失", None
                    old = {"operating_cost": "16499.50", "gross_profit": "10637.25",
                           "asset_impairment_loss": "22321.73", "gross_margin": "0.392"}.get(key)
                    if old is not None:
                        row["value"] = dict.fromkeys(PERIODS, old)
                original = copy.deepcopy(payload)
                version = SimpleNamespace(payload=payload, report_month=MONTH, version=1, status="confirmed")
                sheet = load_workbook(version_workbook(version), data_only=True)["09-店铺损益表"]
                for number, row in enumerate(rows, 2):
                    self.assertEqual(sheet.cell(number, 1).value, row["label"])
                    for column, period in enumerate(PERIODS, 2):
                        value = row["value"][period]
                        self.assertEqual(sheet.cell(number, column).value, None if value is None else float(value))
                self.assertFalse(any(row[0].value == "跌价列报口径" for row in sheet))
                self.assertEqual(payload, original)

    async def test_v4_frozen_version_preserves_previous_net_profit_and_reserved_amounts(self):
        response = await self.response()
        frozen = response.model_dump(mode="json")
        frozen["calculation_policy"] = "order_created_fx_mxn8_net_impairment_v4"
        reserved = next(row for row in frozen["rows"] if row["key"] == "non_operating_income")
        reserved["is_reserved"] = True
        reserved["manual_value"] = dict.fromkeys(PERIODS, "100")
        payload = {"inventory": [], "product_lines": [], "warnings": [], "store": vars(STORE),
                   "report_date": "2026-10-31", "profit_loss": frozen}
        original = copy.deepcopy(payload)
        version = SimpleNamespace(payload=payload, report_month=MONTH, version=1, status="confirmed")
        book = load_workbook(version_workbook(version), data_only=True)
        sheet = book["09-店铺损益表"]
        self.assertIsNone(row_by_label(sheet, "加：营业外收入")[1].value)
        self.assertEqual(row_by_label(sheet, "三、净利润")[1].value, -11684.48)
        self.assertEqual(payload, original)
        book.close()

    async def test_new_frozen_version_exports_its_saved_net_amount_and_version(self):
        response = await self.response()
        payload = {"inventory": [], "product_lines": [], "warnings": [], "store": vars(STORE),
                   "report_date": "2026-10-31", "profit_loss": response.model_dump(mode="json")}
        version = SimpleNamespace(payload=payload, report_month=MONTH, version=2, status="confirmed")
        original = copy.deepcopy(payload)
        book = load_workbook(version_workbook(version), data_only=True)
        sheet = book["09-店铺损益表"]
        self.assertEqual(row_by_label(sheet, "减：存货跌价净影响")[1].value, 20481.15)
        self.assertEqual(row_by_label(sheet, "  本期销售转销")[1].value, 1840.58)
        self.assertIsNone(row_by_label(sheet, "信用减值损失")[1].value)
        self.assertEqual(row_by_label(sheet, "毛利")[1].value, 8796.67)
        self.assertEqual(row_by_label(sheet, "二、营业利润")[1].value, -11684.48)
        self.assertNotIn("跌价列报口径", [row[0].value for row in sheet])
        version_sheet = book["版本及核对说明"]
        self.assertEqual(version_sheet.max_row, 1)
        self.assertEqual([cell.value for cell in version_sheet[1][2:6]], ["版本", 2, "状态", "confirmed"])
        self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
