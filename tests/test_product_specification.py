"""规格必填、历史空值、导入导出与兼容 DDL；仅隔离 SQLite/ASGI。"""

import unittest
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, HTTPException
from openpyxl import load_workbook
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from auth import get_current_user
from database import Base, get_db
from migrations.add_product_specification import DDL
from models import InventoryBatch, Product, ProductImpairmentRule
from routers.products import router
from schemas import ProductCreate, ProductImportRow
from services.product_import import (
    IMPORT_HEADERS, build_product_import_result, confirm_product_import, issue_product_import_token,
    parse_product_import, preview_product_import,
)
from services.product_specification import normalize_product_specification, specification_export_text
from tests.test_product_import import FakeSession, spreadsheet
from tests.test_product_types import ProductSession


class ProductSpecificationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.db = ProductSession(self.session)
        self.user = SimpleNamespace(id=1, role="viewer")
        app = FastAPI()
        app.include_router(router)

        async def db():
            yield self.db

        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.session.close()
        self.engine.dispose()

    async def test_create_requires_nonblank_bounded_specification_without_writes(self):
        for value in (None, "", " \t\n ", "规" * 256):
            with self.subTest(value=value):
                data = {"sku": "NEW", "name": "商品"}
                if value is not None:
                    data["specification"] = value
                response = await self.client.post("/products", data=data)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.session.scalar(select(func.count()).select_from(Product)), 0)
                self.assertEqual(self.session.scalar(select(func.count()).select_from(ProductImpairmentRule)), 0)

    async def test_all_roles_can_save_and_read_trimmed_specifications(self):
        for role in ("admin", "operator", "viewer"):
            self.user.role = role
            response = await self.client.post("/products", data={
                "sku": role, "name": "同名商品", "specification": "  黑色 / 500 mL  ",
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["specification"], "黑色 / 500 mL")
        self.assertEqual(len((await self.client.get("/products")).json()), 3)
        listed = (await self.client.get("/products?page=1")).json()
        self.assertTrue(all(p["specification"] == "黑色 / 500 mL" for p in listed["items"]))
        self.assertEqual((await self.client.get("/products/options")).json()[0]["specification"], "黑色 / 500 mL")
        duplicate = await self.client.post("/products", data={
            "sku": "viewer", "name": "同名商品", "specification": "另一规格",
        })
        self.assertEqual(duplicate.status_code, 400)

    async def test_legacy_product_is_readable_and_requires_specification_when_edited(self):
        legacy = Product(sku="OLD", name="历史商品", product_type="stable", safe_stock_quantity=20)
        self.session.add(legacy)
        self.session.commit()
        listed = (await self.client.get("/products?page=1")).json()["items"][0]
        self.assertIsNone(listed["specification"])
        for value in (None, "", "  ", "x" * 256):
            data = {"sku": "OLD", "name": "不应改名"}
            if value is not None:
                data["specification"] = value
            response = await self.client.put(f"/products/{legacy.id}", data=data)
            self.assertEqual(response.status_code, 422, response.text)
            self.session.refresh(legacy)
            self.assertEqual(legacy.name, "历史商品")
            self.assertIsNone(legacy.specification)
        response = await self.client.put(f"/products/{legacy.id}", data={
            "sku": "OLD", "name": "历史商品", "specification": " 白色 / L ",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["specification"], "白色 / L")
        self.assertEqual(response.json()["safe_stock_quantity"], 20)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(ProductImpairmentRule)), 0)
        response = await self.client.put(f"/products/{legacy.id}", data={
            "sku": "OLD", "name": "历史商品", "specification": " ",
        })
        self.assertEqual(response.status_code, 422)
        self.session.refresh(legacy)
        self.assertEqual(legacy.specification, "白色 / L")

    async def test_maximum_length_and_special_text_round_trip(self):
        for index, specification in enumerate(("规" * 255, "🔵" * 255, '<img src=x onerror="alert(1)">', "000123")):
            response = await self.client.post("/products", data={
                "sku": f"TEXT-{index}", "name": "商品", "specification": specification,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["specification"], specification)

    async def test_line_breaks_are_saved_as_separators_in_create_and_legacy_edit(self):
        raw = "  黑色\r\n标准版\r大号\nUSB\u2028套装\u2029一件  "
        expected = "黑色 标准版 大号 USB 套装 一件"
        response = await self.client.post("/products", data={"sku": "LINES", "name": "商品", "specification": raw})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["specification"], expected)
        legacy = Product(sku="OLD-LINES", name="历史商品", specification="黑色\n标准版")
        self.session.add(legacy)
        self.session.commit()
        response = await self.client.put(f"/products/{legacy.id}", data={
            "sku": legacy.sku, "name": legacy.name, "specification": legacy.specification,
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["specification"], "黑色 标准版")
        self.session.refresh(legacy)
        self.assertEqual(legacy.specification, "黑色 标准版")
        exported = await self.client.get("/products/export")
        imported = parse_product_import("roundtrip.xlsx", exported.content)
        self.assertEqual({row.specification for row in imported.rows}, {expected, "黑色 标准版"})

    async def test_xml_invalid_characters_rejected_before_create_or_edit(self):
        product = Product(sku="CONTROL-OLD", name="原名称", specification="原规格")
        self.session.add(product)
        self.session.commit()
        for char in ("\x00", "\x08", "\x0b", "\x0c", "\x1f", "\ufffe", "\uffff"):
            # 覆盖首尾字符也不能被 strip 静默去掉。
            for value in (f"黑色{char}标准版", char + "标准版", "标准版" + char):
                with self.subTest(value=repr(value)):
                    data = {"sku": "CONTROL-NEW", "name": "不能保存", "specification": value}
                    created = await self.client.post("/products", data=data)
                    self.assertEqual(created.status_code, 422, created.text)
                    self.assertEqual(created.json()["detail"]["code"], "product_specification_invalid_characters")
                    data["sku"] = product.sku
                    edited = await self.client.put(f"/products/{product.id}", data=data)
                    self.assertEqual(edited.status_code, 422, edited.text)
                    self.session.refresh(product)
                    self.assertEqual((product.name, product.specification), ("原名称", "原规格"))
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Product)), 1)

    async def test_legacy_invalid_specifications_do_not_break_bulk_export_or_change_data(self):
        original = "=黑色\x0b标准版\x00\ufffe"
        product = Product(sku="LEGACY-BAD", name="旧商品", specification=original)
        self.session.add_all([product, Product(sku="VALID", name="正常商品", specification="蓝色 / L")])
        self.session.commit()
        response = await self.client.get("/products/export")
        self.assertEqual(response.status_code, 200)
        sheet = load_workbook(BytesIO(response.content), data_only=False).active
        rows = {sheet.cell(row, 3).value: row for row in range(2, sheet.max_row + 1)}
        cell = sheet.cell(rows[product.sku], 5)
        self.assertEqual(cell.value, "=黑色[U+000B]标准版[U+0000][U+FFFE]")
        self.assertEqual(cell.data_type, "s")
        self.assertIn("原始数据未修改", cell.comment.text)
        self.assertEqual(sheet.cell(rows["VALID"], 5).value, "蓝色 / L")
        self.assertIsNone(sheet.cell(rows["VALID"], 5).comment)
        self.session.refresh(product)
        self.assertEqual(product.specification, original)

    async def test_export_preserves_legacy_null_literal_text_and_shifted_formats(self):
        legacy = Product(sku="LEGACY", name="旧商品", created_at=datetime(2026, 11, 1))
        literal = Product(sku="000001", name="=name", specification="=1+1", created_at=datetime(2026, 11, 2))
        self.session.add_all([legacy, literal])
        self.session.flush()
        self.session.add(InventoryBatch(
            product_id=literal.id, batch_no="SPEC-BATCH", quantity=4, remaining_quantity=3,
            purchase_price=Decimal("12.5"), unit_cost=Decimal("12.5"), arrived_at=datetime(2026, 11, 3),
        ))
        self.session.commit()
        response = await self.client.get("/products/export")
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else "")
        sheet = load_workbook(BytesIO(response.content), data_only=False).active
        self.assertEqual([sheet.cell(1, col).value for col in (4, 5, 6)], ["商品名称", "规格/型号", "商品类型"])
        rows = {sheet.cell(row, 3).value: row for row in range(2, sheet.max_row + 1)}
        row = rows["000001"]
        self.assertEqual(sheet.cell(row, 5).value, "=1+1")
        self.assertEqual(sheet.cell(row, 5).data_type, "s")
        self.assertEqual(sheet.cell(row, 5).number_format, "@")
        self.assertEqual(sheet.cell(row, 8).value, 3)
        self.assertEqual(sheet.cell(row, 9).value, 37.5)
        self.assertEqual(sheet.cell(row, 9).number_format, '¥#,##0.00')
        self.assertEqual(sheet.cell(row, 10).value, datetime(2026, 11, 3))
        self.assertEqual(sheet.cell(row, 11).number_format, "yyyy-mm-dd hh:mm")
        self.assertIsNone(sheet.cell(rows["LEGACY"], 5).value)
        imported = parse_product_import("export.xlsx", response.content)
        self.assertEqual(imported.rows[0].specification, "=1+1")
        self.assertFalse(imported.rows[0].errors)
        self.assertEqual(imported.rows[1].status, "error")


class ProductSpecificationImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_normalizes_line_breaks_before_preview_and_confirmation(self):
        parsed = parse_product_import("lines.xlsx", spreadsheet([
            ["LINES", "商品", " 黑色\r\n标准版\r大号\nUSB ", "新品", 0],
        ]))
        db = FakeSession(parsed.rows)
        preview = await preview_product_import(db, parsed)
        self.assertTrue(preview.can_import)
        self.assertEqual(preview.rows[0].specification, "黑色 标准版 大号 USB")
        token = issue_product_import_token(parsed, preview, 1, "fixture-only")
        result = await confirm_product_import(db, parsed, token, 1, "fixture-only")
        self.assertEqual(result.created_count, 1)
        self.assertEqual(db.added[0].specification, "黑色 标准版 大号 USB")

    async def test_invalid_specifications_block_preview_and_are_field_errors(self):
        values = (None, "", " \t ", 123, "x" * 256, "=1+1", "#N/A")
        content = spreadsheet([[f"SKU-{i}", "商品", spec, "新品", 0] for i, spec in enumerate(values)])
        parsed = parse_product_import("spec.xlsx", content)
        self.assertTrue(all(row.status == "error" for row in parsed.rows))
        self.assertTrue(all({e.field for e in row.errors} == {"规格/型号"} for row in parsed.rows))
        db = FakeSession(parsed.rows)
        report = await preview_product_import(db, parsed)
        self.assertFalse(report.can_import)
        self.assertEqual(report.error_count, len(values))
        db.commit.assert_not_awaited()

    def test_header_aliases_and_trimmed_text(self):
        for label in ("规格/型号", "规格", "型号", "specification"):
            headers = list(IMPORT_HEADERS)
            headers[2] = label
            parsed = parse_product_import("spec.xlsx", spreadsheet([["001", "商品", " 0007 / 蓝色 ", "新品", 0]], headers))
            self.assertFalse(parsed.rows[0].errors)
            self.assertEqual(parsed.rows[0].specification, "0007 / 蓝色")

    def test_old_template_requires_new_column_and_duplicate_aliases_are_rejected(self):
        for headers, message in (
            (("SKU", "商品名称", "商品类型", "安全库存"), "缺少必要列：规格/型号"),
            ((*IMPORT_HEADERS, "规格"), "重复列"),
        ):
            with self.assertRaises(HTTPException) as error:
                parse_product_import("old.xlsx", spreadsheet([], headers))
            self.assertIn(message, error.exception.detail)

    def test_result_export_does_not_interpret_specification_as_formula(self):
        sheet = load_workbook(build_product_import_result([
            ProductImportRow(row_number=2, sku="001", name="商品", specification="=1+1", product_type="new",
                             safe_stock_quantity=0, status="created", product_id=3),
        ]), data_only=False).active
        self.assertEqual(sheet["D1"].value, "规格/型号")
        self.assertEqual(sheet["D2"].value, "=1+1")
        self.assertEqual(sheet["D2"].data_type, "s")
        self.assertEqual(sheet["G2"].value, "已新增")
        self.assertEqual(sheet["H2"].value, 3)

    def test_result_export_marks_unsupported_characters_without_dropping_rows(self):
        row = ProductImportRow(row_number=2, sku="001", name="商品", specification="黑色\x0b\uffff标准版",
                               product_type="new", status="error", message="请修正规格")
        sheet = load_workbook(build_product_import_result([row]), data_only=False).active
        self.assertEqual(sheet["D2"].value, "黑色[U+000B][U+FFFF]标准版")
        self.assertIn("原始数据未修改", sheet["D2"].comment.text)
        self.assertEqual(sheet["G2"].value, "错误")
        self.assertEqual(row.specification, "黑色\x0b\uffff标准版")


class ProductSpecificationCompatibilityTests(unittest.TestCase):
    def test_additive_ddl_keeps_old_rows_and_allows_null(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, sku VARCHAR(64), name VARCHAR(255))"))
                connection.execute(text("INSERT INTO products VALUES (1, 'OLD', 'legacy')"))
                connection.execute(text(DDL))
                self.assertEqual(connection.execute(text("SELECT id, sku, name, specification FROM products")).one(),
                                 (1, "OLD", "legacy", None))
        finally:
            engine.dispose()

    def test_schema_normalizes_and_rejects_whitespace(self):
        self.assertEqual(ProductCreate(sku="A", name="商品", specification=" 标准款 ").specification, "标准款")
        self.assertEqual(ProductCreate(sku="A", name="商品", specification="黑色\r\n标准版").specification, "黑色 标准版")
        for value in ("   ", "黑色\x0b标准版", "\ud800"):
            with self.assertRaises(ValueError):
                ProductCreate(sku="A", name="商品", specification=value)

    def test_all_xml_invalid_ranges_and_valid_supplementary_characters(self):
        for codepoint in (*range(9), 11, 12, *range(14, 32), 0xD800, 0xDFFF, 0xFFFE, 0xFFFF):
            value = "A" + chr(codepoint) + "B"
            with self.subTest(codepoint=codepoint), self.assertRaises(HTTPException) as error:
                normalize_product_specification(value)
            self.assertEqual(error.exception.detail["code"], "product_specification_invalid_characters")
            self.assertEqual(specification_export_text(value), f"A[U+{codepoint:04X}]B")
        self.assertEqual(normalize_product_specification("🔵\t标准版"), "🔵\t标准版")
        self.assertEqual(specification_export_text("🔵\t标准版"), "🔵\t标准版")

    def test_limit_is_checked_after_normalizing_crlf(self):
        raw = "A" * 127 + "\r\n" + "B" * 127
        self.assertEqual(len(normalize_product_specification(raw)), 255)
        with self.assertRaises(HTTPException):
            normalize_product_specification(raw + "C")
