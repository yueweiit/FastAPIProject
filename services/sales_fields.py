"""页面与Excel共用的局部字段说明资源；当前仅zh-CN，其他语言回退中文。"""
import json
from pathlib import Path


FIELD_RESOURCE = Path(__file__).resolve().parents[1] / "static" / "sales-fields.zh-CN.json"


def sales_field_help():
    return json.loads(FIELD_RESOURCE.read_text(encoding="utf-8"))
