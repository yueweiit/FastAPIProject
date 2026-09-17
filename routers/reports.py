from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (
    InventoryBatch,
    InventoryPeriodSnapshot,
    Product,
    ProductImpairmentRule,
    Sale,
    SaleCostDetail,
    Store,
    StoreProfitLossReport,
    User,
)
from schemas import (
    MonthlyReportItem,
    StoreProfitLossCell,
    StoreProfitLossResponse,
    StoreProfitLossRowResponse,
    StoreProfitLossUpdateRequest,
)
from auth import RequireAnyRole, RequireOperator
from services.accounting_periods import ensure_period_snapshot
from services.oa_office_expenses import office_space_totals_by_application_date
from services.inventory_impairment import (
    InventoryLayer,
    ImpairmentRule,
    PRODUCT_TYPE_NEW,
    batch_impairments,
    daily_impairment_rate,
    impairment_rate,
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
    "计提数量",
    "库存总金额(元)",
    "累计计提比例",
    "累计跌价准备(元)",
    "账面价值(元)",
    "与上月末账面价值差额(元)",
    "状态",
    "备注",
]

STORE_PROFIT_LOSS_ROWS = (
    ("operating_revenue", "一、营业收入", "formula", None),
    ("product_sales_revenue", "  商品销售收入", "auto", "销售记录中的净商品收入"),
    ("shipping_revenue", "  运费收入", "manual", None),
    ("other_revenue", "  其他收入", "manual", None),
    ("operating_cost", "减：营业成本（对应）", "formula", None),
    ("product_purchase_cost", "  应商品采购成本", "auto", "FIFO 实际扣除数量 × 入库采购单价"),
    ("head_logistics_cost", "  头程尾程物流成本", "auto", "FIFO 实际扣除数量分摊入库头程和尾程物流费用"),
    ("customs_import_tax", "  关税及进口税费（正报）", "manual", None),
    ("local_logistics_cost", "  本地物流配送成本", "manual", None),
    ("fulfillment_cost", "  代发成本", "manual", None),
    ("other_direct_cost", "  其他直接成本", "auto", "销售记录中的其他费用"),
    ("gross_profit", "毛利", "formula", None),
    ("gross_margin", "毛利率", "formula", None),
    ("tax_surcharge", "减：税金及附加", "manual", None),
    ("selling_expenses", "减：销售费用（包括样本）", "formula", None),
    ("salary", "  人员薪资", "manual", None),
    ("advertising", "  广告推广费", "manual", None),
    ("platform_subscription", "  平台月费/年费", "manual", None),
    ("warehousing", "  仓储费用", "manual", None),
    ("delivery", "  配送费", "manual", None),
    ("platform_fines", "  平台罚款/赔偿", "manual", None),
    ("admin_expenses", "减：管理费用", "formula", None),
    ("rent_utilities", "  房租+水电+网费", "auto", "OA 办公场地费用中 LatínGo 的租金和电费，均分至有效店铺"),
    ("shared_admin", "  平摊人事+财务管理费用", "manual", None),
    ("research_development", "减：研发费用", "manual", None),
    ("finance_expenses", "减：财务费用", "manual", None),
    ("asset_impairment_loss", "减：资产减值损失", "formula", None),
    ("inventory_impairment", "    存货跌价准备", "auto", "本月末计提额减上月末计提额"),
    ("credit_impairment_loss", "减：信用减值损失", "manual", None),
    ("operating_profit", "二、营业利润", "formula", None),
    ("non_operating_income", "加：营业外收入", "manual", None),
    ("non_operating_expense", "减：营业外支出", "manual", None),
    ("income_tax_expense", "减：所得税费用", "manual", None),
    ("net_profit", "三、净利润", "formula", None),
)
STORE_PROFIT_LOSS_AUTO_KEYS = {
    key for key, _label, kind, _source in STORE_PROFIT_LOSS_ROWS if kind == "auto"
}
STORE_PROFIT_LOSS_FORMULA_KEYS = {
    key for key, _label, kind, _source in STORE_PROFIT_LOSS_ROWS if kind == "formula"
}
STORE_PROFIT_LOSS_MANUAL_KEYS = {
    key for key, _label, kind, _source in STORE_PROFIT_LOSS_ROWS if kind == "manual"
}
STORE_PROFIT_LOSS_ROW_KEYS = {
    key for key, _label, _kind, _source in STORE_PROFIT_LOSS_ROWS
}
STORE_PROFIT_LOSS_PERIODS = ("current", "previous", "ytd")


def _previous_month_end(value: date) -> date:
    return value.replace(day=1) - timedelta(days=1)


def _excel_date(value: date) -> str:
    return f"DATE({value.year},{value.month},{value.day})"


def _next_month_start(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _month_periods(report_month: date) -> dict[str, tuple[date, date]]:
    month_start = report_month.replace(day=1)
    previous_start = _previous_month_end(month_start).replace(day=1)
    return {
        "current": (month_start, _next_month_start(month_start)),
        "previous": (previous_start, month_start),
        "ytd": (date(month_start.year, 1, 1), _next_month_start(month_start)),
    }


def _decimal_or_zero(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _blank_profit_loss_cells() -> dict[str, dict[str, Decimal | None]]:
    return {
        key: {period: None for period in STORE_PROFIT_LOSS_PERIODS}
        for key, _label, _kind, _source in STORE_PROFIT_LOSS_ROWS
    }


def _build_store_profit_loss_values(
    auto_values: dict[str, dict[str, Decimal]],
    manual_values: dict,
) -> dict[str, dict[str, Decimal | None]]:
    """Combine generated/manual cells and apply the template's formulas."""
    values = _blank_profit_loss_cells()
    for key in STORE_PROFIT_LOSS_AUTO_KEYS:
        for period in STORE_PROFIT_LOSS_PERIODS:
            values[key][period] = auto_values.get(key, {}).get(period, Decimal("0"))
    for key in STORE_PROFIT_LOSS_MANUAL_KEYS:
        stored = manual_values.get(key, {}) if isinstance(manual_values, dict) else {}
        for period in STORE_PROFIT_LOSS_PERIODS:
            value = stored.get(period) if isinstance(stored, dict) else None
            values[key][period] = None if value in (None, "") else Decimal(str(value))

    for period in STORE_PROFIT_LOSS_PERIODS:
        get = lambda key: _decimal_or_zero(values[key][period])
        values["operating_revenue"][period] = sum(
            (get(key) for key in ("product_sales_revenue", "shipping_revenue", "other_revenue")),
            Decimal("0"),
        )
        values["operating_cost"][period] = sum(
            (get(key) for key in (
                "product_purchase_cost", "head_logistics_cost", "customs_import_tax",
                "local_logistics_cost", "fulfillment_cost", "other_direct_cost",
            )),
            Decimal("0"),
        )
        values["gross_profit"][period] = (
            get("operating_revenue") - get("operating_cost")
        )
        revenue = get("operating_revenue")
        values["gross_margin"][period] = (
            get("gross_profit") / revenue if revenue else None
        )
        values["selling_expenses"][period] = sum(
            (get(key) for key in (
                "salary", "advertising", "platform_subscription", "warehousing",
                "delivery", "platform_fines",
            )),
            Decimal("0"),
        )
        values["admin_expenses"][period] = get("rent_utilities") + get("shared_admin")
        values["asset_impairment_loss"][period] = get("inventory_impairment")
        values["operating_profit"][period] = (
            get("gross_profit")
            - get("tax_surcharge")
            - get("selling_expenses")
            - get("admin_expenses")
            - get("research_development")
            - get("finance_expenses")
            - get("asset_impairment_loss")
            - get("credit_impairment_loss")
        )
        values["net_profit"][period] = (
            get("operating_profit")
            + get("non_operating_income")
            - get("non_operating_expense")
            - get("income_tax_expense")
        )
    return values


def _store_sale_scope(store_id: int):
    return or_(
        Sale.store_id == store_id,
        and_(Sale.store_id.is_(None), User.store_id == store_id),
    )


def _add_period_value(
    target: dict[str, Decimal], sold_at: date, amount: Decimal,
    periods: dict[str, tuple[date, date]],
) -> None:
    for period, (start, end) in periods.items():
        if start <= sold_at < end:
            target[period] += amount


async def _store_profit_loss_auto_values(
    db: AsyncSession, store_id: int, report_month: date
) -> dict[str, dict[str, Decimal]]:
    periods = _month_periods(report_month)
    ytd_start, ytd_end = periods["ytd"]
    sales = list((await db.execute(
        select(Sale)
        .outerjoin(User, User.id == Sale.user_id)
        .where(
            _store_sale_scope(store_id),
            Sale.sold_at >= datetime.combine(ytd_start, time.min),
            Sale.sold_at < datetime.combine(ytd_end, time.min),
        )
    )).scalars().all())

    auto_values = {
        key: {period: Decimal("0") for period in STORE_PROFIT_LOSS_PERIODS}
        for key in STORE_PROFIT_LOSS_AUTO_KEYS
    }
    if sales:
        # Imported rows retain their source amount and historical FX rate in the
        # sales module; manual sales fall back to their stored CNY amount.
        from routers.sales import _sale_import_context

        import_context_by_sale, _ = await _sale_import_context(db, sales)
        for sale in sales:
            context = import_context_by_sale.get(sale.id, {})
            rate = context.get("exchange_rate_to_cny", Decimal("1"))
            if rate is None:
                continue
            revenue = context.get(
                "source_net_product_sales", sale.selling_price * sale.quantity
            ) * rate
            _add_period_value(
                auto_values["product_sales_revenue"],
                sale.sold_at.date(),
                revenue,
                periods,
            )

    if sales:
        sale_ids = [sale.id for sale in sales]
        cost_rows = (await db.execute(
            select(SaleCostDetail, InventoryBatch, Sale.sold_at)
            .join(Sale, Sale.id == SaleCostDetail.sale_id)
            .join(InventoryBatch, InventoryBatch.id == SaleCostDetail.batch_id)
            .where(SaleCostDetail.sale_id.in_(sale_ids))
        )).all()
        for detail, batch, sold_at in cost_rows:
            quantity = Decimal(detail.quantity)
            _add_period_value(
                auto_values["product_purchase_cost"], sold_at.date(),
                batch.purchase_price * quantity, periods,
            )
            divisor = Decimal(batch.quantity or 1)
            _add_period_value(
                auto_values["head_logistics_cost"], sold_at.date(),
                (batch.shipping_cost + batch.last_mile_cost) * quantity / divisor, periods,
            )
        for sale in sales:
            context = import_context_by_sale.get(sale.id, {})
            rate = context.get("exchange_rate_to_cny", Decimal("1"))
            if rate is None:
                continue
            other_expense = context.get("source_other_expense", sale.platform_fee) * rate
            _add_period_value(
                auto_values["other_direct_cost"],
                sale.sold_at.date(),
                other_expense,
                periods,
            )

    previous_month_end = _previous_month_end(report_month.replace(day=1))
    month_before_end = _previous_month_end(previous_month_end.replace(day=1))
    current_impairment = await _store_inventory_impairment_total(
        db, store_id, datetime.combine(_next_month_start(report_month.replace(day=1)), time.min)
    )
    previous_impairment = await _store_inventory_impairment_total(
        db, store_id, month_start_cutoff(previous_month_end)
    )
    month_before_impairment = await _store_inventory_impairment_total(
        db, store_id, month_start_cutoff(month_before_end)
    )
    year_end_previous = date(report_month.year - 1, 12, 31)
    year_end_impairment = await _store_inventory_impairment_total(
        db, store_id, month_start_cutoff(year_end_previous)
    )
    auto_values["inventory_impairment"] = {
        "current": current_impairment - previous_impairment,
        "previous": previous_impairment - month_before_impairment,
        "ytd": current_impairment - year_end_impairment,
    }
    active_store_count = await db.scalar(
        select(func.count()).select_from(Store).where(Store.is_active.is_(True))
    )
    if active_store_count:
        office_totals = await office_space_totals_by_application_date(ytd_start, ytd_end)
        for application_date, total in office_totals.items():
            _add_period_value(
                auto_values["rent_utilities"],
                application_date,
                total / Decimal(active_store_count),
                periods,
            )
    return auto_values


def month_start_cutoff(value: date) -> datetime:
    return datetime.combine(value + timedelta(days=1), time.min)


async def _store_inventory_impairment_total(
    db: AsyncSession, store_id: int, cutoff: datetime
) -> Decimal:
    batch_rows = (await db.execute(
        select(
            InventoryBatch,
            User.store_id,
            Product.product_type,
            Product.safe_stock_quantity,
        )
        .join(Product, Product.id == InventoryBatch.product_id)
        .outerjoin(User, User.id == InventoryBatch.user_id)
        .where(InventoryBatch.arrived_at < cutoff)
    )).all()
    if not batch_rows:
        return Decimal("0")
    batch_ids = [batch.id for batch, *_ in batch_rows]
    product_ids = {batch.product_id for batch, *_ in batch_rows}
    rule_rows = (await db.execute(
        select(ProductImpairmentRule).where(
            ProductImpairmentRule.product_id.in_(product_ids)
        )
    )).scalars().all()
    rules_by_product: defaultdict[int, list[ImpairmentRule]] = defaultdict(list)
    for rule in rule_rows:
        rules_by_product[rule.product_id].append(ImpairmentRule(
            product_type=rule.product_type,
            safe_stock_quantity=rule.safe_stock_quantity,
            effective_date=rule.effective_date,
        ))
    deductions = (await db.execute(
        select(
            SaleCostDetail.batch_id,
            func.coalesce(func.sum(SaleCostDetail.quantity), 0),
        )
        .join(Sale, Sale.id == SaleCostDetail.sale_id)
        .where(
            SaleCostDetail.batch_id.in_(batch_ids),
            Sale.sold_at < cutoff,
        )
        .group_by(SaleCostDetail.batch_id)
    )).all()
    deducted_by_batch = {row[0]: int(row[1] or 0) for row in deductions}
    as_of = (cutoff - timedelta(days=1)).date()
    layers = [
        InventoryLayer(
            batch_id=batch.id,
            product_id=batch.product_id,
            store_id=batch_store_id,
            arrived_at=batch.arrived_at.date(),
            quantity=max(0, batch.quantity - deducted_by_batch.get(batch.id, 0)),
            product_type=product_type,
            safe_stock_quantity=safe_stock_quantity,
            rules=tuple(rules_by_product[batch.product_id]),
        )
        for batch, batch_store_id, product_type, safe_stock_quantity in batch_rows
    ]
    impairments = batch_impairments(layers, as_of)
    total = Decimal("0")
    for batch, batch_store_id, _product_type, _safe_stock_quantity in batch_rows:
        if batch_store_id != store_id:
            continue
        impairment = impairments.get(batch.id)
        if impairment:
            total += batch.unit_cost * impairment.impairment_units
    return total


async def _resolve_report_store(
    db: AsyncSession, user: User, store_id: int | None
) -> Store:
    if user.role == "operator":
        if user.store_id is None:
            raise HTTPException(status_code=400, detail="当前账号未绑定店铺，请联系管理员")
        if store_id is not None and store_id != user.store_id:
            raise HTTPException(status_code=403, detail="运营账号只能查看绑定店铺")
        store_id = user.store_id
    if store_id is None:
        store_id = await db.scalar(
            select(Store.id).where(Store.is_active.is_(True)).order_by(Store.id).limit(1)
        )
    if store_id is None:
        raise HTTPException(status_code=404, detail="暂无可用店铺")
    store = await db.get(Store, store_id)
    if not store:
        raise HTTPException(status_code=404, detail="店铺不存在")
    return store


async def _store_profit_loss_response(
    db: AsyncSession, user: User, store: Store, report_month: date,
    report: StoreProfitLossReport | None,
) -> StoreProfitLossResponse:
    auto_values = await _store_profit_loss_auto_values(db, store.id, report_month)
    manual_values = report.manual_values if report else {}
    values = _build_store_profit_loss_values(auto_values, manual_values)
    remarks = report.remarks if report else {}
    rows = [
        StoreProfitLossRowResponse(
            key=key,
            label=label,
            kind=kind,
            is_auto=kind == "auto",
            is_formula=kind == "formula",
            source=source,
            value=StoreProfitLossCell(**values[key]),
            remark=str(remarks.get(key, "")),
        )
        for key, label, kind, source in STORE_PROFIT_LOSS_ROWS
    ]
    return StoreProfitLossResponse(
        store_id=store.id,
        store_name=store.name,
        store_platform=store.platform,
        report_month=report_month.strftime("%Y-%m"),
        period_label=f"{report_month.year}年{report_month.month}月",
        can_edit=user.role in ("admin", "operator"),
        updated_at=report.updated_at if report else None,
        rows=rows,
    )


@router.get("/store-profit-loss", response_model=StoreProfitLossResponse)
async def get_store_profit_loss(
    report_month: date = Query(..., description="核算月份，传入该月第一天"),
    store_id: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireAnyRole),
):
    """获取单店单月损益表，自动项由系统数据生成，其他项保留人工输入。"""
    if report_month.day != 1:
        raise HTTPException(status_code=400, detail="核算月份必须传入该月第一天")
    store = await _resolve_report_store(db, user, store_id)
    report = await db.scalar(
        select(StoreProfitLossReport).where(
            StoreProfitLossReport.store_id == store.id,
            StoreProfitLossReport.report_month == report_month,
        )
    )
    return await _store_profit_loss_response(db, user, store, report_month, report)


@router.put("/store-profit-loss", response_model=StoreProfitLossResponse)
async def update_store_profit_loss(
    data: StoreProfitLossUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(RequireOperator),
):
    """保存单店单月损益表中的人工输入。"""
    if data.report_month.day != 1:
        raise HTTPException(status_code=400, detail="核算月份必须传入该月第一天")
    store = await _resolve_report_store(db, user, data.store_id)
    manual_values = {}
    for key, cell in data.manual_values.items():
        if key not in STORE_PROFIT_LOSS_MANUAL_KEYS:
            continue
        manual_values[key] = {
            period: (
                None
                if getattr(cell, period) is None
                else str(getattr(cell, period))
            )
            for period in STORE_PROFIT_LOSS_PERIODS
        }
    remarks = {
        key: str(value).strip()[:500]
        for key, value in data.remarks.items()
        if key in STORE_PROFIT_LOSS_ROW_KEYS and str(value).strip()
    }
    report = await db.scalar(
        select(StoreProfitLossReport).where(
            StoreProfitLossReport.store_id == store.id,
            StoreProfitLossReport.report_month == data.report_month,
        )
    )
    if report is None:
        report = StoreProfitLossReport(
            store_id=store.id,
            report_month=data.report_month,
            manual_values=manual_values,
            remarks=remarks,
            updated_by_user_id=user.id,
        )
        db.add(report)
    else:
        report.manual_values = manual_values
        report.remarks = remarks
        report.updated_by_user_id = user.id
    await db.commit()
    await db.refresh(report)
    return await _store_profit_loss_response(db, user, store, data.report_month, report)


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
        age_days = max((report_date - item["arrived_at"]).days, 0)
        product_type = item.get("product_type", "stable")
        provision_quantity = int(item.get("provision_quantity", item["quantity"]))
        current_rate = item.get(
            "impairment_rate",
            impairment_rate(report_date, item["arrived_at"], product_type),
        )
        inventory_amount = Decimal(item["unit_cost"]) * item["quantity"]
        impairment_amount = Decimal(item.get(
            "impairment_amount",
            Decimal(item["unit_cost"]) * provision_quantity * current_rate,
        ))
        book_value = inventory_amount - impairment_amount
        previous_provision_quantity = int(item.get(
            "previous_provision_quantity", item["previous_month_quantity"]
        ))
        previous_rate = item.get(
            "previous_impairment_rate",
            impairment_rate(previous_month_end, item["arrived_at"], product_type),
        )
        previous_impairment_amount = item.get("previous_impairment_amount")
        calculated_previous_book_value = Decimal(item["unit_cost"]) * Decimal(
            item["previous_month_quantity"]
        ) - (
            Decimal(previous_impairment_amount)
            if previous_impairment_amount is not None
            else Decimal(item["unit_cost"]) * Decimal(previous_provision_quantity) * previous_rate
        )
        snapshot_previous_book_value = item.get("previous_book_value")
        previous_book_value = (
            Decimal(snapshot_previous_book_value)
            if snapshot_previous_book_value is not None
            else calculated_previous_book_value
        )
        book_value_difference = book_value - previous_book_value
        status = "安全库存内" if provision_quantity == 0 and item["quantity"] else (
            "达到上限" if current_rate >= Decimal("0.9") else (
                "接近上限" if current_rate >= Decimal("0.6") else "正常计提"
            )
        )
        daily_rate = daily_impairment_rate(report_date, product_type)
        previous_daily_rate = daily_impairment_rate(previous_month_end, product_type)
        uses_historical_amount = "impairment_amount" in item
        uses_snapshot_previous_book_value = snapshot_previous_book_value is not None
        formula_cache.update({
            f"E{row_number}": age_days,
            f"H{row_number}": provision_quantity,
            f"I{row_number}": inventory_amount,
            f"J{row_number}": current_rate,
            f"K{row_number}": impairment_amount,
            f"L{row_number}": book_value,
            f"M{row_number}": book_value_difference,
            f"N{row_number}": status,
        })
        sheet.append([
            item["store_name"],
            item["sku"],
            item["product_name"],
            item["arrived_at"],
            f'=IF(D{row_number}="","",{report_date_formula}-D{row_number})',
            item["unit_cost"],
            item["quantity"],
            provision_quantity,
            f'=IF(OR(F{row_number}="",G{row_number}=""),"",F{row_number}*G{row_number})',
            (
                current_rate if uses_historical_amount
                else f'=IF(E{row_number}="","",MIN(E{row_number}*{daily_rate},0.9))'
            ),
            (
                impairment_amount if uses_historical_amount
                else f'=IF(OR(F{row_number}="",H{row_number}="",J{row_number}=""),"",F{row_number}*H{row_number}*J{row_number})'
            ),
            f'=IF(OR(I{row_number}="",K{row_number}=""),"",I{row_number}-K{row_number})',
            (
                book_value_difference if uses_historical_amount
                or uses_snapshot_previous_book_value
                else (
                    f'=IF(OR(D{row_number}="",F{row_number}="",L{row_number}=""),"",'
                    f'L{row_number}-F{row_number}*({item["previous_month_quantity"]}-'
                    f'{previous_provision_quantity}*MIN(MAX({previous_month_end_formula}-D{row_number},0)*{previous_daily_rate},0.9)))'
                )
            ),
            (
                f'=IF(H{row_number}=0,"安全库存内",IF(J{row_number}>=0.9,"达到上限",'
                f'IF(J{row_number}>=0.6,"接近上限","正常计提")))'
            ),
            (
                f"批次号：{item['batch_no']}；商品类型："
                f"{'新品' if product_type == PRODUCT_TYPE_NEW else '稳健商品'}；"
                f"上月末库存：{item['previous_month_quantity']}；"
                f"上月末账面价值：{previous_book_value:.4f}；"
                "单件成本含采购、头程、尾程及其他成本，未单列关税"
            ),
        ])
        for cell in sheet[row_number]:
            cell.font = Font(name="等线", size=10)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = table_border
        sheet.cell(row_number, 4).number_format = "yyyy-mm-dd"
        sheet.cell(row_number, 5).number_format = "0"
        for column in (6, 9, 11, 12, 13):
            sheet.cell(row_number, column).number_format = '¥#,##0.00'
        for column in (7, 8):
            sheet.cell(row_number, column).number_format = "#,##0"
        sheet.cell(row_number, 10).number_format = "0%"

    first_data_row = 2
    last_data_row = len(rows) + 1
    total_row = last_data_row + 1
    sheet.cell(total_row, 1, "合计")
    if rows:
        for column in range(5, 14):
            letter = sheet.cell(1, column).column_letter
            sheet.cell(total_row, column, f"=SUM({letter}{first_data_row}:{letter}{last_data_row})")
            formula_cache[f"{letter}{total_row}"] = sum(
                formula_cache[f"{letter}{row_number}"]
                if letter not in ("F", "G")
                else rows[row_number - first_data_row]["unit_cost" if letter == "F" else "quantity"]
                for row_number in range(first_data_row, last_data_row + 1)
            )
    else:
        for column in range(5, 14):
            sheet.cell(total_row, column, 0)
    for cell in sheet[total_row]:
        cell.fill = PatternFill("solid", fgColor="F2F2F2")
        cell.font = Font(name="等线", size=10, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = table_border
    for column in (6, 9, 11, 12, 13):
        sheet.cell(total_row, column).number_format = '¥#,##0.00'
    sheet.cell(total_row, 5).number_format = "0"
    for column in (7, 8):
        sheet.cell(total_row, column).number_format = "#,##0"
    sheet.cell(total_row, 10).number_format = "0%"

    notes_row = total_row + 2
    notes = [
        f"报告截止日：{report_date.isoformat()}；上月末：{previous_month_end.isoformat()}。",
        "计提规则：2026-09起，稳健商品仅超出安全库存的数量按每日1%计提；新品全部库存按每日0.5%计提；类型变更前已计提金额保留，累计上限90%。",
        "安全库存口径：按商品全仓汇总，优先豁免最新入库批次；此前月份仍按每日1%计提全部库存。",
        "库存口径：当前库存按截止日前已确认销售计算；上月末使用系统自动生成的月末快照。",
        "成本口径：单件到仓成本使用系统批次单件成本，包含采购、头程、尾程及其他成本；当前未单列关税。",
        "店铺口径：按入库批次创建人的当前绑定店铺；无法确认时显示“未关联店铺”。",
    ]
    for offset, note in enumerate(notes):
        row_number = notes_row + offset
        sheet.merge_cells(start_row=row_number, start_column=1, end_row=row_number, end_column=15)
        cell = sheet.cell(row_number, 1, note)
        cell.font = Font(name="等线", size=9, color="666666")
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    widths = {
        "A": 18, "B": 18, "C": 28, "D": 13, "E": 12, "F": 18, "G": 12,
        "H": 12, "I": 18, "J": 14, "K": 20, "L": 17, "M": 25, "N": 13, "O": 62,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.auto_filter.ref = f"A1:O{max(1, last_data_row)}"
    sheet.print_title_rows = "1:1"
    sheet.print_area = f"A1:O{notes_row + len(notes) - 1}"
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
    if report_date > date.today():
        raise HTTPException(status_code=400, detail="报告截止日不能晚于当天")
    report_cutoff = datetime.combine(report_date + timedelta(days=1), time.min)
    previous_month_end = _previous_month_end(report_date)
    snapshot_period = await ensure_period_snapshot(db, previous_month_end)
    snapshots_by_batch = {
        snapshot.batch_id: snapshot
        for snapshot in (
            await db.execute(
                select(InventoryPeriodSnapshot).where(
                    InventoryPeriodSnapshot.accounting_period_id == snapshot_period.id
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
            Product.product_type,
            Product.safe_stock_quantity,
            User.store_id,
        )
        .join(Product, Product.id == InventoryBatch.product_id)
        .outerjoin(User, User.id == InventoryBatch.user_id)
        .outerjoin(Store, Store.id == User.store_id)
        .where(InventoryBatch.arrived_at < report_cutoff)
        .order_by(Store.name, Product.sku, InventoryBatch.arrived_at, InventoryBatch.id)
    )
    batch_rows = batch_result.all()
    batch_ids = [row[0].id for row in batch_rows]
    product_ids = {row[0].product_id for row in batch_rows}
    rule_rows = (await db.execute(
        select(ProductImpairmentRule).where(
            ProductImpairmentRule.product_id.in_(product_ids)
        )
    )).scalars().all() if product_ids else []
    rules_by_product: defaultdict[int, list[ImpairmentRule]] = defaultdict(list)
    for rule in rule_rows:
        rules_by_product[rule.product_id].append(ImpairmentRule(
            product_type=rule.product_type,
            safe_stock_quantity=rule.safe_stock_quantity,
            effective_date=rule.effective_date,
        ))

    deducted_by_batch: dict[int, int] = {}
    if batch_ids:
        deduction_result = await db.execute(
            select(
                SaleCostDetail.batch_id,
                func.coalesce(func.sum(case(
                    (Sale.sold_at < report_cutoff, SaleCostDetail.quantity),
                    else_=0,
                )), 0).label("report_quantity"),
            )
            .join(Sale, Sale.id == SaleCostDetail.sale_id)
            .where(SaleCostDetail.batch_id.in_(batch_ids))
            .group_by(SaleCostDetail.batch_id)
        )
        deducted_by_batch = {
            row.batch_id: int(row.report_quantity)
            for row in deduction_result.all()
        }

    report_items = []
    for (
        batch,
        sku,
        product_name,
        store_name,
        product_type,
        safe_stock_quantity,
        store_id,
    ) in batch_rows:
        report_deducted = deducted_by_batch.get(batch.id, 0)
        quantity = max(0, batch.quantity - report_deducted)
        previous_snapshot = snapshots_by_batch.get(batch.id)
        previous_month_quantity = int(previous_snapshot.quantity) if previous_snapshot else 0
        previous_book_value = (
            Decimal(previous_snapshot.book_value) if previous_snapshot else None
        )
        if quantity == 0 and previous_month_quantity == 0:
            continue
        report_items.append({
            "batch": batch,
            "store_name": store_name or "未关联店铺",
            "sku": sku,
            "product_name": product_name,
            "store_id": store_id,
            "product_type": product_type,
            "safe_stock_quantity": safe_stock_quantity,
            "arrived_at": batch.arrived_at.date(),
            "unit_cost": batch.unit_cost,
            "quantity": quantity,
            "previous_month_quantity": previous_month_quantity,
            "previous_book_value": previous_book_value,
            "batch_no": batch.batch_no,
        })

    current_impairments = batch_impairments([
        InventoryLayer(
            batch_id=item["batch"].id,
            product_id=item["batch"].product_id,
            store_id=item["store_id"],
            arrived_at=item["arrived_at"],
            quantity=item["quantity"],
            product_type=item["product_type"],
            safe_stock_quantity=item["safe_stock_quantity"],
            rules=tuple(rules_by_product[item["batch"].product_id]),
        )
        for item in report_items
    ], report_date)
    report_rows = []
    for item in report_items:
        current_impairment = current_impairments.get(item["batch"].id)
        report_rows.append({
            key: value for key, value in item.items() if key != "batch"
        } | {
            "provision_quantity": current_impairment.provision_quantity if current_impairment else 0,
            "impairment_rate": current_impairment.rate if current_impairment else Decimal("0"),
            "impairment_amount": (
                item["unit_cost"] * current_impairment.impairment_units
                if current_impairment else Decimal("0")
            ),
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
