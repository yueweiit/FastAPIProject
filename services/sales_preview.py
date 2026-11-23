"""绑定用户、来源与实际扣库计划的短期预览签名；不保存业务数据。"""
import base64
import hashlib
import hmac
import json
import time

from fastapi import HTTPException


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


def _encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _signature(raw):
    from auth import SECRET_KEY
    return hmac.new(SECRET_KEY.encode(), b"sales-deduction-preview-v1:" + raw, hashlib.sha256).digest()


def issue(context, plan):
    raw = json.dumps({"context": context, "plan": digest(plan), "expires": int(time.time()) + 900}, sort_keys=True).encode()
    return _encode(raw) + "." + _encode(_signature(raw))


def verify(token, context, plan):
    try:
        if len(token) > 2048:
            raise ValueError
        raw, signature = [base64.b64decode(p + "=" * (-len(p) % 4), altchars=b"-_", validate=True) for p in token.split(".")]
        if not hmac.compare_digest(signature, _signature(raw)):
            raise ValueError
        data = json.loads(raw)
        if data["context"] != context or data["expires"] <= int(time.time()) or data["plan"] != digest(plan):
            raise HTTPException(409, detail={"code": "sales_preview_changed", "message": "文件、映射、来源或可用库存已变化，或预览已过期，请重新预览"})
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise HTTPException(400, detail={"code": "sales_preview_invalid", "message": "预览凭据无效，请重新预览"}) from None


def candidate_rows(candidates):
    from services.sales_mapping import first, LOCAL_SKU_HEADERS, MERCHANT_SKU_HEADERS
    return [{"row": int(item["row"]["_row_number"]), "order_no": item["order_no"],
             "source_sku": first(item["row"], ("SKU ID", *MERCHANT_SKU_HEADERS, *LOCAL_SKU_HEADERS, "SKU")),
             "product_name": item["row"].get("产品名") or item["row"].get("商品名称"),
             "source_quantity": item["quantity"], "sold_at": str(item["sold_at"]) if item["sold_at"] else None,
             "status": "pending" if item["mapping_error"] else "deduct" if item["is_inventory_sale"] else "settlement",
             "reason": item["mapping_error"], "resolution": item.get("resolution"),
             "components": [{"product_id": part["product"].id, "sku": part["product"].sku,
                 "name": part["product"].name, "specification": part["product"].specification,
                 "multiplier": part["multiplier"], "required_quantity": item["quantity"] * part["multiplier"],
                 "deduct_quantity": item["quantity"] * part["multiplier"] if item["is_inventory_sale"] else 0,
                 "available_quantity": part.get("available_quantity")} for part in item["components"]]}
            for item in candidates]
