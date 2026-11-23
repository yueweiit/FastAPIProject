"""销售编码匹配与不可变店铺映射；库存写入仍由原FIFO服务完成。"""
import hashlib
import json
import unicodedata
from collections import Counter

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import joinedload, selectinload

from models import Sale, SalesProductMapping, SalesProductMappingComponent, SettlementEntry, SettlementEntryAllocation, Store
from services.sales_product_identity import source_local_specification

LOCAL_SKU_HEADERS = ("本地SKU", "本地 SKU", "本地商品SKU", "本地商品 SKU")
MERCHANT_SKU_HEADERS = ("商家SKU", "商家 SKU", "商家编码", "Seller SKU", "Seller sku")


def text(value):
    return str(value).strip() if value is not None else ""


def first(row, headers):
    return next((text(row[key]) for key in headers if text(row.get(key))), "")


def normalized(kind, value):
    value = text(value)
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) if kind == "product_name" else value


def identity(kind, value):
    value = text(value)
    return {"kind": kind, "value": value, "hash": hashlib.sha256(normalized(kind, value).encode()).hexdigest()}


def local_components(value, products):
    """完整SKU优先；组件表达式按同一商品合并，供匹配及幂等共用。"""
    exact = [p for p in products if p.sku == value]
    if len(exact) == 1:
        return [{"product": exact[0], "component_name": exact[0].name, "multiplier": 1}]
    from routers.sales import _product_expression_parts
    try:
        declared = _product_expression_parts(value)
    except ValueError:
        return None
    parts = []
    for code, multiplier in declared:
        matched = [p for p in products if p.sku == code]
        if len(matched) != 1:
            return None
        parts.append({"product_id": matched[0].id, "multiplier": multiplier})
    return components_from_ids(parts, products)


def local_component_hash(parts):
    counts = Counter()
    for part in parts:
        counts[int(part["product_id"])] += int(part["multiplier"])
    value = json.dumps(sorted(counts.items()), separators=(",", ":"))
    return hashlib.sha256(("local-components-v1:" + value).encode()).hexdigest()


def source_identity(row, products=None, *, historical=False):
    """传商品目录时规范化本地组件；省略目录保留旧原文键及映射编辑标识。"""
    if value := text(row.get("SKU ID")):
        return identity("platform_sku", value)
    if value := first(row, MERCHANT_SKU_HEADERS):
        return identity("merchant_sku", value)
    if value := first(row, LOCAL_SKU_HEADERS):
        key = identity("local_sku", value)
        if products is not None:
            parts = local_components(value, products)
            saved = (row.get("_product_resolution") or {}).get("components") if historical else None
            if saved:
                # 历史已确认组件不随商品改码重新解释；不读取上传文件的内部元数据。
                key["hash"] = local_component_hash(saved)
            elif parts:
                key["hash"] = local_component_hash([
                    {"product_id": p["product"].id, "multiplier": p["multiplier"]} for p in parts])
        return key
    if value := first(row, ("产品名", "商品名称")):
        return identity("product_name", value)
    return None


def order_identifier(row, products=None):
    key = source_identity(row, products)
    if not key or key["kind"] == "product_name":
        # 名称可被改写，不能因改名就生成第二个出库占位。无编码同订单多行须先补可靠标识。
        return ""
    return key["value"] if key["kind"] == "platform_sku" else key["kind"] + ":" + key["hash"]


async def conflicting_source_orders(db, candidates, *, store_id=None, locked=False, products=None):
    """同订单从名称/商家编码改为另一种标识时，不能当作新出库明细。"""
    groups = {}
    for item in candidates:
        order = text(item["row"].get("订单ID/调整单ID"))
        if item["is_inventory_sale"] and order:
            shop = store_id if store_id is not None else item["entry"].store_id
            key = source_identity(item["row"], products)
            groups.setdefault((shop, order), []).append((item, key))
    if not groups:
        return []
    # 调用方先锁相关FIFO批次，再联合当前读直接销售和组合分配；
    # 不能依赖等待Store锁前的快照，也不能反向占住销售行再等库存。
    query = (select(SettlementEntry.store_id, SettlementEntry.order_id, SettlementEntry.platform_sku_id, SettlementEntry.raw_data)
        .outerjoin(SettlementEntryAllocation, SettlementEntryAllocation.settlement_entry_id == SettlementEntry.id)
        .join(Sale, or_(Sale.id == SettlementEntry.sale_id, Sale.id == SettlementEntryAllocation.sale_id))
        .where(SettlementEntry.store_id.in_({key[0] for key in groups}), SettlementEntry.order_id.in_({key[1] for key in groups}))
        .order_by(SettlementEntry.id, SettlementEntryAllocation.id, Sale.id))
    existing = {}
    for row in (await db.execute(query.with_for_update() if locked else query)).all():
        raw = row.raw_data or {}
        key = source_identity(raw, products, historical=True)
        kind = key["kind"] if key else "platform_sku" if row.platform_sku_id else "product_name"
        existing.setdefault((row.store_id, row.order_id), set()).add((kind, key["hash"] if key else None))
        if kind == "local_sku":
            # 人工改选组件仍保留来源标识；历史快照和原来源两种比较键都须防重。
            source = source_identity(raw, products)
            existing[(row.store_id, row.order_id)].add((kind, source["hash"]))
    conflicts = []
    for key, items in groups.items():
        current = {identity["kind"] if identity else "product_name" for _item, identity in items}
        saved = existing.get(key, set())
        previous = {kind for kind, _hash in saved}
        if (len(items) > 1 and (len(current) > 1 or "product_name" in current)
                or previous and ("product_name" in previous | current or len(previous | current) > 1)):
            conflicts.extend(item for item, _kind in items)
        else:
            # 兼容旧原文哈希占位：同组件换写法不能生成第二次出库。
            conflicts.extend(item for item, identity in items if identity and identity["kind"] == "local_sku"
                             and ("local_sku", identity["hash"]) in saved)
    return conflicts


async def lock_stores(db, store_ids):
    # 所有映射写入、导入和重试均先锁店铺，再锁导入头/来源/FIFO批次。
    return (await db.execute(select(Store).where(Store.id.in_(sorted(set(store_ids))))
        .order_by(Store.id).with_for_update().execution_options(populate_existing=True))).scalars().all()


async def load_mappings(db, store_ids, *, locked=False):
    # MySQL REPEATABLE READ 下等待店铺锁前可能已有读取快照。
    # 写入路径将版本及组件一起锁定读取，避免拿到等待前的旧版本或空组件。
    loader = joinedload if locked else selectinload
    statement = select(SalesProductMapping).options(loader(SalesProductMapping.components))
    statement = (statement
        .where(SalesProductMapping.store_id.in_(store_ids)).order_by(SalesProductMapping.effective_from, SalesProductMapping.id)
        .execution_options(populate_existing=True))
    return (await db.execute(statement.with_for_update() if locked else statement)).unique().scalars().all()


def versions(mappings, store_id, key):
    return [m for m in mappings if m.store_id == store_id and m.key_kind == key["kind"] and m.key_hash == key["hash"]]


def components_from_ids(parts, products):
    by_id = {p.id: p for p in products}
    counts = Counter()
    for part in parts:
        counts[int(part["product_id"])] += int(part["multiplier"])
    if not counts or any(key not in by_id or not 0 < count <= 2147483647 for key, count in counts.items()):
        return None
    return [{"product": by_id[key], "component_name": by_id[key].name, "multiplier": count}
            for key, count in sorted(counts.items())]


def snapshot(parts, method, version_id=None):
    return {"method": method, "version_id": version_id, "components": [
        {"product_id": p["product"].id, "sku": p["product"].sku, "name": p["product"].name,
         "specification": p["product"].specification, "multiplier": p["multiplier"]} for p in parts]}


def precision_error(row):
    if any(isinstance(row.get(key), (int, float)) and abs(row[key]) >= 10**15
           for key in ("订单ID/调整单ID", "SKU ID", *LOCAL_SKU_HEADERS, *MERCHANT_SKU_HEADERS)):
        return "待确认来源编码或订单号以长数字保存，可能已丢失精度；请从原始来源提供文本标识"
    return None


def resolve(row, products, mappings, store_id, on):
    """名称只使用确认过的别名；未知编码不回退名称或旧全局映射。"""
    if error := precision_error(row):
        return [], error, None
    local = first(row, LOCAL_SKU_HEADERS)
    direct = None
    if local:
        direct = local_components(local, products)
        if not direct:
            return [], "未找到本地商品映射: 本地SKU不存在或不唯一", None
    key = source_identity(row)
    keys = [key] if key and key["kind"] != "local_sku" else []
    merchant = first(row, MERCHANT_SKU_HEADERS)
    if merchant and (not key or key["kind"] != "merchant_sku"):
        keys.append(identity("merchant_sku", merchant))
    found = []
    for current in keys:
        candidates = versions(mappings, store_id, current)
        effective = [m for m in candidates if on is not None and m.effective_from <= on]
        if candidates and not effective:
            return [], "平台 SKU 映射在该销售日期无已确认版本", None
        if not effective:
            continue
        mapping = max(effective, key=lambda m: (m.effective_from, m.id))
        if not mapping.is_active:
            return [], "历史映射配置无效: 该销售日期的映射已停用", None
        parts = components_from_ids([{"product_id": c.product_id, "multiplier": c.multiplier} for c in mapping.components], products)
        if not parts:
            return [], "历史映射配置无效: 本地商品或扣库倍数无效", None
        found.append((parts, mapping))
    choices = ([direct] if direct else []) + [p for p, _ in found]
    signature = lambda parts: sorted((p["product"].id, p["multiplier"]) for p in parts)
    if len({tuple(signature(p)) for p in choices}) > 1:
        return [], "平台 SKU 映射到多个本地商品: 文件编码与已确认映射冲突", None
    if not choices:
        from routers.sales import _resolve_product_components
        suggested, error = _resolve_product_components(row, products, [], on, strict_names=True)
        if error:
            return [], error, None
        codes = "、".join(f"{p['product'].sku} × {p['multiplier']}" for p in suggested)
        return [], f"未配置平台 SKU ID 映射: {key['value'] if key else '-'}；名称候选为 {codes}，请确认商品及每份扣库数量", None
    parts = choices[0]
    spec = source_local_specification(row)
    if spec and (len(parts) != 1 or normalized("product_name", parts[0]["product"].specification) != normalized("product_name", spec)):
        return [], "平台 SKU 映射到多个本地商品: 指定规格与已确认商品冲突，请核对", None
    mapping = found[0][1] if found else None
    resolved = snapshot(parts, "store_mapping" if mapping else "local_sku", mapping.id if mapping else None)
    if mapping:
        resolved["source_unit"] = mapping.source_unit
    return parts, None, resolved


async def save_mapping(db, user, store_id, key, data, parts, *, is_active=True, locked=False):
    if user.role not in {"admin", "operator"} or user.role != "admin" and user.store_id != store_id:
        raise HTTPException(403, detail={"code": "sales_mapping_forbidden", "message": "只能维护有权限店铺的销售映射"})
    if key is None or key["kind"] not in {"platform_sku", "merchant_sku", "product_name"} or not key["value"] or len(key["value"]) > 500:
        raise HTTPException(422, detail={"code": "sales_mapping_identity_invalid", "message": "缺少有效来源编码或名称"})
    if key["kind"] == "product_name" and not data.confirm_name_unique:
        raise HTTPException(422, detail={"code": "sales_mapping_name_confirmation_required", "message": "保存名称别名前，请确认该名称在本店只代表这一组商品及装量"})
    if not locked:
        stores = await lock_stores(db, [store_id])
        if not stores or not stores[0].is_active:
            raise HTTPException(400, detail="店铺不存在或已停用")
    existing = versions(await load_mappings(db, [store_id], locked=True), store_id, key)
    latest = max((m.id for m in existing), default=None)
    if latest != data.expected_version_id:
        raise HTTPException(409, detail={"code": "sales_mapping_version_conflict", "message": "映射已有其他版本，请刷新核对后再保存"})
    mapping = SalesProductMapping(store_id=store_id, key_kind=key["kind"], key_value=key["value"], key_hash=key["hash"],
        effective_from=data.effective_from, source_unit=data.source_unit.strip() or "份", is_active=is_active, created_by=user.id)
    mapping.components = [SalesProductMappingComponent(product_id=p["product"].id, multiplier=p["multiplier"]) for p in parts]
    db.add(mapping)
    await db.flush()
    return mapping
