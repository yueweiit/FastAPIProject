import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI, HTTPException, UploadFile
from PIL import Image

from models import Product
from routers.products import create_product, update_product
from services.product_images import ProductImageFiles, normalize_product_image, read_product_image


def image_bytes(format="PNG"):
    output = BytesIO()
    Image.new("RGB", (4, 3), "red").save(output, format=format)
    return output.getvalue()


class ProductImageTests(unittest.IsolatedAsyncioTestCase):
    def test_real_image_is_reencoded_and_trailing_active_content_removed(self):
        suffix, content = normalize_product_image(image_bytes() + b"<script>void 0</script>", "photo.PNG")
        self.assertEqual(suffix, ".png")
        self.assertNotIn(b"<script>", content)
        with Image.open(BytesIO(content)) as image:
            self.assertEqual(image.size, (4, 3))
            self.assertEqual(image.format, "PNG")
            image.verify()

    def test_jpeg_is_supported(self):
        suffix, content = normalize_product_image(image_bytes("JPEG"), "photo.jpeg")
        self.assertEqual(suffix, ".jpg")
        with Image.open(BytesIO(content)) as image:
            image.verify()

    def test_active_formats_and_forged_images_are_rejected(self):
        for name, content in [("photo.html", b"<html>test</html>"), ("photo.svg", b"<svg/>"),
                              ("photo.jpg", b"<html>test</html>"), ("photo.png", b"not an image")]:
            with self.subTest(name=name), self.assertRaises(HTTPException) as error:
                normalize_product_image(content, name)
            self.assertEqual(error.exception.status_code, 422)

    async def test_reads_are_bounded_before_decode(self):
        image = SimpleNamespace(filename="photo.png", read=AsyncMock(return_value=b"12345"))
        with patch("services.product_images.MAX_IMAGE_BYTES", 4), self.assertRaises(HTTPException) as error:
            await read_product_image(image)
        self.assertEqual(error.exception.status_code, 413)
        image.read.assert_awaited_once_with(5)

    def test_pixel_budget_is_checked_before_decode(self):
        with patch("services.product_images.MAX_IMAGE_PIXELS", 10), self.assertRaises(HTTPException) as error:
            normalize_product_image(image_bytes(), "photo.png")
        self.assertEqual(error.exception.status_code, 413)

    async def test_legacy_active_files_are_blocked_and_images_are_sandboxed(self):
        with tempfile.TemporaryDirectory(prefix="fifo-image-test-") as directory:
            root = Path(directory)
            (root / "old.html").write_text("<title>test</title>", encoding="utf-8")
            (root / "old.svg").write_text("<svg/>", encoding="utf-8")
            (root / "photo.png").write_bytes(image_bytes())
            (root / "forged.jpg").write_text("<html>test</html>", encoding="utf-8")
            app = FastAPI()
            app.mount("/uploads", ProductImageFiles(directory=directory))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid") as client:
                for name in ("old.html", "old.svg"):
                    self.assertEqual((await client.get(f"/uploads/{name}")).status_code, 404)
                response = await client.get("/uploads/photo.png")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "image/png")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                self.assertIn("sandbox", response.headers["content-security-policy"])
                response = await client.get("/uploads/forged.jpg")
                self.assertEqual(response.headers["content-type"], "image/jpeg")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_invalid_upload_does_not_write_a_file_or_commit(self):
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
                             commit=AsyncMock())
        with patch("routers.products._save_product_image", new_callable=AsyncMock) as save, self.assertRaises(HTTPException):
            await create_product(sku="IMG-TEST", name="Test", product_type="new", safe_stock_quantity=0,
                                 image=UploadFile(BytesIO(b"<html/>"), filename="image.html"), db=db,
                                 user=SimpleNamespace(role="viewer"))
        save.assert_not_awaited()
        db.commit.assert_not_awaited()

    async def test_failed_create_removes_only_the_new_file(self):
        def add(value):
            if isinstance(value, Product):
                value.id = 1
        db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
                             add=Mock(side_effect=add), flush=AsyncMock(), rollback=AsyncMock(),
                             commit=AsyncMock(side_effect=RuntimeError("test transaction failure")))
        with tempfile.TemporaryDirectory(prefix="fifo-image-test-") as directory:
            root = Path(directory)
            old = root / "existing.png"
            old.write_bytes(image_bytes())
            with patch("routers.products.UPLOAD_DIR", root), self.assertRaises(RuntimeError):
                await create_product(sku="IMG-TEST", name="Test", product_type="new", safe_stock_quantity=0,
                                     image=UploadFile(BytesIO(image_bytes()), filename="photo.png"), db=db,
                                     user=SimpleNamespace(role="viewer"))
            self.assertEqual(list(root.iterdir()), [old])
        db.rollback.assert_awaited_once()

    async def test_failed_update_preserves_the_old_image(self):
        product = SimpleNamespace(id=1, image="/uploads/existing.png", created_at=datetime(2026,1,1))
        db = SimpleNamespace(get=AsyncMock(return_value=product),
                             execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
                             rollback=AsyncMock(), commit=AsyncMock(side_effect=RuntimeError("test transaction failure")))
        with tempfile.TemporaryDirectory(prefix="fifo-image-test-") as directory:
            root = Path(directory)
            old = root / "existing.png"
            old.write_bytes(image_bytes())
            with patch("routers.products.UPLOAD_DIR", root), self.assertRaises(RuntimeError):
                await update_product(1, sku="IMG-TEST", name="Test", product_type=None, safe_stock_quantity=None,
                                     image=UploadFile(BytesIO(image_bytes()), filename="photo.png"), db=db,
                                     user=SimpleNamespace(role="viewer"))
            self.assertEqual(list(root.iterdir()), [old])
        db.rollback.assert_awaited_once()
