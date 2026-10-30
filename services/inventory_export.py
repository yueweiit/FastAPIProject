"""库存工作台Excel：只格式化查询结果，不另行计算库存或读取数据库。"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from services.inventory_impairment import PRODUCT_TYPE_NAMES
from services.product_import import _set_text, _style_header

MAX_EXPORT_PRODUCTS = 2000
EXPORT_BATCH_SIZE = 100
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STATUS_NAMES = {"normal": "正常", "low": "低于安全库存", "zero": "无库存", "pending": "待核对"}
WARNING_MESSAGES = {
    "inventory_assessment_quantity_mismatch": "逐批数量日结与准备回放不一致，相关成本及跌价待核对。",
    "inventory_assessment_voided_history": "存在本月历史持有的已作废批次，本月计提总额待核对。",
    "inventory_history_inconsistent": "批次数量、销售明细或更正记录不一致，相关日结待核对。",
    "inventory_arrival_restatement": "存在入库日期更正，按当前入库日期重算。",
    "inventory_scope_restricted": "入库按可见批次，出库仅含绑定店铺；全量期初、调整和期末不展示。",
    "inventory_current_documents": "历史按当前有效单据及已有日志重算，不能恢复撤销、更正前的完整流水。",
}


def _append(sheet, values, row):
    for col, value in enumerate(values, 1):
        cell = sheet.cell(row, col, value)
        if isinstance(value, str):
            _set_text(cell, value)


def _known_sum(values):
    values = list(values)
    return None if any(value is None for value in values) else sum(values, 0)


def _notes(product, scope):
    codes = list(product.get("warnings", []))
    if scope == "store_outbound":
        codes.append("inventory_scope_restricted")
    return "；".join(WARNING_MESSAGES.get(code, code) for code in dict.fromkeys(codes))


def _format(sheet, widths, *, frozen="C2", numeric=(), money=(), header_rows=1):
    _style_header(sheet)
    sheet.freeze_panes = frozen
    max_column = sheet.max_column
    for col, width in widths.items():
        sheet.column_dimensions[get_column_letter(col)].width = width
    for row in sheet.iter_rows(min_row=header_rows + 1):
        for col in numeric:
            row[col - 1].number_format = '#,##0'
        for col in money:
            row[col - 1].number_format = '¥#,##0.00'
        for cell in row:
            cell.alignment = Alignment(vertical="center", wrap_text=cell.column in (2, max_column))
    if header_rows == 1:
        sheet.auto_filter.ref = f"A1:{get_column_letter(sheet.max_column)}{sheet.max_row}"


def _total(sheet, rows, columns):
    values = [None] * sheet.max_column
    values[0] = "合计"
    for col in columns:
        values[col - 1] = _known_sum(row[col - 1] for row in rows)
    _append(sheet, values, len(rows) + 2)
    for cell in sheet[sheet.max_row]:
        cell.fill = PatternFill("solid", fgColor="F2F4F7")
        cell.font = Font(bold=True)


def build_inventory_workbook(view, products, *, scope, keyword=None, selected_sku=None, start=None, end=None):
    workbook = Workbook()
    workbook.remove(workbook.active)
    if view == "overview":
        sheet = workbook.create_sheet("库存总览")
        _append(sheet, ["SKU", "商品名称", "商品类型", "安全库存", "当前库存", "库存成本金额(元)", "最近入库", "库存状态"], 1)
        rows = [[p["sku"], p["name"], PRODUCT_TYPE_NAMES[p["product_type"]],
                 p["safe_stock_quantity"] if p["product_type"] == "stable" else None,
                 p["stock_quantity"], p["stock_value"], p["last_batch_at"], STATUS_NAMES[p["stock_status"]]] for p in products]
        for number, row in enumerate(rows, 2):
            _append(sheet, row, number)
        _format(sheet, {1: 24, 2: 36, 3: 20, 4: 16, 5: 18, 6: 24, 7: 24, 8: 22}, numeric=(4, 5), money=(6,))
        _total(sheet, rows, (5, 6))
        sheet.cell(sheet.max_row, 5).number_format = '#,##0'
        sheet.cell(sheet.max_row, 6).number_format = '¥#,##0.00'
    else:
        summary = workbook.create_sheet("月度汇总")
        _append(summary, ["SKU", "商品名称", "期初库存", "本月入库", "本月出库", "本月调整", "期末库存", "说明"], 1)
        rows = []
        for product in products:
            days = product["days"]
            row = [product["sku"], product["name"], days[0]["opening_quantity"],
                   _known_sum(day["inbound"] for day in days), _known_sum(day["outbound"] for day in days),
                   _known_sum(day["adjustment"] for day in days), days[-1]["closing_quantity"], _notes(product, scope)]
            rows.append(row)
            _append(summary, row, len(rows) + 1)
        _format(summary, {1: 24, 2: 36, 3: 18, 4: 18, 5: 18, 6: 18, 7: 18, 8: 60}, numeric=(3, 4, 5, 6, 7))
        _total(summary, rows, (3, 4, 5, 6, 7))
        for cell in summary[summary.max_row][2:7]:
            cell.number_format = '#,##0'
        if view == "daily":
            sheet = workbook.create_sheet("每日进出")
            _append(sheet, ["日期", "SKU", "商品名称", "期初库存", "入库", "出库", "调整", "期末库存", "说明"], 1)
            number = 2
            for product in products:
                for day in product["days"]:
                    _append(sheet, [day["date"], product["sku"], product["name"], day["opening_quantity"],
                                    day["inbound"], day["outbound"], day["adjustment"], day["closing_quantity"], _notes(product, scope)], number)
                    number += 1
            _format(sheet, {1: 16, 2: 24, 3: 36, 4: 18, 5: 16, 6: 16, 7: 16, 8: 18, 9: 60}, frozen="D2", numeric=(4, 5, 6, 7, 8))
            for row in sheet.iter_rows(min_row=2):
                row[0].number_format = 'yyyy-mm-dd'
                row[2].alignment = Alignment(vertical="center", wrap_text=True)
        else:
            sheet = workbook.create_sheet("每日横向表")
            days = products[0]["days"]
            _set_text(sheet.cell(1, 1), "SKU")
            _set_text(sheet.cell(1, 2), "商品名称")
            sheet.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
            sheet.merge_cells(start_row=1, start_column=2, end_row=2, end_column=2)
            for index, day in enumerate(days):
                column = 3 + index * 4
                sheet.cell(1, column, day["date"])
                sheet.cell(1, column).number_format = 'yyyy-mm-dd'
                sheet.merge_cells(start_row=1, start_column=column, end_row=1, end_column=column + 3)
                for offset, label in enumerate(("入库", "出库", "调整", "期末")):
                    _set_text(sheet.cell(2, column + offset), label)
            note_col = 3 + len(days) * 4
            _set_text(sheet.cell(1, note_col), "说明")
            sheet.merge_cells(start_row=1, start_column=note_col, end_row=2, end_column=note_col)
            for number, product in enumerate(products, 3):
                values = [product["sku"], product["name"]]
                for day in product["days"]:
                    values.extend(day[key] for key in ("inbound", "outbound", "adjustment", "closing_quantity"))
                _append(sheet, [*values, _notes(product, scope)], number)
            _format(sheet, {1: 24, 2: 36, note_col: 60}, frozen="C3", numeric=range(3, note_col), header_rows=2)
            for row in sheet.iter_rows(min_row=1, max_row=2):
                for cell in row:
                    cell.fill = PatternFill("solid", fgColor="5B9BD5")
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.alignment = Alignment(horizontal="center", vertical="center")
            for col in range(3, note_col):
                sheet.column_dimensions[get_column_letter(col)].width = 14
    notes = workbook.create_sheet("说明")
    notes_rows = [
        ["项目", "内容"], ["导出范围", f"当前查询全部结果，共{len(products)}个商品，不限当前页。"],
        ["查询条件", (f"{selected_sku}；{keyword}" if keyword else selected_sku) if selected_sku else (keyword or "全部商品")],
        ["数据范围", "共享库存记录余额" if scope == "shared" else "全量库存日结" if scope == "global" else WARNING_MESSAGES["inventory_scope_restricted"]],
        ["报告期间", f"{start.isoformat()} 至 {end.isoformat()}" if start is not None else "当前有效批次记录余额"],
        ["空白单元格", "表示无法确认或权限受限，不表示0；相关合计也保留空白。"],
        ["金额口径", "库存成本包含现有成本补差，不扣减跌价准备；财务账面价值见报表。"],
        ["历史口径", WARNING_MESSAGES["inventory_current_documents"]],
    ]
    for number, values in enumerate(notes_rows, 1):
        _append(notes, values, number)
    _format(notes, {1: 24, 2: 110}, frozen="A2")
    for row in notes.iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    result = BytesIO()
    workbook.save(result)
    result.seek(0)
    return result


def build_assessment_workbook(products, *, as_of, scope, keyword=None):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "库存盘点与跌价"
    headers = ["SKU", "商品名称", "本月入库", "本月出库" if scope == "global" else "本店本月出库",
               "截止日账面库存", "平均单件成本(元)", "库存成本金额(元)", "期末跌价余额(元)",
               "本月计提总额(元)", "账面价值(元)", "盘点时账面数", "实盘数量", "盘点差异(实盘-账面)",
               "盘点日期", "保存时间(UTC)", "盘点状态", "备注/警示"]
    _append(sheet, headers, 1)
    rows = []
    for product in products:
        count = product["count"] or {}
        status = "账面已变化，需复核" if product["count_basis_changed"] else "已盘点" if count else "未盘点"
        row = [product["sku"], product["name"], product["month_inbound"], product["month_outbound"],
               product["system_quantity"], product["average_unit_cost"], product["inventory_amount"],
               product["impairment_amount"], product["monthly_total_impairment_amount"], product["book_value"],
               count.get("system_quantity"), count.get("actual_quantity"), count.get("difference"),
               count.get("count_date"), count.get("created_at"), status,
               "；".join(filter(None, [count.get("note"), _notes(product, scope)]))]
        rows.append(row)
        _append(sheet, row, len(rows) + 1)
    _format(sheet, {1:24,2:36,**{col:23 for col in range(3,17)},17:70}, numeric=(3,4,5,11,12,13), money=range(6,11))
    _total(sheet, rows, (3,4,5,7,8,9,10,11,12,13))
    for column in (3,4,5,11,12,13):
        sheet.cell(sheet.max_row,column).number_format = '#,##0'
    for column in (7,8,9,10):
        sheet.cell(sheet.max_row,column).number_format = '¥#,##0.00'
    batches = workbook.create_sheet("批次明细")
    _append(batches, ["SKU","商品名称","批次号","入库日期","入库天数","剩余数量","单件成本(元)",
                      "库存成本金额(元)","期末跌价余额(元)","本月计提总额(元)","跌价归属店铺","说明"],1)
    owners = workbook.create_sheet("店铺跌价分摊")
    _append(owners,["SKU","批次号","归属店铺","期末跌价金额(元)"],1)
    batch_number = owner_number = 2
    for product in products:
        for batch in product["batches"]:
            _append(batches,[product["sku"],product["name"],batch["batch_no"],batch["arrived_at"],batch["age_days"],
                batch["quantity"],batch["unit_cost"],batch["inventory_amount"],batch["impairment_amount"],
                batch["monthly_total_impairment_amount"]," / ".join(batch["store_names"]) or "未关联店铺",batch["note"]],batch_number)
            batch_number += 1
            for owner in batch["store_allocations"]:
                _append(owners,[product["sku"],batch["batch_no"],owner["store_name"],owner["impairment_amount"]],owner_number)
                owner_number += 1
    _format(batches,{1:24,2:36,3:30,4:18,5:16,6:18,7:22,8:24,9:24,10:24,11:36,12:70},numeric=(5,6),money=(7,8,9,10))
    _format(owners,{1:24,2:30,3:36,4:28},money=(4,))
    notes=workbook.create_sheet("说明")
    for index,pair in enumerate([
        ["截止日",as_of], ["查询",keyword or "全部商品"], ["范围","全部匹配商品，不限当前页；" + ("全量出库/分摊" if scope == "global" else "出库和店铺分摊仅绑定店铺；共享库存仅今日")],
        ["盘点", "实盘减保存时账面数；0是有效盘点，空白是未盘点。旧盘点差异不随后续销售重算。保存不修改库存、FIFO、成本或跌价。"],
        ["日期", "盘点对照以所选自然日日末为界，保存时间为UTC。后续补单、更正或撤销会提示账面已变化，须重新核对。"],
        ["金额", "跌价按既有持有期间规则逐批计算后汇总；本月计提包含已售部分，账面价值=库存成本-期末跌价余额；实盘数不参与金额计算。"],
        ["待核对", "未知/异常金额及不完整合计留空；明细备注保留规则历史推定和异常提示。"],
    ],1): _append(notes,pair,index)
    _format(notes,{1:24,2:110})
    output=BytesIO();workbook.save(output);output.seek(0)
    return output
