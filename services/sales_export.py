"""销售台账Excel；消费列表已计算值，不另算FIFO、汇率或店铺归属。"""
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from services.product_import import _set_text, _style_header
from services.sales_fields import sales_field_help
from services.sales_reconciliation import TEXT
from services.product_specification import specification_export_text

MAX_EXPORT_ROWS = 20_000
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HEADERS = ("店铺", "日期", "本地商品 SKU", "本地商品名称", "实际出库数量", "销售单价（元）", "FIFO成本单价（元）",
           "店铺收入（元）", "订单总成本（元，本行）", "其他费用（元）", "毛利（元）", "订单号", "记录类型",
           "原币", "核算汇率", "汇率日期", "成本状态", "说明", "采购成本（元）", "头程成本（元）",
           "尾程成本（元）", "其他入库成本（元）", "出库商品跌价（元，参考）",
           "单件采购（元/件）", "单件头程（元/件）", "单件尾程（元/件）", "单件其他入库成本（元/件）", "交易类型", "来源数量",
           "来源净商品销售额（原币，整行勿合计）", "来源结算总金额（原币，整行勿合计）", "汇率来源",
           "分摊净商品销售额（原币）", "分摊结算总金额（原币）",
           "关联编号", "来源文件", "来源工作表", "原文件行号", "来源 SKU ID", "来源产品名", "销售明细编号",
           "本地商品规格/型号", "来源规格（SKU 名称）", "导入指定规格/型号", "汇率核算月份")
SOURCE_FIELDS = (
    ("source_ref", "关联编号"), ("store_name", "店铺"), ("source_file", "来源文件"),
    ("source_sheet", "来源工作表"), ("source_row_number", "原文件行号"), ("order_no", "订单号"),
    ("sku", "SKU ID"), ("product_name", "产品名"), ("transaction_type", "交易类型"), ("quantity", "来源数量"),
    ("order_created_at", "订单创建日期"), ("settlement_date", "结算日期"), ("currency", "原币"),
    ("net_product_sales", "净商品销售额（原币）"), ("settlement_total", "结算总金额（原币）"),
    ("status", "处理状态"), ("reason", "待核对原因"), ("line_count", "出库明细数"), ("actual_quantity", "实际出库件数"),
    ("revenue", "来源收入（折人民币）"), ("cost", "销售成本（元）"), ("expense", "来源费用（折人民币）"),
    ("profit", "毛利（元）"), ("rate", "核算汇率"), ("rate_date", "汇率日期"), ("detail_ids", "销售明细编号"),
    ("cost_status", "成本状态"),
    ("source_specification", "来源规格（SKU 名称）"), ("requested_local_specification", "导入指定规格/型号"),
    ("rate_month", "汇率核算月份"), ("rate_source", "汇率来源"),
)
RECORD_NAMES = {"sale": "销售", "other_expense": "赔付费用", "compensation_income": "赔付收入", "manual_settlement": "手工结算补录", "settlement_adjustment": "退款/结算调整"}
COST_NAMES = {"actual": "已核对", "unverified": "未核对", "needs_review": "待核对", "not_applicable": "赠品免成本"}


def _append(sheet, values, row_number):
    for column, value in enumerate(values, 1):
        if isinstance(value, str):
            value = specification_export_text(value)
        cell = sheet.cell(row_number, column, value)
        if isinstance(value, str):
            _set_text(cell, value)


def _source_sheet(workbook, originals):
    sheet = workbook.active
    sheet.title = TEXT["zh-CN"]["source_sheet"]
    _append(sheet, [label for _key, label in SOURCE_FIELDS], 1)
    for number, row in enumerate(originals, 2):
        _append(sheet, [row.get(key) for key, _label in SOURCE_FIELDS], number)
    totals = {"source_ref": "合计"}
    currencies = {row.get("currency") for row in originals}
    sum_keys = ["line_count", "actual_quantity", "revenue", "cost", "expense", "profit"]
    if len(currencies) == 1 and None not in currencies and "" not in currencies:
        sum_keys.extend(("net_product_sales", "settlement_total"))
    for key in sum_keys:
        parts = [row.get(key) for row in originals]
        totals[key] = None if None in parts else sum(parts, Decimal(0))
    _append(sheet, [totals.get(key) for key, _label in SOURCE_FIELDS], len(originals) + 2)
    _style_header(sheet)
    widths = (16, 22, 36, 24, 14, 30, 24, 40, 18, 14, 20, 20, 12, 26, 26, 20, 64, 16, 18, 26, 24, 26, 22, 18, 16, 26, 30, 32, 32, 18, 24)
    for column, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for cells in sheet.iter_rows(min_row=2):
        for cell, (key, _label) in zip(cells, SOURCE_FIELDS):
            cell.alignment = Alignment(vertical="center", wrap_text=key in ("product_name", "reason", "detail_ids", "source_specification", "requested_local_specification"))
            if key in ("net_product_sales", "settlement_total"):
                cell.number_format = '#,##0.00##'
            elif key in ("revenue", "cost", "expense", "profit"):
                cell.number_format = '¥#,##0.00'
            elif key in ("order_created_at", "settlement_date", "rate_date"):
                cell.number_format = 'yyyy-mm-dd'
            elif key == "rate":
                cell.number_format = '0.00000000'
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="EEF3F8")
    sheet.freeze_panes = "F2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(SOURCE_FIELDS))}{len(originals) + 1}"
    return sheet


def build_sales_workbook(rows, *, month, keyword, scope, product_id=None, order_summaries=(), originals=(), batch_info=None):
    resource = sales_field_help()
    impairment_help = next(field for field in resource["fields"] if field["key"] == "impairment_cny")
    workbook = Workbook()
    source_sheet = _source_sheet(workbook, originals)
    sheet = workbook.create_sheet(TEXT["zh-CN"]["detail_sheet"])
    _append(sheet, HEADERS, 1)
    values = []
    for number, row in enumerate(rows, 2):
        notes = []
        if row["exchange_rate_to_cny"] is None:
            notes.append("汇率缺失，相关人民币金额待核对")
        if row.get("exchange_rate_is_provisional"):
            notes.append(TEXT["zh-CN"]["provisional_fx"])
        rate_source = row.get("exchange_rate_source")
        if row.get("exchange_rate_mode") == "manual":
            rate_source = TEXT["zh-CN"]["manual_provisional_fx" if row.get("exchange_rate_is_provisional") else "manual_fx"]
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
            row.get("source_row_net_product_sales"), row.get("source_row_settlement_total"), rate_source,
            row.get("allocated_net_product_sales"), row.get("allocated_settlement_total"),
            row.get("source_ref"), row.get("source_file"), row.get("source_sheet"), row.get("source_row_number"),
            row.get("source_sku_id"), row.get("source_product_name"), row.get("detail_id"),
            row.get("local_specification"), row.get("source_specification"), row.get("requested_local_specification"),
            row.get("exchange_rate_month"),
        ])
        _append(sheet, values[-1], number)
    totals = [None] * len(HEADERS)
    totals[0] = "合计"
    for col in (5, 8, 9, 10, 11, 19, 20, 21, 22, 23):
        parts = [row[col - 1] for row in values]
        totals[col - 1] = None if None in parts else sum(parts, Decimal(0))
    currencies = {row[13] for row in values}
    if len(currencies) == 1 and None not in currencies and "" not in currencies:
        for col in (33, 34):
            parts = [row[col - 1] for row in values]
            totals[col - 1] = None if None in parts else sum(parts, Decimal(0))
    _append(sheet, totals, len(rows) + 2)
    _style_header(sheet)
    widths = (22, 14, 24, 36, 14, 20, 22, 23, 26, 20, 20, 30, 22, 12, 18, 14, 16, 70, 20, 20, 20, 22, 30, 23, 23, 23, 25, 18, 16, 44, 42, 22, 32, 30,
              18, 36, 24, 16, 24, 40, 20, 32, 32, 32, 18)
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
        for column in (30, 31, 33, 34):
            cells[column - 1].number_format = '#,##0.00##'
        for cell in cells:
            cell.alignment = Alignment(vertical="center", wrap_text=cell.column in (4, 18, 35, 36, 37, 38, 40, 42, 43, 44))
    for cell in sheet[sheet.max_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="EEF3F8")
    sheet.freeze_panes = "E2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{len(rows) + 1}"
    source_positions = {row["source_ref"]: number for number, row in enumerate(originals, 2)}
    detail_positions = {}
    for number, row in enumerate(rows, 2):
        for reference in (row.get("source_ref") or "").splitlines():
            detail_positions.setdefault(reference, number)
        reference = row.get("source_ref")
        if reference in source_positions:
            sheet.cell(number, 35).hyperlink = f"#'{source_sheet.title}'!A{source_positions[reference]}"
            sheet.cell(number, 35).style = "Hyperlink"
    for reference, number in source_positions.items():
        if reference in detail_positions:
            source_sheet.cell(number, 1).hyperlink = f"#'{sheet.title}'!AI{detail_positions[reference]}"
            source_sheet.cell(number, 1).style = "Hyperlink"
    notes = workbook.create_sheet("说明")
    for number, pair in enumerate((("范围", scope), ("月份", month or "全部月份"), ("商品名称/SKU/订单号", keyword or "全部"),
                 ("商品ID", product_id), ("原始结算行及无来源历史销售行数", len(originals)), ("拆分明细数（含独立结算）", len(rows)),
                 ("口径", "第一张表按原始结算记录保留一行，包含待确认；同订单的多个SKU、退款和调整分别保留。第二张表按本地商品展示出库，独立结算的实际出库数量为0。两表为同一业务的不同层次，金额不可跨表相加。"),
                 ("范围规则", "按当前筛选匹配来源行后，包含该来源的全部获准组件，可能超出商品或月份筛选；待确认以订单创建日优先归月。按导入批次导出不受页面筛选影响；拆分明细的来源金额和订单号仅取本次获准来源，核算汇率按该销售的店铺和销售月份取值。已关联原始行跟随销售核算月份；独立结算及待确认按订单创建日、结算日、导入日依次归月。原始行关联多个销售月份时，折人民币金额待核对，核算月份见各表对应列。要与原文件逐行核对，请使用导入历史中的导出本次对账。"),
                 ("对应关系", "关联编号定位原始结算行，销售明细编号定位拆分行；可点击关联编号跳转。来源产品名与原始表一致，保留原列空值，历史缺列时读取保存的来源名称；多个来源按关联编号顺序换行。来源文件、工作表和原文件行号用于回查原文件，不按订单号或产品名去重。无来源的历史销售单独标记，不猜测来源名称、数量或金额。"),
                 ("待确认口径", "原始表的来源金额包括待确认行；折人民币使用本店对应核算月份的报表汇率，不能直接作为已确认销售报表总额。待确认、成本不完整或关联异常时，成本及毛利留空；多个来源共用销售或关联明细不完整时，原始表的出库明细数、实际出库件数也留空。未知项的合计留空，拆分表仍按每条销售明细统计实际出库数量。"),
                 ("金额", "收入、费用、成本及毛利为人民币，与财务报表共用店铺及月份汇率：手动优先，否则按月末最近可用值；当前月手动值标记暂估。跨月分别计算，汇率缺失不回退到旧逐日值。成本沿用当前成本追溯结果。来源净商品销售额、来源结算总金额为原币，币种见原币列，汇率表示1原币兑人民币；不修改库存、销售或原始成本。"),
                 ("原币核对", "第一张表的原始金额每来源行只计一次，可按同币种核对原文件；第二张表的来源整行金额在各组件重复展示，不可合计，核对拆分金额应使用分摊净商品销售额、分摊结算总金额。仅同币种且金额完整时显示原币合计；赔付净商品销售额保留原值，收入按赔付结算额计入。无来源时原币值留空。"),
                 ("成本拆分", "采购、头程、尾程、其他入库成本按实际FIFO批次汇总；展示金额按分舍入，尾差按科目金额由大到小分配，合计等于本行订单总成本。组合商品同一订单号各行相加为整单成本。"),
                 ("跌价", impairment_help["formula"] + "；" + impairment_help["note"]),
                 ("汇率归属", "原始结算店铺与关联销售的有效核算店铺不一致、销售归属缺失或关联多个核算月份时，原始表标记待核对，外币核算汇率及受影响的人民币金额、合计留空。原始店铺、原币及已确认成本保留，具体取率查看拆分明细；人民币固定汇率1及已知零金额仍可计算。"),
                 ("待核对", "未知金额和相应合计留空，不以0代替；未核对成本沿用台账展示，作废批次相关毛利待核对。")), 1):
        _append(notes, pair, number)
    notes.column_dimensions["A"].width = 22
    notes.column_dimensions["B"].width = 100
    _append(notes, ("商品规格", "来源规格保留原SKU名称；导入指定规格取原文件可选的规格/型号列；本地商品规格读取实际关联商品的当前资料，历史缺失留空，不推断颜色、装量或组合。规格列追加在表尾，原有字段位置保持不变。历史文本中Excel不支持的字符以[U+XXXX]标出，原始数据未修改。"), notes.max_row + 1)
    if batch_info:
        for pair in (("导入批次", batch_info["id"]), ("导入文件", batch_info["file_name"]),
                     ("文件有效数据行数", batch_info["total_rows"]), ("本次重复跳过行数", batch_info["skipped_duplicates"]),
                     ("批次范围", "仅包含本次实际保存的结算行；重复跳过行未在本批次保存，已撤销批次不提供对账导出。")):
            _append(notes, pair, notes.max_row + 1)
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
    workbook.active = 0
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
