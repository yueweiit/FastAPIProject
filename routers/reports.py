from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import case, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (
    InventoryBatch,
    InventoryPeriodSnapshot,
    Product,
    Sale,
    SaleCostDetail,
    Store,
    User,
)
from schemas import MonthlyReportItem
from auth import RequireAnyRole
from services.accounting_periods import (
    auto_confirm_expired_periods,
    ensure_monthly_period,
    get_confirmed_period,
)

router = APIRouter(prefix="/reports", tags=["报表"])

INVENTORY_IMPAIRMENT_SHEET = "07-存货跌价准备明细表"
INVENTORY_IMPAIRMENT_HEADERS = [
    "店铺",
    "SKU编码",
    "商品名称",
    "入库日期",
    "已入库天数",
    "单件到仓成本(元)",
    "库存数量",
    "库存总金额(元)",
    "累计计提比例",
    "累计跌价准备(元)",
    "账面价值(元)",
    "与上月末账面价值差额(元)",
    "状态",
    "备注",
]


def _previous_month_end(value: date) -> date:
    return value.replace(day=1) - timedelta(days=1)


def _excel_date(value: date) -> str:
    return f"DATE({value.year},{value.month},{value.day})"


def _build_inventory_impairment_workbook(
    rows: list[dict], report_date: date
) -> tuple[Workbook, dict[str, int | Decimal | str]]:
    previous_month_end = _previous_month_end(report_date)
    report_date_formula = _excel_date(report_date)
    previous_month_end_formula = _excel_date(previous_month_end)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = INVENTORY_IMPAIRMENT_SHEET
    sheet.freeze_panes = "A2"
    sheet.sheet_view.showGridLines = False
    sheet.append(INVENTORY_IMPAIRMENT_HEADERS)

    header_fill = PatternFill("solid", fgColor="C00000")
    thin_gray = Side(style="thin", color="BFBFBF")
    table_border = Border(left=thin_gray, right=thin_gray, top=thin_gray, bottom=thin_gray)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(name="等线", size=10, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = table_border
    sheet.row_dimensions[1].height = 32
    formula_cache: dict[str, int | Decimal | str] = {}

    for row_number, item in enumerate(rows, start=2):
        age_days = (report_date - item["arrived_at"]).days
        impairment_rate = min(Decimal(age_days) * Decimal("0.01"), Decimal("0.9"))
        inventory_amount = Decimal(item["unit_cost"]) * item["quantity"]
        impairment_amount = inventory_amount * impairment_rate
        book_value = inventory_amount - impairment_amount
        previous_age_days = max((previous_month_end - item["arrived_at"]).days, 0)
        previous_rate = min(Decimal(previous_age_days) * Decimal("0.01"), Decimal("0.9"))
        previous_inventory_amount = Decimal(item["unit_cost"]) * item["previous_month_quantity"]
        calculated_previous_book_value = previous_inventory_amount * (Decimal("1") - previous_rate)
        snapshot_previous_book_value = item.get("previous_book_value")
        previous_book_value = (
            Decimal(snapshot_previous_book_value)
            if snapshot_previous_book_value is not None
            else calculated_previous_book_value
        )
        book_value_difference = book_value - previous_book_value
        status = "达到上限" if impairment_rate >= Decimal("0.9") else (
            "接近上限" if impairment_rate >= Decimal("0.6") else "正常计提"
        )
        formula_cache.update({
            f"E{row_number}": age_days,
            f"H{row_number}": inventory_amount,
            f"I{row_number}": impairment_rate,
            f"J{row_number}": impairment_amount,
            f"K{row_number}": book_value,
            f"L{row_number}": book_value_difference,
            f"M{row_number}": status,
        })
        sheet.append([
            item["store_name"],
            item["sku"],
            item["product_name"],
            item["arrived_at"],
            f'=IF(D{row_number}="","",{report_date_formula}-D{row_number})',
            item["unit_cost"],
            item["quantity"],
            f'=IF(OR(F{row_number}="",G{row_number}=""),"",F{row_number}*G{row_number})',
            f'=IF(E{row_number}="","",MIN(E{row_number}*0.01,0.9))',
            f'=IF(OR(H{row_number}="",I{row_number}=""),"",H{row_number}*I{row_number})',
            f'=IF(OR(H{row_number}="",J{row_number}=""),"",H{row_number}-J{row_number})',
            (
                f'=IF(OR(D{row_number}="",F{row_number}="",K{row_number}=""),"",'
                f'K{row_number}-F{row_number}*{item["previous_month_quantity"]}*'
                f'(1-MIN(MAX({previous_month_end_formula}-D{row_number},0)*0.01,0.9)))'
            ),
            f'=IF(I{row_number}="","",IF(I{row_number}>=0.9,"达到上限",'
            f'IF(I{row_number}>=0.6,"接近上限","正常计提")))',
            (
                f"批次号：{item['batch_no']}；上月末库存：{item['previous_month_quantity']}；"
                "单件成本含采购、头程、尾程及其他成本，未单列关税"
            ),
        ])
        for cell in sheet[row_number]:
            cell.font = Font(name="等线", size=10)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = table_border
        sheet.cell(row_number, 4).number_format = "yyyy-mm-dd"
        sheet.cell(row_number, 5).number_format = "0"
        for column in (6, 8, 10, 11, 12):
            sheet.cell(row_number, column).number_format = '¥#,##0.00'
        sheet.cell(row_number, 7).number_format = "#,##0"
        sheet.cell(row_number, 9).number_format = "0%"

    first_data_row = 2
    last_data_row = len(rows) + 1
    total_row = last_data_row + 1
    sheet.cell(total_row, 1, "合计")
    if rows:
        for column in range(5, 13):
            letter = sheet.cell(1, column).column_letter
            sheet.cell(total_row, column, f"=SUM({letter}{first_data_row}:{letter}{last_data_row})")
            formula_cache[f"{letter}{total_row}"] = sum(
                formula_cache[f"{letter}{row_number}"]
                if letter not in ("F", "G")
                else rows[row_number - first_data_row]["unit_cost" if letter == "F" else "quantity"]
                for row_number in range(first_data_row, last_data_row + 1)
            )
    else:
        for column in range(5, 13):
            sheet.cell(total_row, column, 0)
    for cell in sheet[total_row]:
        cell.fill = PatternFill("solid", fgColor="F2F2F2")
        cell.font = Font(name="等线", size=10, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = table_border
    for column in (6, 8, 10, 11, 12):
        sheet.cell(total_row, column).number_format = '¥#,##0.00'
    sheet.cell(total_row, 5).number_format = "0"
    sheet.cell(total_row, 7).number_format = "#,##0"
    sheet.cell(total_row, 9).number_format = "0%"

    notes_row = total_row + 2
    notes = [
        f"报告截止日：{report_date.isoformat()}；上月末：{previous_month_end.isoformat()}。",
        "计提规则：按入库自然日每天计提1%，累计计提比例最高90%；达到60%标记为接近上限。",
        "库存口径：当前库存按截止日前已确认销售计算；上月末优先使用已确认的月末批次快照。",
        "成本口径：单件到仓成本使用系统批次单件成本，包含采购、头程、尾程及其他成本；当前未单列关税。",
        "店铺口径：按入库批次创建人的当前绑定店铺；无法确认时显示“未关联店铺”。",
    ]
    for offset, note in enumerate(notes):
        row_number = notes_row + offset
        sheet.merge_cells(start_row=row_number, start_column=1, end_row=row_number, end_column=14)
        cell = sheet.cell(row_number, 1, note)
        cell.font = Font(name="等线", size=9, color="666666")
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    widths = {
        "A": 18, "B": 18, "C": 28, "D": 13, "E": 12, "F": 18, "G": 12,
        "H": 18, "I": 14, "J": 20, "K": 17, "L": 25, "M": 13, "N": 48,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.auto_filter.ref = f"A1:N{max(1, last_data_row)}"
    sheet.print_title_rows = "1:1"
    sheet.print_area = f"A1:N{notes_row + len(notes) - 1}"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    workbook.calculation.calcMode = "auto"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    return workbook, formula_cache


def _workbook_bytes_with_formula_cache(
    workbook: Workbook, formula_cache: dict[str, int | Decimal | str]
) -> BytesIO:
    """Save formulas and their last-calculated values for Excel Protected View."""
    raw_output = BytesIO()
    workbook.save(raw_output)
    raw_output.seek(0)

    spreadsheet_namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ElementTree.register_namespace("", spreadsheet_namespace)
    output = BytesIO()
    with ZipFile(raw_output, "r") as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for member in source.infolist():
            content = source.read(member.filename)
            if member.filename == "xl/worksheets/sheet1.xml":
                root = ElementTree.fromstring(content)
                for cell in root.findall(f".//{{{spreadsheet_namespace}}}c"):
                    coordinate = cell.attrib.get("r")
                    if coordinate not in formula_cache:
                        continue
                    formula = cell.find(f"{{{spreadsheet_namespace}}}f")
                    if formula is None:
                        continue
                    cached_value = formula_cache[coordinate]
                    value = cell.find(f"{{{spreadsheet_namespace}}}v")
                    if value is None:
                        value = ElementTree.SubElement(cell, f"{{{spreadsheet_namespace}}}v")
                    if isinstance(cached_value, str):
                        cell.set("t", "str")
                        value.text = cached_value
                    else:
                        cell.attrib.pop("t", None)
                        value.text = (
                            format(cached_value, "f")
                            if isinstance(cached_value, Decimal)
                            else str(cached_value)
                        )
                content = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(member, content)
    output.seek(0)
    return output


@router.get("/inventory-impairment/export")
async def export_inventory_impairment_report(
    report_date: date = Query(..., description="报告截止日"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    """按指定截止日导出存货跌价准备明细表。"""
    report_cutoff = datetime.combine(report_date + timedelta(days=1), time.min)
    previous_month_end = _previous_month_end(report_date)
    previous_month_cutoff = datetime.combine(previous_month_end + timedelta(days=1), time.min)

    # 首次查看历史月份时补建该月期间；超过月末后的确认窗口会自动确认。
    await ensure_monthly_period(db, previous_month_end)
    await auto_confirm_expired_periods(db)
    confirmed_period = await get_confirmed_period(db, previous_month_end)
    snapshots_by_batch = {}
    if confirmed_period:
        snapshots_by_batch = {
            snapshot.batch_id: snapshot
            for snapshot in (
                await db.execute(
                    select(InventoryPeriodSnapshot).where(
                        InventoryPeriodSnapshot.accounting_period_id
                        == confirmed_period.id
                    )
                )
            ).scalars().all()
        }

    batch_result = await db.execute(
        select(
            InventoryBatch,
            Product.sku,
            Product.name.label("product_name"),
            Store.name.label("store_name"),
        )
        .join(Product, Product.id == InventoryBatch.product_id)
        .outerjoin(User, User.id == InventoryBatch.user_id)
        .outerjoin(Store, Store.id == User.store_id)
        .where(InventoryBatch.arrived_at < report_cutoff)
        .order_by(Store.name, Product.sku, InventoryBatch.arrived_at, InventoryBatch.id)
    )
    batch_rows = batch_result.all()
    batch_ids = [row[0].id for row in batch_rows]

    deducted_by_batch: dict[int, tuple[int, int]] = {}
    if batch_ids:
        deduction_result = await db.execute(
            select(
                SaleCostDetail.batch_id,
                func.coalesce(func.sum(case(
                    (Sale.sold_at < report_cutoff, SaleCostDetail.quantity),
                    else_=0,
                )), 0).label("report_quantity"),
                func.coalesce(func.sum(case(
                    (Sale.sold_at < previous_month_cutoff, SaleCostDetail.quantity),
                    else_=0,
                )), 0).label("previous_month_quantity"),
            )
            .join(Sale, Sale.id == SaleCostDetail.sale_id)
            .where(SaleCostDetail.batch_id.in_(batch_ids))
            .group_by(SaleCostDetail.batch_id)
        )
        deducted_by_batch = {
            row.batch_id: (int(row.report_quantity), int(row.previous_month_quantity))
            for row in deduction_result.all()
        }

    report_rows = []
    for batch, sku, product_name, store_name in batch_rows:
        report_deducted, previous_month_deducted = deducted_by_batch.get(batch.id, (0, 0))
        quantity = max(0, batch.quantity - report_deducted)
        previous_snapshot = snapshots_by_batch.get(batch.id)
        if previous_snapshot is not None:
            previous_month_quantity = int(previous_snapshot.quantity)
            previous_book_value = Decimal(previous_snapshot.book_value)
        else:
            previous_month_quantity = (
                max(0, batch.quantity - previous_month_deducted)
                if not confirmed_period and batch.arrived_at < previous_month_cutoff
                else 0
            )
            previous_book_value = None
        if quantity == 0 and previous_month_quantity == 0:
            continue
        report_rows.append({
            "store_name": store_name or "未关联店铺",
            "sku": sku,
            "product_name": product_name,
            "arrived_at": batch.arrived_at.date(),
            "unit_cost": batch.unit_cost,
            "quantity": quantity,
            "previous_month_quantity": previous_month_quantity,
            "previous_book_value": previous_book_value,
            "batch_no": batch.batch_no,
        })

    workbook, formula_cache = _build_inventory_impairment_workbook(report_rows, report_date)
    output = _workbook_bytes_with_formula_cache(workbook, formula_cache)
    filename = f"inventory_impairment_{report_date.strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/monthly", response_model=list[MonthlyReportItem])
async def monthly_report(
    start_date: date = Query(..., description="开始日期"),
    end_date: date = Query(..., description="结束日期"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    """月度报表 - 所有角色可查看，operator只看自己的数据"""
    month_expr = func.DATE_FORMAT(Sale.sold_at, "%Y-%m").label("month")
    start_at = datetime.combine(start_date, time.min)
    end_at = datetime.combine(end_date + timedelta(days=1), time.min)
    stmt = (
        select(
            month_expr,
            Sale.product_id,
            func.sum(Sale.quantity).label("sold_quantity"),
        )
        .where(
            Sale.sold_at >= start_at,
            Sale.sold_at < end_at,
        )
        .group_by(month_expr, Sale.product_id)
        .order_by(month_expr, Sale.product_id)
    )
    if user.role == "operator":
        stmt = stmt.where(Sale.user_id == user.id)

    result = await db.execute(stmt)
    sold_rows = result.all()

    # 库存信息
    inventory_stmt = select(
        InventoryBatch.product_id,
        func.sum(InventoryBatch.remaining_quantity).label("inventory_quantity"),
        func.sum(InventoryBatch.remaining_quantity * InventoryBatch.unit_cost).label("inventory_value"),
    ).group_by(InventoryBatch.product_id)
    inv_result = await db.execute(inventory_stmt)
    inventory_map: dict[int, tuple[int, Decimal]] = {}
    for row in inv_result.all():
        inventory_map[row.product_id] = (
            row.inventory_quantity or 0,
            row.inventory_value or Decimal("0"),
        )

    products_result = await db.execute(select(Product))
    product_map = {p.id: p for p in products_result.scalars().all()}

    reports = []
    for row in sold_rows:
        product = product_map.get(row.product_id)
        if not product:
            continue
        inv_qty, inv_val = inventory_map.get(row.product_id, (0, Decimal("0")))
        reports.append(MonthlyReportItem(
            month=row.month,
            product_id=row.product_id,
            sku=product.sku,
            product_name=product.name,
            sold_quantity=row.sold_quantity or 0,
            inventory_quantity=inv_qty,
            inventory_value=inv_val,
        ))

    return reports
