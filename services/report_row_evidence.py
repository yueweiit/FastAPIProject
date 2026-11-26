"""07/12只读计算依据。只记录报表已采用的输入、结果，不写回业务事实。"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
from hashlib import sha256
import json

from fastapi import HTTPException

from services.profit_loss_evidence import EvidenceCache, NOTES, STATUS, serial, source_row
from services.batch_impairment_history import rule_at
from services.batch_store_allocations import (
    allocated_batch_amount, allocated_provision_period, allocated_provision_transfers,
)
from services.cost_layers import base_cost_components

VERSION = "report_row_evidence_v1"
ZERO = Decimal(0)
CACHE = EvidenceCache()
LABELS = {
    "zh-CN": {
        "original_quantity": "原入库数量", "original_inventory_amount": "原入库成本",
        "sold_quantity": "累计销售数量", "sold_cost_amount": "累计销售成本",
        "quantity": "期末数量", "provision_quantity": "期末计提数量", "unit_cost": "有效单位成本",
        "age_days": "库龄天数", "inventory_amount": "期末库存成本", "impairment_rate": "期末准备比例",
        "impairment_amount": "期末跌价准备", "monthly_impairment_amount": "期末存货本月增提",
        "opening_impairment_amount": "月初跌价准备", "monthly_total_impairment_amount": "本月总计提",
        "monthly_writeoff_amount": "本月准备转销", "monthly_transfer_in_amount": "本月准备转入",
        "monthly_transfer_out_amount": "本月准备转出", "book_value": "期末账面价值",
        "previous_book_value": "上月账面价值", "book_value_difference": "账面价值差额", "batch_count": "批次数",
        "current_quantity": "本月销售数量", "margin_change": "毛利率变动（百分点）",
        **{f"{period}_{key}": f"{label}{name}" for period, label in
           (("current", "本月"), ("previous", "上月"), ("ytd", "本年累计"))
           for key, name in (("revenue", "收入"), ("cost", "成本"), ("gross", "毛利"), ("margin", "毛利率"))},
    }
}
INVENTORY_NOTES = {
    "unit_cost": "原FIFO基准单位成本加已启用的实际成本补差；不会改写原批次或出库单价。",
    "age_days": "报告截止日减入库自然日，最小为0；它不是全部存货的累计计提天数。",
    "original_quantity": "批次原入库数量；期初扣减保留为独立基线。单店归属报表可为折算数量。",
    "sold_quantity": "截止日前已保存FIFO出库件数，不含期初扣减。共享库存仅按日期列示匿名数量，不展示其他店铺订单。",
    "sold_cost_amount": "已保存出库数量乘当时FIFO单价，加已启用补差；可能不同于当前批次单价乘累计销量。",
    "quantity": "原入库数量减期初扣减再减截止日前实际销售。单店报表按截止日有效归属折算，并非该店实物存量。",
    "provision_quantity": "持有期间回放得到的期末计提数量；稳健商品保护最新库存的独立安全库存，新品不扣安全库存。",
    "impairment_rate": "期末保留准备对应的比例，由每日有效类型、安全库存和销售流水回放；规则切换不会清除已提准备。",
    "impairment_amount": "期末持有库存的准备余额。月初准备加本月总计提减销售转销，再加归属转入减转出。",
    "monthly_impairment_amount": "仅期末仍持有库存在本月增加的准备，不含本月已经售出的部分；不可替代本月总计提。",
    "monthly_total_impairment_amount": "本月实际持有期间每日增提之和，包含已售部分售前计提；入库当天0天，销售当天先计提再转销，单层准备比例最多90%。",
    "monthly_writeoff_amount": "销售消耗的已计提准备，可能含以前月份金额；不同于销售页面的当月参考跌价。",
    "opening_impairment_amount": "按上月末持有库存、准备单位和当时有效归属得到月初准备；不是读取或补建旧快照。",
    "monthly_transfer_in_amount": "按归属变更日开始前未转销准备计算转入，不增加当月计提费用。",
    "monthly_transfer_out_amount": "按归属变更日开始前未转销准备计算转出，不增加当月销售转销。",
    "original_inventory_amount": "原入库数量乘有效单位成本；单店按固定精度分配金额，不能用折算数量自行重新舍入。",
    "inventory_amount": "期末数量乘有效单位成本；单店金额按固定精度归属分配。",
    "previous_book_value": "上月末库存成本减上月末准备；缺少或异常基准保持未知。",
    "book_value": "期末库存成本减期末准备。",
    "book_value_difference": "期末账面价值减上月账面价值；不是本月利润或单独的跌价费用。",
}
INVENTORY_FORMULAS = {
    "original_inventory_amount": [("original_quantity", "+"), ("unit_cost", "×")],
    "inventory_amount": [("quantity", "+"), ("unit_cost", "×")],
    "impairment_amount": [("opening_impairment_amount", "+"), ("monthly_total_impairment_amount", "+"),
                          ("monthly_writeoff_amount", "−"), ("monthly_transfer_in_amount", "+"),
                          ("monthly_transfer_out_amount", "−")],
    "book_value": [("inventory_amount", "+"), ("impairment_amount", "−")],
    "book_value_difference": [("book_value", "+"), ("previous_book_value", "−")],
}


def known_sum(values):
    values = list(values)
    return None if any(v is None for v in values) else sum(values, ZERO)


def subtract(a, b):
    return None if a is None or b is None else a - b


def field_format(key):
    if "margin" in key or key == "impairment_rate":
        return "percent"
    return "number" if "quantity" in key or key in ("age_days", "batch_count") else "money"


def record(item, field, day, amount, source, reference="", **details):
    item.setdefault("_source_rows", {}).setdefault(field, []).append(source_row(day, amount, source, reference, **details))


def expression_text(field):
    missing = "待核对/未提供"
    if not field["inputs"]:
        return "来源合计：" + str(field["source_total"] if field["source_total"] is not None else missing)
    expression = " ".join((i["operator"] + " " if index else "") +
        f"{i['label']} ({i['value'] if i['value'] is not None else missing})" for index, i in enumerate(field["inputs"]))
    result = str(field["value"] if field["value"] is not None else missing)
    if field["format"] == "percent" and field["value"] is not None:
        value = Decimal(str(field['value']))
        with localcontext() as context:
            context.prec = max(context.prec, value.adjusted() + 5)
            result += f"（{(value * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%）"
    return expression + " = " + result


def finish(kind, row_key, title, context, values, sources, notes, formulas=None, warning=""):
    fields = {}
    for key, value in values.items():
        deps = (formulas or {}).get(key, [])
        rows = sources.get(key, [])
        total = known_sum(Decimal(r["amount"]) if r["amount"] is not None else None for r in rows)
        status = "unknown" if value is None else "complete"
        if any(r.get("scope_status") == "restricted" for r in rows):
            status = "partial"
        elif not deps and value is not None and (total is None or abs(Decimal(value) - total) > Decimal("1e-12")):
            status = "partial"
        fields[key] = {"label": LABELS["zh-CN"].get(key, key), "format": field_format(key),
                       "value": value, "note": notes.get(key, ""), "status": status,
                       "formula": " ".join((op + " " if i else "") + LABELS["zh-CN"].get(k, k)
                                           for i, (k, op) in enumerate(deps)) if deps else "来源参与值相加",
                       "inputs": [{"key": k, "operator": op, "label": LABELS["zh-CN"].get(k, k),
                                   "value": values.get(k), "format": field_format(k)} for k, op in deps],
                       "source_total": None if deps else total, "source_count": len(rows)}
    for _ in range(len(fields)):
        changed = False
        for field in fields.values():
            if field["status"] == "complete" and any(fields.get(i["key"], {}).get("status") == "partial" for i in field["inputs"]):
                field["status"], changed = "partial", True
        if not changed:
            break
    evidence = serial({"schema_version": VERSION, "kind": kind, "row_key": str(row_key), "title": title,
                       "context": context, "fields": fields, "sources": sources,
                       "warning": warning, "rounding": NOTES["rounding"], "status_labels": STATUS})
    for field in evidence["fields"].values():
        field["expression"] = expression_text(field)
    # 稳定排序用于不可变月度版本去重；不包含查询时间、缓存令牌或页面行号。
    for rows in evidence["sources"].values():
        rows.sort(key=lambda r: json.dumps(r, ensure_ascii=False, sort_keys=True))
    evidence["calculation_id"] = sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    return evidence


def product_evidence(item, report_month):
    values = {"current_quantity": item["current_quantity"]}
    formulas, notes = {}, {"current_quantity": "本月已保存销售的本地库存件数；组合商品按拆分件数，不是订单数。"}
    for period in ("current", "previous", "ytd"):
        revenue, cost = item[period]["revenue"], item[period]["cost"]
        gross = subtract(revenue, cost)
        values.update({f"{period}_revenue": revenue, f"{period}_cost": cost, f"{period}_gross": gross,
                       f"{period}_margin": None if gross is None or not revenue else gross / revenue})
        formulas[f"{period}_gross"] = [(f"{period}_revenue", "+"), (f"{period}_cost", "−")]
        formulas[f"{period}_margin"] = [(f"{period}_gross", "+"), (f"{period}_revenue", "÷")]
        notes[f"{period}_revenue"] = "按本地商品归集销售及独立结算净商品收入，再加该店人工收入分摊；原币按销售所属月的报表汇率折算。累计逐月取率。"
        notes[f"{period}_cost"] = "已保存FIFO成本及补差、平台费用、月度代发费与人工直接费用分摊之和，减库存准备转销。沿用12原口径，未改成09净跌价展示。负数来源表示抵减。"
        notes[f"{period}_gross"] = "收入减成本；任一输入未知，毛利保持未知。"
        notes[f"{period}_margin"] = "本期间毛利除本期间收入；零收入或未知输入不计算比例，不把各月比例相加。"
    values["margin_change"] = subtract(values["current_margin"], values["previous_margin"])
    formulas["margin_change"] = [("current_margin", "+"), ("previous_margin", "−")]
    notes["margin_change"] = "本月毛利率减上月毛利率，按百分点解释；任一期比例不可计算则留空。"
    return finish("product", item.get("product_id") or item.get("row_type"), item["name"], str(report_month),
                  values, item.get("_source_rows", {}), notes, formulas, item.get("note", ""))


def inventory_evidence(row, batch, sales, replay, rules, accruals, report_date, store_id):
    """销售仅匿名按日聚合；共享库存依据不可暴露销售ID/订单/结算原文。"""
    start, end = report_date.replace(day=1), report_date + timedelta(days=1)
    previous_day = start - timedelta(days=1)
    current, previous = replay.balances[report_date][batch.id], replay.balances[previous_day][batch.id]
    scoped = row["impairment_allocation_applied"]
    stores = rule_at(batch, report_date).stores
    fraction = (Decimal(1) / len(stores) if store_id in {s["id"] for s in stores} else ZERO) if scoped else Decimal(1)
    def share(value):
        return allocated_batch_amount(value, batch, store_id, stores=stores) if scoped else value
    details = {"批次": batch.batch_no, "入库日": row["arrived_at"], "有效单位成本": row["unit_cost"],
               "截止日": report_date, "计提类型（截止日）": row.get("impairment_product_type"),
               "类型及安全库存生效历史": rules, "归属折算": scoped,
               "截止日归属数量": len(stores), "本店数量份额": fraction if scoped else None,
               "归属规则": [{"生效日": r.effective_date, "归属数量": len(r.stores),
                              "本店参与": store_id in {s["id"] for s in r.stores} if scoped else None}
                             for r in batch.allocation_rules],
               "说明": row["comparison_note"]}
    values = {key: row.get(key) for key in INVENTORY_NOTES if key not in ("age_days", "book_value", "book_value_difference")}
    values.update(age_days=max(0, (report_date - row["arrived_at"]).days),
                  book_value=subtract(row["inventory_amount"], row["impairment_amount"]))
    values["book_value_difference"] = subtract(values["book_value"], row["previous_book_value"])
    sources = {}
    def add(key, day, amount, source="批次及持有期间回放", **extra):
        sources.setdefault(key, []).append(source_row(day, amount, source, batch.batch_no, **extra))
    # 日流水在分享前汇总，固定精度按累计差分分配，保证明细之和等于原报表分配额。
    daily = defaultdict(lambda: {"qty": ZERO, "cost": ZERO, "opening": ZERO, "cost_lines": defaultdict(lambda: ZERO)})
    for sale in sales:
        day = sale.sold_at.date()
        opening = isinstance(getattr(sale, "business_metadata", None), dict) and sale.business_metadata.get("kind") == "opening_deduction"
        daily[day]["opening" if opening else "qty"] += sale.report_quantity
        if not opening:
            daily[day]["cost"] += sale.report_quantity * sale.saved_unit_cost
            daily[day]["cost_lines"][sale.saved_unit_cost] += sale.report_quantity
    add("quantity", row["arrived_at"], Decimal(batch.quantity) * fraction, "原入库数量", **details)
    cumulative = ZERO
    for day, data in sorted(daily.items()):
        cost = share(cumulative + data["cost"]) - share(cumulative)
        cumulative += data["cost"]
        add("sold_quantity", day, data["qty"] * fraction, "匿名FIFO出库数量", **{"全局当日件数": data["qty"], "本店数量份额": fraction})
        add("sold_cost_amount", day, cost, "匿名FIFO保存成本及补差", **{"全局当日保存成本及补差": data["cost"], "全局当日件数": data["qty"],
            "当日保存单价明细（含已启用补差）": [{"单价": unit, "数量": qty, "金额": unit * qty} for unit, qty in sorted(data["cost_lines"].items())],
            "分配方式": "累计固定精度差分" if scoped else "全局"})
        add("quantity", day, -(data["qty"] + data["opening"]) * fraction, "数量扣减", **{"实际出库": data["qty"], "期初扣减": data["opening"], "本店数量份额": fraction})
    for key in ("monthly_total_impairment_amount", "monthly_writeoff_amount"):
        writeoff = key == "monthly_writeoff_amount"
        events = replay.writeoffs_by_batch.get(batch.id, []) if writeoff else accruals
        days = sorted({r.sale.sold_at.date() if writeoff else r.day for r in events})
        if not writeoff:
            # accrued_units同样传播异常转销；不能只列正常增提日而漏掉未知来源。
            days = sorted(set(days) | {r.sale.sold_at.date() for r in replay.writeoffs_by_batch.get(batch.id, []) if r.units is None})
        for day in days:
            if not start <= day < end:
                continue
            units = (replay.written_off_units if writeoff else replay.accrued_units)(batch.id, day, day + timedelta(days=1))
            amount = allocated_provision_period(replay, batch, store_id, day, day + timedelta(days=1), writeoff=writeoff) if scoped else (None if units is None else units * batch.unit_cost)
            add(key, day, amount, "匿名准备转销" if writeoff else "每日持有增提", **{"全局准备单位": units, "有效单位成本": batch.unit_cost,
                "计算": "准备单位 × 有效单位成本，再按当日有效归属分配" if scoped else "准备单位 × 有效单位成本",
                "准备单位含义": "回放各数量层的件数 × 当日转销比例之和" if writeoff else "回放各数量层的件数 × 当日增加比例之和",
                "按当日归属分配": scoped})
    for rule in batch.allocation_rules[1:]:
        day = rule.effective_date
        if start <= day < end:
            incoming, outgoing = allocated_provision_transfers(replay, batch, store_id, day, day + timedelta(days=1))
            for key, amount in (("monthly_transfer_in_amount", incoming), ("monthly_transfer_out_amount", outgoing)):
                add(key, day, amount, "准备归属转移", **{"变更前归属数量": len(rule_at(batch, day - timedelta(days=1)).stores), "变更后归属数量": len(rule.stores), "不计入费用": True})
    formulas = dict(INVENTORY_FORMULAS)
    if values['impairment_amount'] is not None and any(values[k] is None for k, _ in formulas['impairment_amount']):
        # 售完/尚未到货的期末余额可为已知0，但期间异常转销未知，不能伪造平衡式。
        formulas.pop('impairment_amount')
    if scoped:
        for key in ("original_inventory_amount", "inventory_amount"):
            formulas.pop(key)
    for key, value in values.items():
        if key in sources or key in formulas:
            continue
        extra = dict(details)
        if key == "unit_cost":
            extra.update({"原基准单价": row["base_unit_cost"], "单位补差": row["cost_adjustment_per_unit"],
                          "基准成本分项": base_cost_components(batch),
                          "补差凭据": [{"凭据ID": header.id, "分类": header.category, "分配数量": line.quantity,
                                      "调整金额": line.amount_cny, "原凭据": header.source_ref}
                                     for line, header in getattr(batch, "layers", [])]})
        if key in ("opening_impairment_amount", "previous_book_value"):
            extra.update({"基准日": previous_day, "全局上月末数量": previous.quantity, "全局上月末准备单位": previous.impairment_units})
        if key in ("provision_quantity", "impairment_rate", "monthly_impairment_amount"):
            extra.update({"全局期末数量": current.quantity, "全局期末计提数量": current.provision_quantity,
                          "全局期末准备单位": current.impairment_units, "全局期末存货本月准备单位": current.period_impairment_units})
        if key in ("sold_quantity", "sold_cost_amount", "monthly_total_impairment_amount", "monthly_writeoff_amount",
                   "monthly_transfer_in_amount", "monthly_transfer_out_amount") and value == 0:
            continue
        add(key, report_date, value, **extra)
    return finish("inventory", batch.id, f"{row['sku']} · {batch.batch_no}", str(report_date), values, sources,
                  INVENTORY_NOTES, formulas, row["comparison_note"])


def summary_evidence(summary, items):
    if not all(item.get("_evidence", {}).get("schema_version") == VERSION for item in items):
        return None
    values = {key: value for key, value in summary.items() if key in LABELS["zh-CN"]}
    sources = {key: [source_row(item["arrived_at"], 1 if key == "batch_count" else item["_evidence"]["fields"][key]["value"],
                       "批次汇总", item["batch_no"], scope_status="restricted" if key != "batch_count" and item["_evidence"]["fields"][key]["status"] == "partial" else None,
                       **{"批次": item["batch_no"], "批次计算编号": item["_evidence"]["calculation_id"],
                          "说明": item["comparison_note"]}) for item in items] for key in values}
    return finish("inventory_summary", items[0]["product_id"], f"{summary['sku']} · {summary['product_name']}",
                  items[0]["_evidence"]["context"], values, sources,
                  {key: "本SKU各批次同字段相加；任一批次未知则合计保持未知。批次依据见07明细。" for key in values},
                  warning=summary.get("comparison_note", ""))


def total_evidence(rows):
    if not rows or not all(row.get("_evidence", {}).get("schema_version") == VERSION for row in rows):
        return None
    first = rows[0]["_evidence"]
    sources, values = {}, {}
    for key in first["fields"]:
        if key in ("unit_cost", "age_days", "impairment_rate") or "margin" in key:
            continue
        sources[key] = [source_row(e["context"], e["fields"][key]["value"], "报表行合计", e["title"],
            scope_status="restricted" if e["fields"][key]["status"] == "partial" else None,
            **{"来源行": e["row_key"], "计算编号": e["calculation_id"]}) for e in (r["_evidence"] for r in rows)]
        values[key] = known_sum(Decimal(r["amount"]) if r["amount"] is not None else None for r in sources[key])
    formulas = {}
    if first["kind"] == "product":
        for period in ("current", "previous", "ytd"):
            gross, revenue = values[f"{period}_gross"], values[f"{period}_revenue"]
            values[f"{period}_margin"] = None if gross is None or not revenue else gross / revenue
            formulas[f"{period}_margin"] = [(f"{period}_gross", "+"), (f"{period}_revenue", "÷")]
        values["margin_change"] = subtract(values["current_margin"], values["previous_margin"])
        formulas["margin_change"] = [("current_margin", "+"), ("previous_margin", "−")]
    return finish(first["kind"], "__total__", "合计", first["context"], values, sources,
        {key: "金额/数量按报表行相加；未知不填0。合计毛利率为合计毛利除合计收入，不相加或平均行比例。" for key in values}, formulas)


def publish_rows(rows, user, *, kind, store_ids=()):
    evidence = {str(row["_evidence"]["row_key"]): row["_evidence"] for row in rows if row.get("_evidence")}
    total = total_evidence(rows)
    if total:
        evidence["__total__"] = total
    token = CACHE.put({"kind": kind, "store_ids": sorted(store_ids), "rows": evidence}, user.id) if evidence else None
    return [{"schema_version": VERSION, "row_key": row["_evidence"]["row_key"], "token": token,
             "calculation_id": row["_evidence"]["calculation_id"],
             "total_calculation_id": total["calculation_id"] if total else None} if row.get("_evidence") else None for row in rows]


def row_page(snapshot, row_key, field, page, page_size):
    row = snapshot["rows"].get(row_key)
    if not row or (field and field not in row["fields"]):
        raise HTTPException(404, detail="报表行或字段不存在")
    field = field or next(iter(row["fields"]))
    sources = row["sources"].get(field, [])
    return {**{key: value for key, value in row.items() if key != "sources"}, "field": field,
            "total": len(sources), "page": page, "items": sources[(page - 1) * page_size:page * page_size]}
