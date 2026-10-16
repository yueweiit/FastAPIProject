"""商品图片的有界解码、重新编码和历史上传文件隔离。"""

from io import BytesIO
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles


MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_ENCODED_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp"}


def normalize_product_image(content: bytes, filename: str) -> tuple[str, bytes]:
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="商品图片不能超过5MB")
    if Path(filename).suffix.lower() not in IMAGE_TYPES:
        raise HTTPException(status_code=422, detail="仅支持JPEG、PNG、GIF或WebP图片")
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in {"JPEG", "PNG", "GIF", "WEBP"}:
                raise ValueError("unsupported image")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise HTTPException(status_code=413, detail="图片像素总数不能超过2000万")
            source.verify()
        with Image.open(BytesIO(content)) as source:
            # 商品图使用首帧；重新编码移除原文件的尾随内容及元数据。
            source.seek(0)
            corrected = ImageOps.exif_transpose(source)
            use_jpeg = source.format == "JPEG"
            image = corrected.convert("RGB" if use_jpeg else "RGBA")
            output = BytesIO()
            image.save(output, format="JPEG" if use_jpeg else "PNG")
            encoded = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422, detail="图片内容无效或已损坏，请重新选择图片") from exc
    if len(encoded) > MAX_ENCODED_BYTES:
        raise HTTPException(status_code=413, detail="图片重新编码后过大，请缩小尺寸后重试")
    return (".jpg" if use_jpeg else ".png"), encoded


async def read_product_image(image: UploadFile | None) -> tuple[str, bytes] | None:
    if image is None or not image.filename:
        return None
    content = await image.read(MAX_IMAGE_BYTES + 1)
    return await run_in_threadpool(normalize_product_image, content, image.filename)


class ProductImageFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        extension = Path(path).suffix.lower()
        if extension not in IMAGE_TYPES:
            # 历史HTML/SVG同样不可访问，不删除或改写已有上传文件。
            raise HTTPException(status_code=404, detail="图片不存在")
        response = await super().get_response(path, scope)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:"
        response.headers["Content-Type"] = IMAGE_TYPES[extension]
        return response
