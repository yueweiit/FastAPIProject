"""财务增强入口；显式启用后使用独立凭证、调整层和不可变版本。"""
from datetime import date
from functools import wraps

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import RequireAdmin, RequireAnyRole, RequireOperator
from database import get_db
from models import (FinancialAuditEvent, FinancialExpense, FinancialExpenseSource, FinancialReportVersion,
                    InventoryCostAdjustment, InventoryCostAdjustmentLine, Store, User)
from schemas import (CostAdjustmentRequest, ExpenseRequest, ExpenseSourceRequest, FinanceReversalRequest,
                     FinancialVersionRequest, ReceiptRequest, SettlementClassificationRequest)
from services.finance_config import financial_controls_enabled
from services import financial_controls as controls
from services.financial_versions import collect_payload, compare_version, save_version, version_workbook
from routers.reports import _prepare_profit_loss_oa_values

class FinancialRoute(APIRoute):
    """先转为精确金额字符串，避免FastAPI普通dict将Decimal转成float。"""
    def __init__(self, path, endpoint, **kwargs):
        @wraps(endpoint)
        async def precise_response(*args, **params):
            result = await endpoint(*args, **params)
            if isinstance(result, Response):
                return result
            return JSONResponse(controls.json_safe(result))
        super().__init__(path, precise_response, **kwargs)


router = APIRouter(prefix="/financial-control", tags=["财务增强"], route_class=FinancialRoute)


async def commit(db):
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        controls.fail("financial_source_conflict", "来源凭证、请求标识或版本发生冲突，请核对已有记录。", 409)


async def write(db, operation):
    try:
        row = await operation
        await db.commit()
        return row
    except IntegrityError:
        await db.rollback()
        controls.fail("financial_source_conflict", "来源凭证、请求标识或版本发生冲突，请核对已有记录。", 409)


@router.get("/config")
async def config(user: User = Depends(RequireAnyRole)):
    return {"enabled": financial_controls_enabled(), "expense_categories": controls.EXPENSE_LABELS,
            "breakdown_categories": controls.BREAKDOWN_LABELS, "can_post": user.role == "admin",
            "can_write": user.role in ("admin", "operator")}


@router.post("/cost-adjustments/preview")
async def preview_cost_adjustment(data: CostAdjustmentRequest, db: AsyncSession = Depends(get_db),
                                  user: User = Depends(RequireOperator)):
    _batches, lines, amount = await controls.prepare_cost_adjustment(db, user, data)
    return {"amount_cny": amount, "allocations": lines,
            "policy": "已售部分追溯原销售月，未售部分加入库存；旧确认版本不覆盖。"}


@router.post("/cost-adjustments")
async def create_cost_adjustment(data: CostAdjustmentRequest, db: AsyncSession = Depends(get_db),
                                 user: User = Depends(RequireOperator)):
    row = await write(db, controls.post_cost_adjustment(db, user, data))
    return controls.record(row)


@router.get("/cost-adjustments")
async def list_cost_adjustments(batch_id: int | None = Query(default=None, gt=0),
                                limit: int = Query(default=50, ge=1, le=200),
                                db: AsyncSession = Depends(get_db), user: User = Depends(RequireAnyRole)):
    controls.require_enabled()
    stmt = select(InventoryCostAdjustment)
    if batch_id:
        stmt = stmt.where(InventoryCostAdjustment.id.in_(select(InventoryCostAdjustmentLine.adjustment_id).where(
            InventoryCostAdjustmentLine.batch_id == batch_id)))
    headers = (await db.execute(stmt.order_by(InventoryCostAdjustment.id.desc()).limit(limit))).scalars().all()
    lines = (await db.execute(select(InventoryCostAdjustmentLine).where(
        InventoryCostAdjustmentLine.adjustment_id.in_([row.id for row in headers])))).scalars().all() if headers else []
    return [{**controls.record(row), "allocations": [controls.record(line) for line in lines if line.adjustment_id == row.id]}
            for row in headers]


@router.post("/cost-adjustments/{row_id}/reverse")
async def reverse_cost_adjustment(row_id: int, data: FinanceReversalRequest,
                                  db: AsyncSession = Depends(get_db), user: User = Depends(RequireOperator)):
    row = await write(db, controls.reverse_cost_adjustment(db, user, row_id, data))
    return controls.record(row)


@router.post("/expenses")
async def create_expense(data: ExpenseRequest, db: AsyncSession = Depends(get_db), user: User = Depends(RequireOperator)):
    row = await write(db, controls.create_expense(db, user, data))
    return controls.record(row)


@router.get("/expenses")
async def list_expenses(store_id: int, report_month: date, page: int = Query(default=1, ge=1),
                        page_size: int = Query(default=50, ge=1, le=100),
                        db: AsyncSession = Depends(get_db), user: User = Depends(RequireAnyRole)):
    controls.require_enabled()
    await controls.scoped_store(db, user, store_id)
    start, end = controls.month_bounds(report_month)
    conditions = (FinancialExpense.store_id == store_id, FinancialExpense.expense_date >= start,
                  FinancialExpense.expense_date < end)
    total = await db.scalar(select(func.count()).select_from(FinancialExpense).where(*conditions))
    rows = (await db.execute(select(FinancialExpense).where(*conditions).order_by(FinancialExpense.id.desc())
        .offset((page-1)*page_size).limit(page_size))).scalars().all()
    sources = (await db.execute(select(FinancialExpenseSource).where(
        FinancialExpenseSource.store_id == store_id, FinancialExpenseSource.report_month == start))).scalars().all()
    return {"items": [controls.record(row) for row in rows], "total": total, "page": page, "page_size": page_size,
            "sources": {row.category: row.mode for row in sources}}


@router.post("/expenses/{row_id}/post")
async def post_expense(row_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(RequireAdmin)):
    row = await write(db, controls.post_expense(db, user, row_id))
    return controls.record(row)


@router.post("/expenses/{row_id}/cancel")
async def cancel_expense(row_id: int, data: FinanceReversalRequest,
                         db: AsyncSession = Depends(get_db), user: User = Depends(RequireOperator)):
    controls.require_enabled()
    row = await db.get(FinancialExpense, row_id)
    if row is None:
        controls.fail("expense_not_found", "费用记录不存在。", 404)
    await controls.scoped_store(db, user, row.store_id, lock=True)
    row = await db.scalar(select(FinancialExpense).where(FinancialExpense.id == row_id).with_for_update())
    if row.status == "cancelled":
        return controls.record(row)
    if row.status != "draft":
        controls.fail("expense_not_draft", "已确认费用请追加反向单，不能作废。", 409)
    before = controls.record(row)
    row.status, row.note = "cancelled", data.note
    await controls.audit(db, user, row, "cancel", before=before, store_id=row.store_id)
    await commit(db)
    return controls.record(row)


@router.post("/expenses/{row_id}/reverse")
async def reverse_expense(row_id: int, data: FinanceReversalRequest,
                          db: AsyncSession = Depends(get_db), user: User = Depends(RequireAdmin)):
    row = await write(db, controls.reverse_expense(db, user, row_id, data))
    return controls.record(row)


@router.put("/expense-source")
async def select_expense_source(data: ExpenseSourceRequest, db: AsyncSession = Depends(get_db),
                                user: User = Depends(RequireOperator)):
    row = await write(db, controls.set_expense_source(db, user, data))
    return controls.record(row)


@router.get("/reconciliation")
async def get_reconciliation(store_id: int, report_month: date, db: AsyncSession = Depends(get_db),
                             user: User = Depends(RequireAnyRole)):
    controls.require_enabled()
    await controls.scoped_store(db, user, store_id)
    return await controls.reconciliation(db, store_id, report_month)


@router.put("/settlements/{entry_id}/classification")
async def classify_settlement(entry_id: int, data: SettlementClassificationRequest,
                              db: AsyncSession = Depends(get_db), user: User = Depends(RequireOperator)):
    row = await write(db, controls.classify_settlement(db, user, entry_id, data))
    return controls.record(row)


@router.post("/receipts")
async def create_receipt(data: ReceiptRequest, db: AsyncSession = Depends(get_db), user: User = Depends(RequireOperator)):
    row = await write(db, controls.create_receipt(db, user, data))
    return controls.record(row)


@router.post("/receipts/{row_id}/reverse")
async def reverse_receipt(row_id: int, data: FinanceReversalRequest,
                         db: AsyncSession = Depends(get_db), user: User = Depends(RequireOperator)):
    row = await write(db, controls.reverse_receipt(db, user, row_id, data))
    return controls.record(row)


@router.get("/overview")
async def financial_overview(store_id: int, report_month: date, db: AsyncSession = Depends(get_db),
                             user: User = Depends(RequireAnyRole)):
    controls.require_enabled()
    store = await controls.scoped_store(db, user, store_id)
    controls.month_bounds(report_month)
    if report_month > controls.today().replace(day=1):
        controls.fail("future_report", "不能查询未来核算月份。")
    active_store_count, oa_values = await _prepare_profit_loss_oa_values(db, report_month, bool(store.name))
    payload = await collect_payload(db, user, store, report_month,
                                    oa_values=oa_values, active_store_count=active_store_count)
    return {"profit_layers": payload["profit_layers"], "warnings": payload["warnings"],
            "report_date": payload["report_date"], "source_digest": controls.digest(payload)}


@router.post("/versions")
async def capture_version(data: FinancialVersionRequest, db: AsyncSession = Depends(get_db),
                           user: User = Depends(RequireOperator)):
    controls.require_enabled()
    controls.month_bounds(data.report_month)
    if data.report_month >= controls.today().replace(day=1):
        controls.fail("month_not_complete", "月度版本只能保存已结束的完整自然月。")
    await controls.scoped_store(db, user, data.store_id)
    existing = await db.scalar(select(FinancialReportVersion).where(
        FinancialReportVersion.request_key == data.request_key))
    if existing:
        if (existing.store_id != data.store_id or existing.report_month != data.report_month
                or existing.note != data.note):
            controls.fail("idempotency_conflict", "相同请求标识的店铺、月份或备注已改变。", 409)
        await db.commit()
        row = existing
    else:
        active_store_count, oa_values = await _prepare_profit_loss_oa_values(db, data.report_month, True)
        row = await write(db, save_version(db, user, data,
                                           oa_values=oa_values, active_store_count=active_store_count))
    return {key: value for key, value in controls.record(row).items() if key != "payload"}


@router.get("/versions")
async def list_versions(store_id: int, report_month: date, db: AsyncSession = Depends(get_db),
                         user: User = Depends(RequireAnyRole)):
    controls.require_enabled()
    await controls.scoped_store(db, user, store_id)
    rows = (await db.execute(select(FinancialReportVersion).where(
        FinancialReportVersion.store_id == store_id, FinancialReportVersion.report_month == report_month
    ).order_by(FinancialReportVersion.version.desc()).limit(100))).scalars().all()
    return [{key: value for key, value in controls.record(row).items() if key != "payload"} for row in rows]


async def scoped_version(db, user, version_id):
    controls.require_enabled()
    row = await db.get(FinancialReportVersion, version_id)
    if row is None:
        controls.fail("version_not_found", "版本不存在。", 404)
    await controls.scoped_store(db, user, row.store_id)
    return row


@router.get("/versions/{version_id}")
async def get_version(version_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(RequireAnyRole)):
    return controls.record(await scoped_version(db, user, version_id))


@router.get("/versions/{version_id}/compare")
async def compare_financial_version(version_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(RequireAnyRole)):
    version = await scoped_version(db, user, version_id)
    active_store_count, oa_values = await _prepare_profit_loss_oa_values(db, version.report_month, True)
    return await compare_version(db, user, version,
                                 oa_values=oa_values, active_store_count=active_store_count)


@router.get("/versions/{version_id}/export")
async def export_version(version_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(RequireAnyRole)):
    row = await scoped_version(db, user, version_id)
    return StreamingResponse(version_workbook(row), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="financial_{row.report_month}_v{row.version}.xlsx"'})


@router.get("/audit")
async def financial_audit(store_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(RequireAdmin)):
    controls.require_enabled()
    await controls.scoped_store(db, user, store_id)
    rows = (await db.execute(select(FinancialAuditEvent).where(FinancialAuditEvent.store_id == store_id)
                            .order_by(FinancialAuditEvent.id.desc()).limit(100))).scalars().all()
    return [controls.record(row) for row in rows]
