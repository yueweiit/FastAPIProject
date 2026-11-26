"""07/12离线依据，保留主表位置、金额、公式缓存；旧版本不补算。"""
from openpyxl.styles import Alignment, Font, PatternFill
from services.profit_loss_evidence import NOTES, STATUS
from services.profit_loss_evidence_export import append, link, amount, pretty
from services.report_row_evidence import VERSION, total_evidence, expression_text
from services.product_specification import specification_export_text

GUIDE, CALC, DETAIL = "07-12字段口径", "07-12计算过程", "07-12来源明细"
INVENTORY_COLUMNS = {5: "age_days", 6: "unit_cost", 7: "original_quantity", 8: "original_inventory_amount",
    9: "sold_quantity", 10: "sold_cost_amount", 11: "quantity", 12: "provision_quantity", 13: "inventory_amount",
    14: "impairment_rate", 15: "impairment_amount", 16: "book_value", 17: "book_value_difference",
    18: "monthly_impairment_amount", 21: "opening_impairment_amount", 22: "monthly_total_impairment_amount",
    23: "monthly_writeoff_amount", 24: "monthly_transfer_in_amount", 25: "monthly_transfer_out_amount"}
PRODUCT_COLUMNS = {2: "current_quantity", 3: "current_revenue", 4: "current_cost", 5: "current_gross",
    6: "current_margin", 7: "previous_margin", 8: "margin_change", 9: "ytd_revenue", 10: "ytd_gross", 11: "ytd_margin"}


def source_text(value):
    def safe(item):
        if isinstance(item, str):
            return specification_export_text(item)
        if isinstance(item, dict):
            return {safe(k): safe(v) for k, v in item.items()}
        if isinstance(item, list):
            return [safe(v) for v in item]
        return item
    return pretty(safe(value))


def append_row_evidence(workbook, *, inventory_rows=None, summary_rows=None, product_rows=None, formula_cache=None):
    from routers.reports import INVENTORY_IMPAIRMENT_SHEET, INVENTORY_IMPAIRMENT_SUMMARY_SHEET, PRODUCT_LINE_PROFIT_SHEET, INVENTORY_IMPAIRMENT_SUMMARY_COLUMNS
    guide, calc, detail = [workbook.create_sheet(name) for name in (GUIDE, CALC, DETAIL)]
    append(guide, ["报表/字段", "口径说明"])
    append(guide, ["阅读顺序", "点击主表数字查看计算过程，再查看来源明细。空白金额的依据位置在单元格批注中。未舍入参与值按字段分别核对，不能跨字段/期间重复相加。"])
    append(guide, ["旧版本", NOTES["legacy"]])
    append(guide, ["共享库存与店铺权限", "独立07按既有共享库存范围展示匿名批次流水；单店附件按原归属份额；12只含本次授权店铺。"])
    append(guide, ["12与09", "12沿用准备转销抵减成本的毛利口径；09使用净跌价列示。12按本地商品分组，保留原表名。"])
    append(guide, ["显示精度", NOTES["rounding"]])
    append(calc, ["计算编号", "报表", "对象", "日期/月份", "字段", "报表值", "口径", "计算公式", "代入过程/来源合计", "核对状态", "来源/首项输入", "返回报表", "第二项输入", "第三项输入", "第四项输入", "第五项输入"])
    append(detail, ["计算编号", "报表", "对象", "字段", "日期", "来源类型", "凭据/批次", "参与值（保留精度）", "详细依据", "返回计算过程", "来源行依据"])
    groups = [(INVENTORY_IMPAIRMENT_SHEET, inventory_rows, INVENTORY_COLUMNS),
              (INVENTORY_IMPAIRMENT_SUMMARY_SHEET, summary_rows,
               {i: key for i, (key, _) in enumerate(INVENTORY_IMPAIRMENT_SUMMARY_COLUMNS, 1)}),
              (PRODUCT_LINE_PROFIT_SHEET, product_rows, PRODUCT_COLUMNS)]
    definitions, positions, pending, by_id, source_links = set(), {}, [], {}, []
    for sheet_name, rows, columns in groups:
        if rows is None:
            continue
        main = workbook[sheet_name] if sheet_name in workbook.sheetnames else None
        total = total_evidence(rows)
        entries = list(rows) + ([{"_evidence": total}] if total else [])
        for number, row in enumerate(entries, 2):
            evidence = row.get("_evidence")
            valid = isinstance(evidence, dict) and evidence.get("schema_version") == VERSION
            if not valid:
                title = row.get("batch_no") or row.get("name") or row.get("sku", "")
                target = append(calc, ["", sheet_name, title, "", "历史版本", None, NOTES["legacy"], "", "", STATUS["legacy"]])
                if main:
                    link(main.cell(number, 1), CALC, target)
                    for col in columns:
                        cell = main.cell(number, col)
                        # 旧版本没有字段依据，用同次主表生成的缓存判断，不读取实时业务或重算历史。
                        missing = cell.data_type == "f" and (formula_cache or {}).get(cell.coordinate) is None
                        link(cell, CALC, target, missing=missing)
                    link(calc.cell(target, 12), sheet_name, number, "返回原报表")
                continue
            for key, field in evidence["fields"].items():
                ident = f"{evidence['calculation_id']}:{key}"
                expression = field.get("expression") or expression_text(field)
                target = append(calc, [ident, sheet_name, evidence["title"], evidence["context"], field["label"], amount(field["value"]),
                    field["note"], field["formula"], expression, STATUS.get(field["status"], field["status"])])
                calc.cell(target, 6).number_format = "0.00%" if field["format"] == "percent" else "0.############" if field["format"] == "number" else '¥#,##0.00'
                positions[(sheet_name, evidence["row_key"], key)] = target
                by_id[ident] = target
                if (sheet_name, key) not in definitions:
                    append(guide, [f"{sheet_name} · {field['label']}", field["note"]])
                    definitions.add((sheet_name, key))
                first_detail = None
                for source in evidence["sources"].get(key, []):
                    # Excel单元格有32767字符限制，续行保留全部原文，只首行带参与值以免误加。
                    text = source_text(source["details"])
                    chunks = [text[i:i + 30000] for i in range(0, len(text), 30000)] or [""]
                    for chunk_index, chunk in enumerate(chunks):
                        detail_row = append(detail, [ident, sheet_name, evidence["title"], field["label"], source["day"],
                            source["source"] if chunk_index == 0 else "详细依据续行（不重复计数）", source["reference"],
                            amount(source["amount"]) if chunk_index == 0 else None, chunk])
                        detail.cell(detail_row, 8).number_format = "0.############"
                        link(detail.cell(detail_row, 10), CALC, target, "返回计算过程")
                        parent_id = source["details"].get("批次计算编号") or source["details"].get("计算编号")
                        if parent_id:
                            source_links.append((detail_row, f"{parent_id}:{key}"))
                        first_detail = first_detail or detail_row
                if first_detail:
                    link(calc.cell(target, 11), DETAIL, first_detail, f"{field['source_count']}条来源")
                elif field["inputs"]:
                    for index, operand in enumerate(field["inputs"]):
                        pending.append((target, 11 if index == 0 else 12 + index, sheet_name, evidence["row_key"], operand["key"]))
                if main:
                    for col, mapped in columns.items():
                        if mapped == key:
                            link(main.cell(number, col), CALC, target, missing=field["value"] is None)
                    if key == next(iter(evidence["fields"])):
                        link(main.cell(number, 1), CALC, target)
                    link(calc.cell(target, 12), sheet_name, number, "返回报表")
            if evidence.get("warning"):
                append(guide, [f"{evidence['title']} · 待核对/说明", evidence["warning"]])
    for target, column, sheet_name, row_key, key in pending:
        other = positions.get((sheet_name, row_key, key))
        if other:
            link(calc.cell(target, column), CALC, other, "查看计算输入")
    for target, ident in source_links:
        if ident in by_id:
            link(detail.cell(target, 11), CALC, by_id[ident], "查看来源行依据")
    for sheet in (guide, calc, detail):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.font = Font(name="等线", size=10, bold=True, color="FFFFFF")
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 36 if column[0].column < 7 else 65
        for row in sheet:
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
