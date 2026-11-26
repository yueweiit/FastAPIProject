"""09/10离线核对附件，直接消费同次计算/固定版本中的依据。"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json

from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from services.product_specification import specification_export_text
from services.profit_loss_evidence import NOTES, PERIODS, SOURCES, STATUS, VERSION

GUIDE, CALC, DETAIL = "报表口径说明", "金额计算依据", "来源明细"
MONEY = '¥#,##0.00;[Red]-¥#,##0.00'


def append(sheet, values):
    number = sheet.max_row + 1 if sheet.cell(1, 1).value is not None else 1
    for column, value in enumerate(values, 1):
        cell = sheet.cell(number, column)
        if isinstance(value, str):
            cell.value = specification_export_text(value)
            cell.data_type = "s"
        else:
            cell.value = value
    return number


def link(cell, sheet, row, label=None, *, missing=False):
    if label is None and (missing or cell.value is None):
        # 公式文本非空不代表结果已知；空缓存加hyperlink会被data_only读回为链接文本。
        # 调用方可传实际结果是否缺失，只添加批注，保留公式及原缓存。
        cell.hyperlink = None
        cell.comment = Comment(f"金额未提供/待核对。请在“{sheet}”第{row}行查看依据。", "报表依据")
        return
    if label is not None:
        cell.value = label
        cell.data_type = "s"
    cell.hyperlink = f"#'{sheet.replace(chr(39), chr(39) * 2)}'!A{row}"
    cell.font = Font(name="等线", size=10, color="0563C1", underline="single")


def amount(value):
    return Decimal(str(value)) if value is not None else None


def pretty(value):
    if value is None:
        return "待核对/未提供"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
    return str(value)


def append_profit_loss_evidence(workbook, store_reports, selected_store_id, *, row_definitions=None, summary_rows=None, summary_store_ids=None):
    """原报表不改金额和顺序；新依据表统一追加在最后。"""
    guide, calc, detail = (workbook.create_sheet(name) for name in (GUIDE, CALC, DETAIL))
    append(guide, ["项目", "说明"])
    append(guide, ["导出时间（UTC）", datetime.now(timezone.utc).isoformat(timespec="seconds")])
    append(guide, ["阅读顺序", "主报表金额 → 金额计算依据 → 来源明细；表内链接支持离线查看，源记录不依赖在线链接。"])
    for note in NOTES.values():
        append(guide, ["口径说明", note])
    append(guide, ["10表", "仅汇总当前有效店铺。本月人工空白在10表按0参与；未知自动金额/待确认费用不填0。毛利率=合计毛利÷合计营业收入；其他占比=该项合计÷合计营业收入。"])
    append(calc, ["核对编号", "店铺", "核算月份", "字段", "期间", "报表金额", "公式", "代入过程/来源合计", "核对状态", "字段口径", "查看明细", "返回报表"])
    append(detail, ["核对编号", "店铺", "字段", "期间", "归属日期", "来源类型", "凭据编号", "参与金额（保留精度）", "详细依据", "返回计算依据"])
    positions, deferred_links = {}, []
    definitions = row_definitions or []
    for store, values in store_reports:
        evidence = values.get("_evidence")
        valid = isinstance(evidence, dict) and evidence.get("schema_version") == VERSION and isinstance(evidence.get("sources"), dict)
        if valid:
            definitions_for_store = [(key, f["label"], f["kind"], f["note"]) for key, f in evidence["fields"].items()]
        else:
            definitions_for_store = definitions
        append(guide, [store.name, f"店铺ID：{store.id}；口径：{values.get('_calculation_policy') or '历史未记录'}；计算编号：{evidence.get('calculation_id', '') if valid else NOTES['legacy']}"])
        for warning in values.get("_warnings", []):
            append(guide, [f"{store.name} · 待核对", warning])
        for rate in values.get("_exchange_rates", []):
            from services.report_exchange_rates import rate_evidence_text
            append(guide, [f"{store.name} · 月度汇率", rate_evidence_text(rate)])
        for key, label, kind, note in definitions_for_store:
            if key not in values:
                continue
            field = evidence["fields"].get(key) if valid else None
            append(guide, [f"{store.name} · {label.strip()}", field["note"] if field else (note or NOTES["legacy"])])
            for period, period_label in PERIODS.items():
                cell = field["cells"][period] if field else None
                ident = f"S{store.id}:{key}:{period}"
                rows = [r for r in evidence["sources"].get(key, []) if period in r["periods"]] if valid else []
                inputs = cell.get("inputs", []) if cell else []
                expression = " ".join((v["operator"] + " " if i else "") + f"{v['label']}({pretty(v['value']) if not v.get('blank_as_zero') else '未填写按0'})" for i, v in enumerate(inputs))
                if not inputs and cell:
                    expression = f"参与记录 {cell['source_count']} 条；未舍入合计 {pretty(cell['source_total'])}"
                index = append(calc, [ident, store.name, evidence.get("report_month") if valid else "历史版本",
                    label.strip(), period_label, values[key].get(period), field["formula"] if field else NOTES["legacy"],
                    expression, STATUS.get(cell["status"]) if cell else STATUS["legacy"], field["note"] if field else (note or NOTES["legacy"]), "", ""])
                positions[(store.id, key, period)] = index
                calc.cell(index, 6).number_format = "0.00%" if key == "gross_margin" else MONEY
                first = detail.max_row + 1
                for row in rows:
                    source_index = append(detail, [ident, store.name, label.strip(), period_label, row["day"],
                        SOURCES.get(row["source"], row["source"]), row["reference"], amount(row["amount"]),
                        "\n".join(f"{k}：{pretty(v)}" for k, v in row["details"].items()), ""])
                    link(detail.cell(source_index, 10), CALC, index, "返回计算依据")
                for term in inputs:
                    source_index = append(detail, [ident, store.name, label.strip(), period_label, "", "报表计算项",
                        term["label"], amount(term["value"]), "未填写在合计中按0参与" if term.get("blank_as_zero") else term["operator"], ""])
                    deferred_links.append((detail.cell(source_index, 7), (store.id, term["key"], period)))
                    link(detail.cell(source_index, 10), CALC, index, "返回计算依据")
                if rows or inputs:
                    link(calc.cell(index, 11), DETAIL, first, "查看明细")
    for cell, target in deferred_links:
        if target in positions:
            link(cell, CALC, positions[target])
    # 09保留历史行定义，不对旧版本补行、套用新公式或现查业务库。
    single = workbook["09-店铺损益表"] if "09-店铺损益表" in workbook.sheetnames else None
    if single:
        selected = next((values for s, values in store_reports if s.id == selected_store_id), {})
        # JSON对象键顺序不能定位Excel行；与主表复用实际保存/传入的行定义。
        defs = [r[0] for r in definitions if r[0] in selected]
        for number, key in enumerate(defs, 2):
            for column, period in enumerate(PERIODS, 2):
                target = positions.get((selected_store_id, key, period))
                if target:
                    link(single.cell(number, column), CALC, target)
                    if period == "current":
                        link(single.cell(number, 1), CALC, target)
                    link(calc.cell(target, 12), single.title, number, "返回09报表")
                    if single.cell(number, column).value is not None:
                        single.cell(number, column).comment = Comment("点击金额查看字段口径、计算过程及来源明细。", "报表依据")
    if summary_rows is not None and "10-各店铺损益汇总表" in workbook.sheetnames:
        # 09可选择停用店铺，10仅含有效店铺；不能以09优先顺序错配10的列。
        by_store = {s.id: (s, v) for s, v in store_reports}
        summary_reports = [by_store[i] for i in summary_store_ids] if summary_store_ids is not None else store_reports
        summary = workbook["10-各店铺损益汇总表"]
        summary_by_key = {row["key"]: row for row in summary_rows}
        for number, row in enumerate(summary_rows, 2):
            key = row["key"]
            for column, (store, _values) in enumerate(summary_reports, 2):
                target = positions.get((store.id, key, "current"))
                if target:
                    link(summary.cell(number, column), CALC, target)
                    if store.id != selected_store_id:
                        link(calc.cell(target, 12), summary.title, number, "返回10报表")
            for name, value, column in (("合计", row["total"], len(summary_reports) + 2), ("占比", row["ratio"], len(summary_reports) + 3)):
                rule = ("合计毛利 ÷ 合计营业收入" if key == "gross_margin" else "各店本月金额相加") if name == "合计" else ("毛利率不再计算占比" if key == "gross_margin" else "该项合计 ÷ 合计营业收入")
                keys = (() if key == "gross_margin" and name == "占比" else
                        ("gross_profit", "operating_revenue") if key == "gross_margin" else
                        (key, "operating_revenue") if name == "占比" else (key,))
                if len(keys) == 2:
                    # 使用页面同一份汇总输入，不重算比例，也不把各店毛利率当成分子。
                    expression = " ÷ ".join(f"{summary_by_key[dep]['label'].strip()}({pretty(summary_by_key[dep]['total'])})" for dep in keys)
                    percentage = f"{(value * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%" if value is not None else pretty(None)
                    expression += " = " + percentage
                elif keys:
                    expression = "；".join(f"{s.name}：{pretty(v)}" for (s, _), v in zip(summary_reports, row["values"]))
                else:
                    expression = rule
                idx = append(calc, [f"ALL:{key}:{name}", "各店铺汇总", "", row["label"].strip(), name, value,
                    rule, expression,
                    "金额待核对/无有效比例" if value is None else "已核对", rule, "", ""])
                calc.cell(idx, 6).number_format = "0.00%" if key == "gross_margin" or name == "占比" else MONEY
                first = detail.max_row + 1
                # 保留各店分项入口；比例另列分子分母，可离线继续追溯。
                for dep in keys:
                    for store, values in summary_reports:
                        target = positions.get((store.id, dep, "current"))
                        i = append(detail, [f"ALL:{key}:{name}", store.name, dep, "本月", "", "各店报表项目", dep, values[dep]["current"], "合计按各店报表金额；人工空白按0，未知保留空白", ""])
                        if target:
                            link(detail.cell(i, 7), CALC, target)
                        link(detail.cell(i, 10), CALC, idx, "返回计算依据")
                if detail.max_row >= first:
                    link(calc.cell(idx, 11), DETAIL, first, "查看各店依据")
                link(calc.cell(idx, 12), summary.title, number, "返回10报表")
                link(summary.cell(number, column), CALC, idx)
    for sheet in (guide, calc, detail):
        sheet.freeze_panes = "C2" if sheet != guide else "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.font = Font(name="等线", bold=True, color="FFFFFF")
        for row in sheet:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for column in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(column)].width = 24
    guide.column_dimensions["B"].width = 110
    for column in ("G", "H", "J"):
        calc.column_dimensions[column].width = 60
    detail.column_dimensions["I"].width = 100
    for row in detail.iter_rows(min_row=2):
        row[7].number_format = '0.00########'
