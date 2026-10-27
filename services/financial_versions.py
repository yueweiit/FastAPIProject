"""店铺/月不可变结果版本；后续补录只产生新版本，不锁死业务月份。"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from models import (FinancialExpense, FinancialExpenseSource, FinancialReportVersion, StoreProfitLossReport,
                    InventoryCostAdjustment, InventoryCostAdjustmentLine, SaleCostDetail, Sale)
from services.financial_controls import (audit, digest, fail, json_safe, month_bounds,
                                         reconciliation, require_enabled, scoped_store, today)


async def collect_payload(db, user, store, report_month, *, oa_values=None, active_store_count=None):
    from routers.reports import (_store_profit_loss_response, _load_inventory_impairment_report_rows,
                                 _product_line_profit_data)
    end = month_bounds(report_month)[1]
    cutoff = min(end - timedelta(days=1), today())
    source_start = min(date(report_month.year, 1, 1), (report_month-timedelta(days=1)).replace(day=1))
    report = await db.scalar(select(StoreProfitLossReport).where(
        StoreProfitLossReport.store_id == store.id, StoreProfitLossReport.report_month == report_month))
    response = await _store_profit_loss_response(
        db, user, store, report_month, report,
        oa_values=oa_values, active_store_count=active_store_count,
    )
    response.can_edit = False
    inventory = await _load_inventory_impairment_report_rows(db, cutoff, store.id)
    lines = await _product_line_profit_data(db, {store.id}, report_month, cutoff)
    reconciliation_data = await reconciliation(db, store.id, report_month)
    warnings = [*response.warnings, *reconciliation_data["warnings"]]
    unchecked = sum(row.get("cost_status") != "actual" for row in inventory)
    if unchecked:
        warnings.append(f"{unchecked}个历史或当前批次尚未全部完成采购/头程/尾程/其他成本核对，结果包含未核对成本。")
    expenses = (await db.execute(select(FinancialExpense).where(
        FinancialExpense.store_id == store.id, FinancialExpense.expense_date >= source_start,
        FinancialExpense.expense_date < end).order_by(FinancialExpense.id))).scalars().all()
    sources = (await db.execute(select(FinancialExpenseSource).where(
        FinancialExpenseSource.store_id == store.id, FinancialExpenseSource.report_month >= source_start,
        FinancialExpenseSource.report_month < end).order_by(FinancialExpenseSource.id))).scalars().all()
    if any(row.status == "draft" for row in expenses):
        warnings.append("本年存在待确认费用凭证，请核对当前人工/明细来源是否已覆盖，避免遗漏或重复。")
    values = {row.key: row.value.current for row in response.rows}
    # 保存涉及批次的调整来源，金额回到原值时仍能区分已发生的补差/反向单。
    batch_ids = {row["batch_id"] for row in inventory}
    batch_ids.update((await db.execute(select(SaleCostDetail.batch_id).join(
        Sale, Sale.id == SaleCostDetail.sale_id).where(
        Sale.store_id == store.id, Sale.sold_at < end))).scalars().all())
    cost_sources = (await db.execute(select(InventoryCostAdjustmentLine, InventoryCostAdjustment).join(
        InventoryCostAdjustment, InventoryCostAdjustment.id == InventoryCostAdjustmentLine.adjustment_id
    ).where(InventoryCostAdjustmentLine.batch_id.in_(batch_ids)).order_by(InventoryCostAdjustmentLine.id))).all() if batch_ids else []
    def subtract(value, keys):
        parts = [values[key] for key in keys]
        return None if value is None or any(x is None for x in parts) else value - sum(parts, Decimal(0))
    product_margin = subtract(values["product_sales_revenue"], ("product_purchase_cost", "head_logistics_cost", "inventory_other_cost"))
    platform_margin = None if product_margin is None or values["other_direct_cost"] is None else product_margin-values["other_direct_cost"]
    return json_safe({"store": {"id": store.id, "name": store.name, "platform": store.platform},
        "report_month": report_month, "report_date": cutoff, "profit_loss": response.model_dump(mode="json"),
        "inventory": inventory, "product_lines": lines, "reconciliation": reconciliation_data,
        "cost_manifest": [{"line_id": line.id, "batch_id": line.batch_id, "quantity": line.quantity,
                           "amount_cny": line.amount_cny, "adjustment_id": header.id,
                           "category": header.category, "source_ref": header.source_ref,
                           "is_final": header.is_final, "reverses_id": header.reverses_id}
                          for line, header in cost_sources],
        "expense_sources": [{"month": row.report_month, "category": row.category, "mode": row.mode} for row in sources],
        "expense_manifest": [{"id": row.id, "category": row.category, "amount": row.amount_cny,
                              "status": row.status, "source_ref": row.source_ref} for row in expenses],
        "profit_layers": {"after_inventory_cost": product_margin, "after_platform_cost": platform_margin,
                          "net_profit": values["net_profit"]},
        "warnings": list(dict.fromkeys(warnings)), "calculation_version": "restated-cost-v1"})


async def save_version(db, user, payload, *, oa_values=None, active_store_count=None):
    require_enabled()
    if db.get_bind().dialect.name == "mysql":
        connection = await db.connection()
        if await connection.get_isolation_level() != "REPEATABLE READ":
            fail("snapshot_isolation_required", "保存月度版本需要REPEATABLE READ一致性快照，请先核验数据库隔离级别。", 503)
    month_bounds(payload.report_month)
    if payload.report_month >= today().replace(day=1):
        fail("month_not_complete", "月度版本只能保存已结束的完整自然月。")
    store = await scoped_store(db, user, payload.store_id, lock=True)
    existing = await db.scalar(select(FinancialReportVersion).where(FinancialReportVersion.request_key == payload.request_key))
    if existing:
        if existing.store_id != store.id or existing.report_month != payload.report_month or existing.note != payload.note:
            fail("idempotency_conflict", "相同请求标识的店铺、月份或备注已改变。", 409)
        return existing
    result = await collect_payload(
        db, user, store, payload.report_month,
        oa_values=oa_values, active_store_count=active_store_count,
    )
    fingerprint = digest(result)
    duplicate = await db.scalar(select(FinancialReportVersion).where(
        FinancialReportVersion.store_id == store.id, FinancialReportVersion.report_month == payload.report_month,
        FinancialReportVersion.source_digest == fingerprint))
    if duplicate:
        return duplicate
    latest = await db.scalar(select(FinancialReportVersion).where(
        FinancialReportVersion.store_id == store.id, FinancialReportVersion.report_month == payload.report_month
    ).order_by(FinancialReportVersion.version.desc()).limit(1).with_for_update())
    row = FinancialReportVersion(request_key=payload.request_key, store_id=store.id,
        report_month=payload.report_month, version=(latest.version+1 if latest else 1), source_digest=fingerprint,
        payload=result, note=payload.note, created_by=user.id,
        status="needs_review" if result["warnings"] else "confirmed")
    db.add(row)
    await db.flush()
    await audit(db, user, row, "capture_version", store_id=store.id)
    return row


async def compare_version(db, user, version, *, oa_values=None, active_store_count=None):
    store = await scoped_store(db, user, version.store_id)
    current = await collect_payload(
        db, user, store, version.report_month,
        oa_values=oa_values, active_store_count=active_store_count,
    )
    old_rows = {row["key"]: row["value"] for row in version.payload["profit_loss"]["rows"]}
    changes = []
    for row in current["profit_loss"]["rows"]:
        for period in ("current", "previous", "ytd"):
            previous = old_rows.get(row["key"], {}).get(period)
            latest = row["value"].get(period)
            equal = (previous is None and latest is None) or (
                previous is not None and latest is not None and Decimal(previous) == Decimal(latest))
            if not equal:
                changes.append({"key": row["key"], "label": row["label"], "period": period,
                    "previous": previous, "current": latest,
                    "difference": Decimal(latest)-Decimal(previous) if previous is not None and latest is not None else None})
    return {"version": version.version, "changed": version.source_digest != digest(current),
            "changes": changes, "warnings": current["warnings"], "profit_layers": current["profit_layers"]}


def version_workbook(version):
    from schemas import StoreProfitLossResponse
    from routers.reports import (_build_inventory_impairment_workbook, _build_inventory_impairment_summary_sheet,
        _summarize_inventory_impairment_rows, _build_store_profit_loss_sheet, _build_product_line_profit_sheet,
        _workbook_bytes_with_formula_cache, STORE_PROFIT_LOSS_PERIODS)
    payload = version.payload
    rows = []
    decimal_fields = {"unit_cost", "base_unit_cost", "cost_adjustment_per_unit", "original_inventory_amount",
        "sold_cost_amount", "previous_book_value", "impairment_rate", "impairment_amount", "monthly_impairment_amount",
        "opening_impairment_amount", "monthly_total_impairment_amount", "monthly_writeoff_amount"}
    for raw in payload["inventory"]:
        item = dict(raw)
        item["arrived_at"] = date.fromisoformat(item["arrived_at"])
        for key in decimal_fields:
            if item.get(key) is not None:
                item[key] = Decimal(str(item[key]))
        rows.append(item)
    workbook, cache = _build_inventory_impairment_workbook(rows, date.fromisoformat(payload["report_date"]))
    report = StoreProfitLossResponse.model_validate(payload["profit_loss"])
    values = {row.key: {period: getattr(row.value, period) for period in STORE_PROFIT_LOSS_PERIODS} for row in report.rows}
    values["_warnings"] = payload["warnings"]
    _build_store_profit_loss_sheet(workbook, SimpleNamespace(**payload["store"]), version.report_month,
                                  values, {row.key: row.remark for row in report.rows})
    lines = []
    for raw in payload["product_lines"]:
        item = {**raw, "sku_ids": set(raw["sku_ids"])}
        for period in STORE_PROFIT_LOSS_PERIODS:
            item[period] = {key: Decimal(value) if value is not None else None for key, value in raw[period].items()}
        lines.append(item)
    _build_product_line_profit_sheet(workbook, lines, version.report_month)
    _build_inventory_impairment_summary_sheet(workbook, _summarize_inventory_impairment_rows(rows), date.fromisoformat(payload["report_date"]))
    sheet = workbook.create_sheet("版本及核对说明")
    sheet.append(["月份", str(version.report_month), "版本", version.version, "状态", version.status])
    for warning in payload["warnings"]:
        sheet.append(["待核对", warning])
    sheet.append(["说明", "四张单店报表来自该不可变版本；跨店汇总需分别选择各店已确认版本。"])
    for row in sheet:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    return _workbook_bytes_with_formula_cache(workbook, cache)
