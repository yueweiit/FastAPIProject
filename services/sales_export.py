"""销售台账Excel；消费列表已计算值，不另算FIFO、汇率或店铺归属。"""
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from services.product_import import _set_text, _style_header
from services.sales_fields import sales_field_help

MAX_EXPORT_ROWS = 20_000
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS = ("店铺", "日期", "SKU ID", "产品名", "实际出库数量", "销售单价（元）", "FIFO成本单价（元）",
           "店铺收入（元）", "订单总成本（元，本行）", "其他费用（元）", "毛利（元）", "订单号", "记录类型",
           "原币", "汇率", "汇率日期", "成本状态", "说明", "采购成本（元）", "头程成本（元）",
           "尾程成本（元）", "其他入库成本（元）", "出库商品跌价（元，参考）",
           "单件采购（元/件）", "单件头程（元/件）", "单件尾程（元/件）", "单件其他入库成本（元/件）", "交易类型", "来源数量",
           "来源净商品销售额（原币）", "来源结算总金额（原币）", "汇率来源")
RECORD_NAMES = {"sale": "销售", "other_expense": "赔付费用", "compensation_income": "赔付收入", "manual_settlement": "手工结算补录", "settlement_adjustment": "退款/结算调整"}
COST_NAMES = {"actual": "已核对", "unverified": "未核对", "needs_review": "待核对"}


def _append(sheet, values, row_number):
    for column, value in enumerate(values, 1):
        cell = sheet.cell(row_number, column, value)
        if isinstance(value, str):
            _set_text(cell, value)


def build_sales_workbook(rows, *, month, keyword, scope, product_id=None, order_summaries=()):
    resource = sales_field_help()
    impairment_help = next(field for field in resource["fields"] if field["key"] == "impairment_cny")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "销售记录"
    _append(sheet, HEADERS, 1)
    values = []
    for number, row in enumerate(rows, 2):
        notes = []
        if row["exchange_rate_to_cny"] is None:
            notes.append("汇率缺失，相关人民币金额待核对")
        if row.get("cost_review_reason"):
            notes.append(row["cost_review_reason"])
        for key in ("cost_breakdown_review_reason", "impairment_review_reason"):
            if row.get(key):
                notes.append(row[key])
        values.append([
            row.get("store_name") or "未关联店铺", row["sold_at"].date(), row["sku"], row["product_name"],
            row["quantity"], row["selling_price_cny"],
            row.get("unit_total_cost_cny"),
            row["sales_revenue_cny"], row.get("order_total_cost_cny") if row.get("order_total_cost_cny") is not None else row["total_cost"], row["platform_fee_cny"], row["profit_cny"],
            row.get("display_order_no") or row["order_no"], RECORD_NAMES.get(row["record_type"], row["record_type"]), row["currency"],
            row["exchange_rate_to_cny"], row["exchange_rate_date"],
            COST_NAMES.get(row["cost_status"], row["cost_status"]) if row["record_type"] == "sale" else "—",
            "；".join(notes),
            row.get("purchase_cost_cny"), row.get("head_shipping_cost_cny"),
            row.get("last_mile_cost_cny"), row.get("other_cost_cny"), row.get("impairment_cny"),
            *(row.get("unit_" + key + "_cost_cny") for key in ("purchase", "head_shipping", "last_mile", "other")),
            row.get("transaction_type"), row.get("source_quantity"),
            row.get("source_row_net_product_sales"), row.get("source_row_settlement_total"), row.get("exchange_rate_source"),
        ])
        _append(sheet, values[-1], number)
    totals = [None] * len(HEADERS)
    totals[0] = "合计"
    for col in (5, 8, 9, 10, 11, 19, 20, 21, 22, 23):
        parts = [row[col - 1] for row in values]
        totals[col - 1] = None if None in parts else sum(parts, Decimal(0))
    _append(sheet, totals, len(rows) + 2)
    _style_header(sheet)
    widths = (22, 14, 24, 36, 14, 20, 22, 23, 26, 20, 20, 30, 22, 12, 18, 14, 16, 70, 20, 20, 20, 22, 30, 23, 23, 23, 25, 18, 16, 30, 28, 22)
    for col, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(col)].width = width
    for cells in sheet.iter_rows(min_row=2):
        cells[1].number_format = cells[15].number_format = "yyyy-mm-dd"
        cells[4].number_format = '#,##0'
        cells[28].number_format = '#,##0'
        for col in (6, 8, 9, 10, 11, 19, 20, 21, 22, 23):
            cells[col - 1].number_format = '¥#,##0.00'
        cells[6].number_format = '¥#,##0.0000'
        for column in (24, 25, 26, 27):
            cells[column - 1].number_format = '¥#,##0.0000'
        cells[14].number_format = '0.00000000'
        for column in (30, 31):
            cells[column - 1].number_format = '#,##0.00##'
        for cell in cells:
            cell.alignment = Alignment(vertical="center", wrap_text=cell.column in (4, 18))
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="EEF3F8")
    sheet.freeze_panes = "E2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(rows) + 1}"
    notes = workbook.create_sheet("说明")
    for number, pair in enumerate((("范围", scope), ("月份", month or "全部月份"), ("商品名称/SKU/订单号", keyword or "全部"),
                 ("商品ID", product_id), ("记录数", len(rows)),
                 ("口径", "导出全部匹配记录，不限当前页；包含台账中的独立退款、调整和赔付事件，事件数量为0。"),
                 ("金额", "收入、费用、成本及毛利为人民币，沿用保存汇率及当前成本追溯结果。来源净商品销售额、来源结算总金额为原币，币种见原币列，汇率表示1原币兑人民币；不修改库存、销售或原始成本。"),
                 ("原币核对", "来源金额保留原始结算行金额，组合各组件重复展示整行值，不参与合计；人民币收入和费用仍按已保存组件份额换算。没有原始结算行时留空。"),
                 ("成本拆分", "采购、头程、尾程、其他入库成本按实际FIFO批次汇总；展示金额按分舍入，尾差按科目金额由大到小分配，合计等于本行订单总成本。组合商品同一订单号各行相加为整单成本。"),
                 ("跌价", impairment_help["formula"] + "；" + impairment_help["note"]),
                 ("待核对", "未知金额和相应合计留空，不以0代替；未核对成本沿用台账展示，作废批次相关毛利待核对。")), 1):
        _append(notes, pair, number)
    notes.column_dimensions["A"].width = 22
    notes.column_dimensions["B"].width = 100
    _append(notes, ("单件口径", "单件成本用未舍入金额除实际库存出库件数，显示4位；不等于平台组合销售套数。单价不合计，不用显示单价反算金额。"), notes.max_row + 1)
    _append(notes, ("订单汇总", "沿用同店内部订单分组：导入按订单ID+来源SKU分组，组合组件归在同组；订单号列仅显示原始编号，同号不同SKU仍可有多行，不合并或改变成本。读取范围不限当前商品、月份或页面，可能包含筛选行之外的组件，不与销售记录行重复累加。未知店铺范围待核对。"), notes.max_row + 1)
    for row in notes:
        row[1].alignment = Alignment(wrap_text=True)
    definitions = workbook.create_sheet("字段口径说明")
    _append(definitions, ("字段编码", "字段名称", "分组", "计算公式", "数据来源", "口径说明"), 1)
    for number, field in enumerate([f for f in [*resource["fields"], *resource["extra_fields"]] if f["kind"] != "action"], 2):
        _append(definitions, [field[key] for key in ("key", "label", "group", "formula", "source", "note")], number)
    _style_header(definitions)
    for column, width in enumerate((34, 28, 20, 70, 60, 90), 1):
        definitions.column_dimensions[get_column_letter(column)].width = width
    for row in definitions.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    definitions.freeze_panes = "D2"
    orders = workbook.create_sheet("订单汇总")
    _append(orders, ("店铺", "订单号", "出库明细数", "整单销售成本（元）", "整单出库跌价（元）", "范围确认", "说明"), 1)
    for number, order in enumerate(order_summaries, 2):
        _append(orders, [order.get("store_name") or "未关联店铺", order.get("display_order_no") or order["order_no"], order["line_count"],
            order.get("total_cost_cny"), order.get("impairment_cny"), "完整订单" if order["complete"] else "待核对",
            order.get("review_reason") or "同店铺完整出库明细；不受当前商品、月份或分页限制。"], number)
        orders.cell(number, 4).number_format = orders.cell(number, 5).number_format = '¥#,##0.00'
    _style_header(orders)
    for column, width in enumerate((24, 30, 16, 26, 26, 20, 90), 1):
        orders.column_dimensions[get_column_letter(column)].width = width
    orders.freeze_panes = "C2"
    orders.auto_filter.ref = orders.dimensions
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
