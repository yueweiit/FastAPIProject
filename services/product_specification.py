"""商品规格的写入校验及中文导出标签；历史空值仅在读取时兼容。"""

import re

from fastapi import HTTPException


SPECIFICATION_MAX_LENGTH = 255
SPECIFICATION_LABEL = "规格/型号"
# XML 1.0 不支持这些字符；先校验原文，不能通过 strip 悄悄丢弃首尾控制字符。
INVALID_SPECIFICATION_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
SPECIFICATION_LINE_BREAKS = re.compile(r"\r\n|[\r\n\u2028\u2029]")
SPECIFICATION_ERRORS = {
    "product_specification_required": "请填写规格/型号",
    "product_specification_too_long": "规格/型号不能超过 255 字符",
    "product_specification_invalid_characters": "规格/型号含无法保存到 Excel 的字符，请删除后重试",
}
SPECIFICATION_EXPORT_WARNING = "原规格含 Excel 不支持的字符，已用 [U+XXXX] 标出；原始数据未修改，请核对后在商品编辑中修正。"


def normalize_product_specification(value: str | None) -> str:
    code = None
    if not isinstance(value, str):
        code = "product_specification_required"
    elif INVALID_SPECIFICATION_CHARACTERS.search(value):
        code = "product_specification_invalid_characters"
    else:
        value = SPECIFICATION_LINE_BREAKS.sub(" ", value).strip()
        if not value:
            code = "product_specification_required"
        elif len(value) > SPECIFICATION_MAX_LENGTH:
            code = "product_specification_too_long"
    if code:
        raise HTTPException(422, detail={"code": code, "message": SPECIFICATION_ERRORS[code]})
    return value


def specification_export_text(value: str | None) -> str | None:
    """历史异常规格仍可导出，以可见码点保留问题位置；不改写数据库。"""
    if value is None:
        return None
    return INVALID_SPECIFICATION_CHARACTERS.sub(lambda match: f"[U+{ord(match[0]):04X}]", value)
