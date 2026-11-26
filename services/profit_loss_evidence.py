"""只读损益依据：记录参与本次计算的事实，不计算/写回销售或库存。"""
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from secrets import token_urlsafe
from time import monotonic
import zlib

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from models import SalesImportBatch, SettlementEntry, SettlementEntryAllocation, User

VERSION = "profit_loss_evidence_v1"
POLICY = "order_created_fx_mxn8_net_impairment_v4"
PERIODS = {"current": "本月", "previous": "上月", "ytd": "本年累计"}
SOURCES = {"sale": "销售记录（分摊后）", "settlement": "独立结算行", "fifo": "已保存FIFO成本",
           "fulfillment": "去重订单", "provision": "存货准备回放", "oa": "OA汇总",
           "manual": "人工填写", "ledger": "费用台账", "empty": "未填写"}
STATUS = {"complete": "依据金额一致", "unknown": "金额待核对", "partial": "依据不完整",
          "empty": "未填写", "reserved": "暂未接入", "legacy": "该版本未保存依据"}
SOURCE_SCOPE_WARNING = "部分原始结算来源归属与本报表不一致或缺失，已隐藏相关明细；金额沿用原核算口径，请联系管理员核对关联。"
NOTES = {
    "legacy": "该版本未保存完整计算依据；仅保留当时金额及已有来源说明，不使用当前数据补算。",
    "manual": "人工空白在合计中按0参与；显式填写0为有效覆盖。修改信息为整张月报最后修改信息，不代表单字段修改历史。",
    "monthly": "本年累计逐月取生效金额；各月使用各自报表汇率，不使用导入时汇率。",
    "oa": "OA当前提供汇总金额；此处不是逐笔审批清单。人工值或台账生效时不重复计入OA默认值。",
    "fifo": "以已保存出库数量×FIFO单价为基准，按原采购/头尾程/其他成本权重分摊，最后非零项承接尾差，再加已启用的成本补差。",
    "provision": "按实际持有期间回放，销售当日先计提后转销；按期间有效归属分摊。转销可含以前期间准备，不等同销售页面的当月参考跌价。",
    "source": "来源金额为本字段的参与金额；原始结算整行仅供定位，不跨拆分销售重复累加。不同期间、字段的明细不可混合求和。",
    "rounding": "以未舍入金额计算后显示2位；明细保留精度，显示值之和可能存在分位尾差。",
    "snapshot": "依据对应本次查询的已保存数据；未保存的输入不参与。重新查询可取得最新结果。",
}
# 仅声明已有公式的依赖，用于解释和跳转；金额始终取原报表计算结果。
FORMULAS = {
    "operating_revenue": [("product_sales_revenue", "+"), ("shipping_revenue", "+"), ("compensation_revenue", "+"), ("other_revenue", "+")],
    "operating_cost": [(k, "+") for k in ("product_purchase_cost", "head_logistics_cost", "inventory_other_cost", "customs_import_tax", "local_logistics_cost", "fulfillment_cost", "other_direct_cost")],
    "gross_profit": [("operating_revenue", "+"), ("operating_cost", "−")],
    "gross_margin": [("gross_profit", "+"), ("operating_revenue", "÷")],
    "selling_expenses": [(k, "+") for k in ("salary", "advertising", "sample_expenses", "platform_subscription", "warehousing", "delivery", "platform_fines")],
    "admin_expenses": [("rent_utilities", "+"), ("shared_admin", "+")],
    "asset_impairment_loss": [("inventory_impairment", "+"), ("inventory_writeoff", "−")],
    "operating_profit": [("gross_profit", "+"), *[(k, "−") for k in ("tax_surcharge", "selling_expenses", "admin_expenses", "research_development", "finance_expenses", "asset_impairment_loss", "credit_impairment_loss")]],
    "net_profit": [("operating_profit", "+")],
}


def serial(value):
    if isinstance(value, (Decimal, date, datetime)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    if isinstance(value, dict):
        return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    return value


def source_row(day, amount, source, reference="", *, scope_status=None, **details):
    row = {"day": day, "amount": amount, "source": source, "reference": str(reference), "details": details}
    if scope_status is not None:
        row["scope_status"] = scope_status
    return serial(row)


def add_source(target, key, periods, day, amount, source, reference="", **details):
    row = source_row(day, amount, source, reference, **details)
    row["periods"] = [p for p, (a, b) in periods.items() if a <= day < b]
    if row["periods"]:
        target.setdefault(key, []).append(row)


async def sale_source_references(db, sales, events, *, store_id):
    """只取来源定位字段；不重新分配原币金额、不读取整份raw_data。"""
    ids, event_ids = [s.id for s in sales], [e.entry_id for e in events]
    links = (await db.execute(select(SettlementEntryAllocation.sale_id, SettlementEntryAllocation.settlement_entry_id)
        .where(SettlementEntryAllocation.sale_id.in_(ids)))).all() if ids else []
    entry_ids = {entry_id for _, entry_id in links} | set(event_ids)
    # 与独立结算/对账导出一致：历史归属按结算行 → 导入批次 → 导入人回退。
    # 关联到本店销售不代表拥有原始整行的读取权限，管理员单店报表也按该店收集依据。
    owner = func.coalesce(SettlementEntry.store_id, SalesImportBatch.store_id, User.store_id)
    rows = (await db.execute(select(SettlementEntry.id, SettlementEntry.sale_id, SettlementEntry.source_file,
        SettlementEntry.source_sheet, SettlementEntry.source_row_number, SettlementEntry.import_batch_id,
        SettlementEntry.order_id, SettlementEntry.platform_sku_id, SettlementEntry.order_created_at,
        SettlementEntry.settlement_date, SettlementEntry.quantity, SettlementEntry.currency,
        SettlementEntry.net_product_sales, SettlementEntry.settlement_total, owner.label("source_store_id"))
        .outerjoin(SalesImportBatch, SalesImportBatch.id == SettlementEntry.import_batch_id)
        .outerjoin(User, User.id == SalesImportBatch.user_id).where(or_(
            SettlementEntry.id.in_(entry_ids), SettlementEntry.sale_id.in_(ids))).order_by(SettlementEntry.id))).all() if entry_ids or ids else []
    by_entry = {r.id: {"结算行ID": r.id, "导入批次": r.import_batch_id, "文件": r.source_file,
        "工作表": r.source_sheet, "原文件行号": r.source_row_number, "原订单号": r.order_id, "平台SKU": r.platform_sku_id,
        "订单创建日期": r.order_created_at, "结算日期": r.settlement_date, "原始数量（非拆分库存件数）": r.quantity,
        "币种": r.currency, "原始整行净商品收入（仅核对勿跨组件累加）": r.net_product_sales,
        "原始整行结算金额（仅核对勿跨组件累加）": r.settlement_total} for r in rows if r.source_store_id == store_id}
    by_sale, restricted = defaultdict(list), set()
    for sale_id, entry_id in sorted(set(links)):
        if entry_id in by_entry:
            by_sale[sale_id].append(by_entry[entry_id])
        else:
            restricted.add(sale_id)
    allocated = {sale_id for sale_id, _ in links}
    for row in rows:
        if row.sale_id in ids and row.sale_id not in allocated:
            if row.id in by_entry:
                by_sale[row.sale_id].append(by_entry[row.id])
            else:
                restricted.add(row.sale_id)
    return dict(by_sale), by_entry, restricted


def manual_sources(reports, report_month, keys, defaults, selections=(), expenses=()):
    """与月事实同批读取的来源；仅force_monthly报表调用，旧all模式不改变。"""
    saved = {r.report_month: r for r in reports}
    modes = {(r.report_month, r.category): r.mode for r in selections}
    expense_map = defaultdict(list)
    for row in expenses:
        if row.settlement_entry_id is None:
            expense_map[(row.expense_date.replace(day=1), row.category)].append(row)
    previous = (report_month - timedelta(days=1)).replace(day=1)
    months = sorted({previous, *[date(report_month.year, m, 1) for m in range(1, report_month.month + 1)]})
    result = {}
    for key in sorted(keys):
        result[key] = []
        for month in months:
            periods = (["current"] if month == report_month else []) + (["previous"] if month == previous else []) + (["ytd"] if month.year == report_month.year else [])
            report = saved.get(month)
            value = ((report.manual_values or {}).get(key, {}).get("current") if report else None)
            default = (defaults or {}).get(key, {}).get(month.isoformat())
            rows = []
            if modes.get((month, key)) == "ledger":
                for expense in sorted(expense_map[(month, key)], key=lambda r: r.id):
                    if expense.status in ("posted", "draft"):
                        rows.append(source_row(expense.expense_date,
                            expense.amount_cny if expense.status == "posted" else None, "ledger", expense.source_ref,
                            凭证ID=expense.id, 状态=expense.status, 原币金额=expense.amount,
                            币种=expense.currency, 凭证汇率=expense.exchange_rate, 备注=expense.note,
                            OA参考值=default, 采用规则="本月选用费用台账；关联平台结算的凭证不重复计入"))
                if not rows:
                    rows = [source_row(month, Decimal(0), "ledger", 采用规则="本月选用费用台账，无参与计算的凭证")]
            elif value not in (None, ""):
                rows = [source_row(month, Decimal(str(value)), "manual", getattr(report, "id", ""),
                    月报最后修改人ID=getattr(report, "updated_by_user_id", None), 月报最后修改时间=getattr(report, "updated_at", None),
                    备注=(getattr(report, "remarks", None) or {}).get(key, ""), OA参考值=default, 采用规则=NOTES["manual"])]
            elif default is not None:
                rows = [source_row(month, default, "oa", 采用规则=NOTES["oa"])]
            else:
                rows = [source_row(month, None, "empty", 采用规则="本月未填写，合计时按0参与")]
            result[key].extend({**row, "periods": periods} for row in rows)
    return result


def build_evidence(values, auto, manual, definitions):
    context = auto.get("_evidence_context")
    if not context or values.get("_calculation_policy") != POLICY:
        return None
    # 来源行已经序列化；仅替换月度费用列表，不复制/改写原自动事实。
    sources = dict(auto.get("_source_rows", {}))
    for key, rows in manual.get("_source_rows", {}).items():
        # OA分项从同次取数保留，仅用于该月实际采用OA的情况。
        oa_rows = sources.get(key, [])
        merged = []
        for row in rows:
            parts = [r for r in oa_rows if r["day"][:7] == row["day"][:7]] if row["source"] == "oa" else []
            merged.extend(parts or [row])
        sources[key] = merged
    sources = {key: sorted(rows, key=lambda r: (r["day"], r["source"], r["reference"],
        json.dumps(r["details"], ensure_ascii=False, sort_keys=True))) for key, rows in sources.items()}
    labels = {key: label.strip() for key, label, _, _ in definitions}
    fields = {}
    for key, label, kind, explanation in definitions:
        terms = FORMULAS.get(key, [])
        formula = " ".join((operator + " " if i else "") + labels[name] for i, (name, operator) in enumerate(terms))
        if not formula:
            formula = {"fulfillment_cost": "按月：去重订单数 × 8 MXN × 当月报表汇率"}.get(key, "参与明细金额合计")
        note = explanation or (NOTES["manual"] if kind == "manual" else "按表内项目计算")
        if key in ("product_purchase_cost", "head_logistics_cost", "inventory_other_cost"):
            note += "；" + NOTES["fifo"]
        if key in ("inventory_impairment", "inventory_writeoff", "asset_impairment_loss"):
            note += "；" + NOTES["provision"]
        if key == "gross_margin":
            note += "；营业收入为0时无有效比例，不填0。"
        cells = {}
        for period in PERIODS:
            value = values[key][period]
            selected = [r for r in sources.get(key, []) if period in r["periods"]]
            inputs = [{"key": dep, "label": labels[dep], "operator": op, "value": serial(values[dep][period]),
                       "blank_as_zero": values[dep][period] is None and any(k == dep and t == "manual" for k, _, t, _ in definitions)
                           and dep not in values.get("_unknown", {}).get(period, [])} for dep, op in terms]
            amounts = [Decimal(r["amount"]) if r["amount"] is not None else (Decimal(0) if r["source"] == "empty" else None) for r in selected]
            total = sum(amounts, Decimal(0)) if all(a is not None for a in amounts) else None
            status = "unknown" if value is None else "complete"
            if kind != "formula" and value is not None and (total is None or abs(value - total) > Decimal("0.00000001")
                    or any(r.get("scope_status") == "restricted" for r in selected)):
                status = "partial"
            if kind == "manual" and value is None and all(r["source"] == "empty" for r in selected):
                status = "empty"
            if key in ("non_operating_income", "non_operating_expense", "income_tax_expense"):
                status = "reserved"
            cells[period] = {"value": serial(value), "inputs": inputs, "source_count": len(selected),
                             "source_total": serial(total) if kind != "formula" else None, "status": status}
        fields[key] = {"label": label.strip(), "kind": kind, "formula": formula, "note": note, "cells": cells}
    result = serial({"schema_version": VERSION, "calculation_policy": values["_calculation_policy"],
        **context, "fields": fields, "exchange_rates": values.get("_exchange_rates", []),
        "warnings": values.get("_warnings", []), "notes": NOTES, "source_labels": SOURCES, "status_labels": STATUS})
    result["sources"] = sources
    result["calculation_id"] = sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
    return result


class EvidenceCache:
    """短期只读查询快照；按用户隔离、容量/时间有界，不作为历史凭据存储。"""
    def __init__(self, max_bytes=64 * 1024 * 1024, ttl=1800):
        self.items = OrderedDict()
        self.max_bytes, self.ttl = max_bytes, ttl

    def put(self, evidence, user_id):
        now = monotonic()
        for key, (expires, *_rest) in list(self.items.items()):
            if expires <= now:
                del self.items[key]
        packed = zlib.compress(json.dumps(evidence, ensure_ascii=False).encode())
        if len(packed) > self.max_bytes:
            return None
        while self.items and (len(self.items) >= 32 or sum(v[2] for v in self.items.values()) + len(packed) > self.max_bytes):
            self.items.popitem(last=False)
        token = token_urlsafe(24)
        self.items[token] = (now + self.ttl, user_id, len(packed), packed)
        return token

    def get(self, token, user_id):
        item = self.items.get(token)
        if item is None or item[0] <= monotonic():
            self.items.pop(token, None)
            raise HTTPException(410, detail={"code": "report_evidence_expired", "message": "查询依据已过期或服务已更新，请重新查询报表。"})
        if item[1] != user_id:
            raise HTTPException(403, detail="无权查看该查询依据")
        return json.loads(zlib.decompress(item[3]))


CACHE = EvidenceCache()


def publish_evidence(report, user):
    if getattr(report, "evidence", None):
        token = CACHE.put(report.evidence, user.id)
        report.evidence = {k: v for k, v in report.evidence.items() if k != "sources"}
        report.evidence["token"] = token
    return report


def evidence_page(evidence, key, period, page, page_size):
    if key not in evidence["fields"] or period not in PERIODS:
        raise HTTPException(404, detail="报表字段或期间不存在")
    rows = [r for r in evidence["sources"].get(key, []) if period in r["periods"]]
    return {"calculation_id": evidence["calculation_id"], "field": key, "period": period,
            "total": len(rows), "page": page, "items": rows[(page - 1) * page_size:page * page_size]}
