"""成本调整、费用凭证、来源切换和结算核对；调用方负责鉴权和事务提交。"""
import hashlib
import json
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, inspect, select

from models import (
    InventoryBatch, InventoryCostAdjustment, InventoryCostAdjustmentLine, Sale, SaleCostDetail,
    Store, FinancialExpense, FinancialExpenseSource, FinancialReceipt, SettlementEntry,
    SettlementClassification, FinancialAuditEvent, SalesImportBatch, User,
)
from services.cost_layers import COST_CATEGORIES, category_adjustments, load_cost_layers, invalidate_finance_cache
from services.finance_config import financial_controls_enabled

ZERO = Decimal(0)
MONEY = Decimal("0.0001")
EXPENSE_LABELS = {
    "customs_import_tax": "关税及进口税费", "local_logistics_cost": "本地物流配送成本",
    "fulfillment_cost": "代发成本", "tax_surcharge": "税金及附加", "salary": "人员薪资",
    "advertising": "广告推广费", "platform_subscription": "平台月费/年费", "warehousing": "仓储费用",
    "delivery": "配送费", "platform_fines": "平台罚款/赔偿", "research_development": "研发费用",
    "finance_expenses": "财务费用", "credit_impairment_loss": "信用减值损失",
    "non_operating_income": "营业外收入", "non_operating_expense": "营业外支出",
    "income_tax_expense": "所得税费用", "shipping_revenue": "运费收入", "other_revenue": "其他收入",
}
BREAKDOWN_LABELS = {"commission": "平台及达人佣金", "tax": "平台税费", "advertising": "广告费用",
                    "logistics": "平台物流费用", "other": "其他结算差额"}


def fail(code, message, status=400):
    raise HTTPException(status, detail={"code": code, "message": message})


def require_enabled():
    if not financial_controls_enabled():
        fail("financial_controls_disabled", "财务增强功能尚未启用，请先核验新增结构及配置。", 503)


def today():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def month_bounds(month):
    if month.day != 1:
        fail("invalid_report_month", "核算月份必须传入当月第一天。")
    return month, date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)


def json_safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=lambda v: sorted(v) if isinstance(v, set) else str(v)))


def digest(value):
    return hashlib.sha256(json.dumps(json_safe(value), sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def money(value):
    try:
        result = Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)
        if not result.is_finite() or abs(result) >= Decimal("1000000000000"):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        fail("amount_out_of_range", "金额超出可保存范围。")


def record(row):
    return {item.key: getattr(row, item.key) for item in row.__mapper__.column_attrs}


async def audit(db, user, entity, action, *, before=None, store_id=None):
    # MySQL不通过RETURNING回填默认值；先在当前事务中完成SQL和显式异步加载，
    # 避免record()读取created_at/updated_at等过期列时触发隐式异步IO。
    await db.flush()
    unloaded = inspect(entity).unloaded.intersection(
        item.key for item in entity.__mapper__.column_attrs
    )
    if unloaded:
        await db.refresh(entity, attribute_names=sorted(unloaded))
    db.add(FinancialAuditEvent(store_id=store_id, entity_type=entity.__tablename__, entity_id=entity.id,
                              action=action, actor_id=user.id, before_values=json_safe(before or {}),
                              after_values=json_safe(record(entity))))


async def scoped_store(db, user, store_id, *, lock=False):
    if user.role != "admin" and (not user.store_id or user.store_id != store_id):
        fail("store_forbidden", "无权访问该店铺财务记录。", 403)
    stmt = select(Store).where(Store.id == store_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    store = await db.scalar(stmt)
    if store is None:
        fail("store_not_found", "店铺不存在。", 404)
    return store


def same_request(row, payload, names):
    if any(getattr(row, key) != getattr(payload, key) for key in names):
        fail("idempotency_conflict", "同一请求标识对应的内容已改变，请重新核对。", 409)


def validate_adjusted_cost(batch, category, layers, amount):
    parts = category_adjustments(layers)
    quantity = Decimal(batch.quantity)
    base = {"purchase": batch.purchase_price, "head_shipping": batch.shipping_cost / quantity,
            "last_mile": batch.last_mile_cost / quantity, "other": batch.other_cost / quantity}
    if base[category] + parts[category] + amount / quantity < 0:
        fail("negative_component_cost", "调整后该成本项目不能为负数。")
    if batch.unit_cost + sum(parts.values(), ZERO) + amount / quantity < 0:
        fail("negative_total_cost", "调整后总成本不能为负数，请核对原成本舍入尾差。")


async def prepare_cost_adjustment(db, user, payload, *, lock=False, validate_amount=True):
    require_enabled()
    payload.source_ref = payload.source_ref.strip()
    ids = [line.batch_id for line in payload.allocations]
    if len(set(ids)) != len(ids):
        fail("duplicate_batch", "同一调整单不能重复选择批次。")
    if not payload.source_ref.strip() or payload.document_date > today() or payload.rate_date > today():
        fail("invalid_cost_document", "凭证号不能为空，凭证和汇率日期不能晚于今天。")
    stmt = select(InventoryBatch).where(InventoryBatch.id.in_(ids)).order_by(
        InventoryBatch.product_id, InventoryBatch.arrived_at, InventoryBatch.id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    batches = list((await db.execute(stmt)).scalars().all())
    if len(batches) != len(ids):
        fail("batch_not_found", "选择的批次不存在。", 404)
    if user.role != "admin" and any(batch.user_id != user.id for batch in batches):
        fail("batch_forbidden", "只能调整自己创建的批次。", 403)
    layers = await load_cost_layers(db, ids, lock=lock)
    requested = {item.batch_id: item for item in payload.allocations}
    weights = {}
    for batch in batches:
        if batch.quantity <= 0:
            fail("invalid_batch_quantity", "批次数量异常，不能分摊成本。")
        weight = Decimal(batch.quantity) if payload.allocation_basis == "quantity" else requested[batch.id].weight
        if weight is None or weight <= 0:
            fail("allocation_weight_required", "非件数分摊必须为每个批次填写正数分摊依据。")
        weights[batch.id] = weight
    amount_cny = money(payload.amount * payload.exchange_rate)
    # 权重最多8位小数；整数除法避免大金额乘高精度权重时受Decimal上下文舍入影响。
    weight_units = [int(weights[batch.id] * Decimal(100_000_000)) for batch in batches]
    denominator = sum(weight_units)
    amount_units = int(abs(amount_cny) / MONEY)
    portions = [divmod(amount_units * weight, denominator) for weight in weight_units]
    units = [whole for whole, _remainder in portions]
    remaining_units = amount_units - sum(units)
    # 最大余数优先；相同余数从固定批次顺序的后端开始，保留确定性。
    ranked = sorted(range(len(batches)), key=lambda i: (portions[i][1], i), reverse=True)
    for index in ranked[:remaining_units]:
        units[index] += 1
    sign = -1 if amount_cny < 0 else 1
    lines = []
    for index, batch in enumerate(batches):
        amount = MONEY * units[index] * sign
        if validate_amount:
            validate_adjusted_cost(batch, payload.category, layers.get(batch.id, []), amount)
        lines.append({"batch_id": batch.id, "batch_no": batch.batch_no, "quantity": batch.quantity,
                      "allocation_weight": weights[batch.id], "amount_cny": amount,
                      "sold_amount_cny": money(amount * Decimal(batch.quantity-batch.remaining_quantity) / Decimal(batch.quantity)),
                      "remaining_amount_cny": money(amount * Decimal(batch.remaining_quantity) / Decimal(batch.quantity))})
        lines[-1]["remaining_amount_cny"] = amount - lines[-1]["sold_amount_cny"]
    return batches, lines, amount_cny


async def post_cost_adjustment(db, user, payload):
    require_enabled()
    existing = await db.scalar(select(InventoryCostAdjustment).where(InventoryCostAdjustment.request_key == payload.request_key))
    batches, lines, amount_cny = await prepare_cost_adjustment(db, user, payload, lock=True, validate_amount=existing is None)
    if existing:
        same_request(existing, payload, ("source_ref", "category", "allocation_basis", "amount", "currency",
                                         "exchange_rate", "rate_date", "document_date", "is_final", "note"))
        old = (await db.execute(select(InventoryCostAdjustmentLine).where(
            InventoryCostAdjustmentLine.adjustment_id == existing.id))).scalars().all()
        if sorted((x.batch_id, x.allocation_weight) for x in old) != sorted((x["batch_id"], x["allocation_weight"]) for x in lines):
            fail("idempotency_conflict", "重复请求的批次分配已改变。", 409)
        return existing
    row = InventoryCostAdjustment(**payload.model_dump(exclude={"allocations"}), amount_cny=amount_cny, created_by=user.id)
    db.add(row)
    await db.flush()
    for line in lines:
        db.add(InventoryCostAdjustmentLine(adjustment_id=row.id, **{key: line[key] for key in
               ("batch_id", "quantity", "allocation_weight", "amount_cny")}))
    await db.flush()
    await audit(db, user, row, "post")
    invalidate_finance_cache(db)
    return row


async def reverse_cost_adjustment(db, user, row_id, payload):
    require_enabled()
    row = await db.get(InventoryCostAdjustment, row_id)
    if row is None:
        fail("adjustment_not_found", "成本调整单不存在。", 404)
    lines = list((await db.execute(select(InventoryCostAdjustmentLine).where(
        InventoryCostAdjustmentLine.adjustment_id == row.id))).scalars().all())
    from schemas import CostAdjustmentRequest, CostAllocationRequest
    request = CostAdjustmentRequest(request_key=payload.request_key, source_ref=f"reversal:{row.id}",
        category=row.category, allocation_basis="weight", amount=-row.amount, currency=row.currency,
        exchange_rate=row.exchange_rate, rate_date=row.rate_date, document_date=today(),
        allocations=[CostAllocationRequest(batch_id=x.batch_id, weight=x.allocation_weight) for x in lines],
        note=payload.note)
    batches, _preview, _amount = await prepare_cost_adjustment(db, user, request, lock=True, validate_amount=False)
    original = await db.scalar(select(InventoryCostAdjustment).where(InventoryCostAdjustment.id == row_id).with_for_update())
    if original.reverses_id:
        fail("cannot_reverse_reversal", "反向单不能再次反向，请另建有凭证的补差单。", 409)
    existing = await db.scalar(select(InventoryCostAdjustment).where(InventoryCostAdjustment.reverses_id == row_id))
    if existing:
        if existing.request_key == payload.request_key and existing.note == payload.note:
            return existing
        fail("already_reversed", "该成本调整已存在反向单。", 409)
    # 历史单据可能采用旧分摊；校验实际将写入的逐笔反向金额，不能重新分摊后校验。
    layers = await load_cost_layers(db, {batch.id for batch in batches})
    saved_lines = {line.batch_id: line for line in lines}
    for batch in batches:
        validate_adjusted_cost(batch, row.category, layers.get(batch.id, []), -saved_lines[batch.id].amount_cny)
    result = InventoryCostAdjustment(**request.model_dump(exclude={"allocations"}),
                                    amount_cny=-row.amount_cny, reverses_id=row.id, created_by=user.id)
    db.add(result)
    await db.flush()
    for line in lines:
        db.add(InventoryCostAdjustmentLine(adjustment_id=result.id, batch_id=line.batch_id,
               quantity=line.quantity, allocation_weight=line.allocation_weight, amount_cny=-line.amount_cny))
    await db.flush()
    await audit(db, user, result, "reverse")
    invalidate_finance_cache(db)
    return result


async def create_expense(db, user, payload):
    require_enabled()
    await scoped_store(db, user, payload.store_id, lock=True)
    payload.source_ref = payload.source_ref.strip()
    if payload.category not in EXPENSE_LABELS or not payload.source_ref.strip() or payload.expense_date > today():
        fail("invalid_expense", "费用科目、凭证号或发生日期不正确。")
    if payload.settlement_entry_id is not None:
        entry = await db.get(SettlementEntry, payload.settlement_entry_id)
        if entry is None or entry.store_id != payload.store_id:
            fail("settlement_store_mismatch", "关联结算必须明确属于同一店铺。")
        await lock_settlement_source(db, entry)
    existing = await db.scalar(select(FinancialExpense).where(FinancialExpense.request_key == payload.request_key))
    names = tuple(key for key in type(payload).model_fields if key != "request_key")
    if existing:
        same_request(existing, payload, names)
        return existing
    row = FinancialExpense(**payload.model_dump(), amount_cny=money(payload.amount * payload.exchange_rate),
                           created_by=user.id, status="draft")
    db.add(row)
    await db.flush()
    await audit(db, user, row, "create", store_id=row.store_id)
    return row


async def post_expense(db, user, row_id):
    require_enabled()
    row = await db.get(FinancialExpense, row_id)
    if row is None:
        fail("expense_not_found", "费用记录不存在。", 404)
    await scoped_store(db, user, row.store_id, lock=True)
    if user.role != "admin":
        fail("expense_post_forbidden", "费用确认需要管理员权限。", 403)
    row = await db.scalar(select(FinancialExpense).where(FinancialExpense.id == row_id).with_for_update())
    if row.status == "posted":
        return row
    if row.status != "draft":
        fail("expense_not_draft", "只有待确认费用可以确认。", 409)
    before = record(row)
    row.status, row.posted_by, row.posted_at = "posted", user.id, utcnow()
    await audit(db, user, row, "post", before=before, store_id=row.store_id)
    return row


async def reverse_expense(db, user, row_id, payload):
    require_enabled()
    original = await db.get(FinancialExpense, row_id)
    if original is None:
        fail("expense_not_found", "费用记录不存在。", 404)
    await scoped_store(db, user, original.store_id, lock=True)
    if user.role != "admin":
        fail("expense_reverse_forbidden", "费用反向更正需要管理员权限。", 403)
    original = await db.scalar(select(FinancialExpense).where(FinancialExpense.id == row_id).with_for_update())
    if original.status != "posted" or original.reverses_id:
        fail("invalid_expense_reversal", "只能对已确认原费用创建一次反向单。", 409)
    existing = await db.scalar(select(FinancialExpense).where(FinancialExpense.reverses_id == row_id))
    if existing:
        if existing.request_key == payload.request_key and existing.note == payload.note:
            return existing
        fail("already_reversed", "该费用已有反向单。", 409)
    values = record(original)
    for key in ("id", "created_at", "posted_at"):
        values.pop(key, None)
    values.update(request_key=payload.request_key, source_ref=f"reversal:{row_id}",
                  amount=-original.amount, amount_cny=-original.amount_cny, reverses_id=row_id,
                  note=payload.note, created_by=user.id, posted_by=user.id, posted_at=utcnow())
    result = FinancialExpense(**values)
    db.add(result)
    await db.flush()
    await audit(db, user, result, "reverse", store_id=result.store_id)
    return result


async def set_expense_source(db, user, payload):
    require_enabled()
    month_bounds(payload.report_month)
    if payload.category not in EXPENSE_LABELS:
        fail("invalid_expense_category", "该科目不支持费用明细来源。")
    await scoped_store(db, user, payload.store_id, lock=True)
    row = await db.scalar(select(FinancialExpenseSource).where(
        FinancialExpenseSource.store_id == payload.store_id,
        FinancialExpenseSource.report_month == payload.report_month,
        FinancialExpenseSource.category == payload.category).with_for_update())
    before = record(row) if row else {}
    if row is None:
        row = FinancialExpenseSource(**payload.model_dump(), updated_by=user.id)
        db.add(row)
        await db.flush()
    else:
        row.mode, row.updated_by = payload.mode, user.id
    await audit(db, user, row, "source_selection", before=before, store_id=row.store_id)
    return row


async def ledger_manual_periods(db, store_id, report_month, keys, reports, monthly_defaults):
    """仅显式选ledger的月/科目覆盖人工值；重复关联平台结算的凭证只作核对。"""
    from services.financial_facts import derive_manual_periods
    previous = (report_month - timedelta(days=1)).replace(day=1)
    start = min(date(report_month.year, 1, 1), previous)
    selections = (await db.execute(select(FinancialExpenseSource).where(
        FinancialExpenseSource.store_id == store_id, FinancialExpenseSource.report_month >= start,
        FinancialExpenseSource.report_month <= report_month))).scalars().all()
    if not selections:
        return derive_manual_periods(reports, report_month, keys, monthly_defaults)
    rows = (await db.execute(select(FinancialExpense).where(
        FinancialExpense.store_id == store_id, FinancialExpense.expense_date >= start,
        FinancialExpense.expense_date < month_bounds(report_month)[1]))).scalars().all()
    by_month = {r.report_month: SimpleNamespace(report_month=r.report_month,
        manual_values=json_safe(r.manual_values or {}), report_metadata=dict(r.report_metadata or {})) for r in reports}
    unknown, warnings = {}, []
    for selected in selections:
        if selected.mode != "ledger":
            continue
        matching = [r for r in rows if r.expense_date.replace(day=1) == selected.report_month
                    and r.category == selected.category and r.settlement_entry_id is None]
        item = by_month.setdefault(selected.report_month, SimpleNamespace(report_month=selected.report_month,
                                  manual_values={}, report_metadata={}))
        item.manual_values.setdefault(selected.category, {})["current"] = str(sum(
            (r.amount_cny for r in matching if r.status == "posted"), ZERO))
        if any(r.status == "draft" for r in matching):
            for period, (lower, upper) in {"current": month_bounds(report_month),
                 "previous": month_bounds(previous), "ytd": (date(report_month.year, 1, 1), month_bounds(report_month)[1])}.items():
                if lower <= selected.report_month < upper:
                    unknown.setdefault(period, []).append(selected.category)
            warnings.append(f"{selected.report_month:%Y-%m}的{EXPENSE_LABELS[selected.category]}仍有待确认明细，相关费用及利润留空。")
    current = by_month.setdefault(report_month, SimpleNamespace(report_month=report_month,
                                 manual_values={}, report_metadata={}))
    current.report_metadata["manual_period_mode"] = "monthly"
    recalculated = derive_manual_periods(list(by_month.values()), report_month, keys, monthly_defaults)
    result = derive_manual_periods(reports, report_month, keys, monthly_defaults)
    for selected in selections:
        if selected.mode != "ledger":
            continue
        periods = (["current"] if selected.report_month == report_month else [])
        if selected.report_month == previous:
            periods.append("previous")
        if selected.report_month.year == report_month.year:
            periods.append("ytd")
        for period in periods:
            result[selected.category][period] = recalculated[selected.category][period]
    result["_unknown"], result["_warnings"] = unknown, warnings
    result["_sources"] = {row.category: row.mode for row in selections if row.report_month == report_month}
    return result


async def create_receipt(db, user, payload):
    require_enabled()
    await scoped_store(db, user, payload.store_id, lock=True)
    payload.source_ref, payload.payout_id = payload.source_ref.strip(), payload.payout_id.strip()
    if not payload.source_ref or not payload.payout_id or payload.receipt_date > today() or payload.amount == 0:
        fail("invalid_receipt", "回款凭证/付款ID不能为空，金额不能为0，日期不能晚于今天。")
    existing = await db.scalar(select(FinancialReceipt).where(FinancialReceipt.request_key == payload.request_key))
    if existing:
        same_request(existing, payload, tuple(key for key in type(payload).model_fields if key != "request_key"))
        return existing
    row = FinancialReceipt(**payload.model_dump(), amount_cny=money(payload.amount * payload.exchange_rate), created_by=user.id)
    db.add(row)
    await db.flush()
    await audit(db, user, row, "receipt", store_id=row.store_id)
    return row


async def reverse_receipt(db, user, row_id, payload):
    require_enabled()
    original = await db.get(FinancialReceipt, row_id)
    if original is None:
        fail("receipt_not_found", "回款记录不存在。", 404)
    await scoped_store(db, user, original.store_id, lock=True)
    original = await db.scalar(select(FinancialReceipt).where(FinancialReceipt.id == row_id).with_for_update())
    if original.reverses_id:
        fail("cannot_reverse_reversal", "反向记录不能再次反向。", 409)
    existing = await db.scalar(select(FinancialReceipt).where(FinancialReceipt.reverses_id == row_id))
    if existing:
        if existing.request_key == payload.request_key and existing.note == payload.note:
            return existing
        fail("already_reversed", "该回款已更正。", 409)
    values = record(original)
    for key in ("id", "created_at"):
        values.pop(key, None)
    values.update(request_key=payload.request_key, source_ref=f"reversal:{row_id}", amount=-original.amount,
                  amount_cny=-original.amount_cny, reverses_id=row_id, note=payload.note, created_by=user.id)
    row = FinancialReceipt(**values)
    db.add(row)
    await db.flush()
    await audit(db, user, row, "reverse", store_id=row.store_id)
    return row


async def lock_settlement_source(db, entry):
    """Store后锁导入头及结算行，与物理撤销串行，不能引用已撤销来源。"""
    if entry.import_batch_id is not None:
        imported = await db.scalar(select(SalesImportBatch).where(
            SalesImportBatch.id == entry.import_batch_id).with_for_update().execution_options(populate_existing=True))
        if imported is None or imported.status != "completed":
            fail("settlement_source_rolled_back", "结算来源已撤销，不能再关联财务凭证。", 409)
    current = await db.scalar(select(SettlementEntry).where(
        SettlementEntry.id == entry.id).with_for_update().execution_options(populate_existing=True))
    if current is None:
        fail("settlement_not_found", "结算来源已不存在。", 409)
    return current


async def classify_settlement(db, user, entry_id, payload):
    require_enabled()
    entry = await db.get(SettlementEntry, entry_id)
    if entry is None or entry.store_id is None:
        fail("settlement_unassigned", "结算不存在或未明确归属店铺，请先核对来源。", 404)
    await scoped_store(db, user, entry.store_id, lock=True)
    entry = await lock_settlement_source(db, entry)
    if set(payload.breakdown) - set(BREAKDOWN_LABELS):
        fail("invalid_breakdown_category", "结算分类包含未知科目。")
    from services.financial_facts import settlement_expense
    values = {key: money(value) for key, value in payload.breakdown.items()}
    expected = money(settlement_expense(entry.net_product_sales or ZERO, entry.settlement_total or ZERO, entry.transaction_type))
    if sum(values.values(), ZERO) != expected:
        fail("breakdown_does_not_reconcile", "分类合计必须等于该结算已计入的差额费用；分类不会再次扣费。")
    row = await db.scalar(select(SettlementClassification).where(
        SettlementClassification.settlement_entry_id == entry_id).with_for_update())
    before = record(row) if row else {}
    if row is None:
        row = SettlementClassification(settlement_entry_id=entry_id, breakdown=json_safe(values),
                                        note=payload.note, confirmed_by=user.id)
        db.add(row)
        await db.flush()
    else:
        row.breakdown, row.note, row.confirmed_by, row.confirmed_at = json_safe(values), payload.note, user.id, utcnow()
    await audit(db, user, row, "classify", before=before, store_id=entry.store_id)
    return row


async def reconciliation(db, store_id, report_month):
    from services.financial_facts import settlement_expense
    start, end = month_bounds(report_month)
    occurred = func.coalesce(SettlementEntry.settlement_date, SettlementEntry.order_created_at, SettlementEntry.imported_at)
    # 与损益财务事件相同的历史归属兜底；缺少直接店铺的行只读展示并提示核对。
    owner = func.coalesce(SettlementEntry.store_id, SalesImportBatch.store_id, User.store_id)
    scoped_entries = select(SettlementEntry).outerjoin(
        SalesImportBatch, SalesImportBatch.id == SettlementEntry.import_batch_id
    ).outerjoin(User, User.id == SalesImportBatch.user_id).where(owner == store_id)
    entries = list((await db.execute(scoped_entries.where(
        occurred >= datetime.combine(start, time.min),
        occurred < datetime.combine(end, time.min)).order_by(occurred, SettlementEntry.id))).scalars().all())
    classification = {row.settlement_entry_id: row for row in (await db.execute(select(SettlementClassification).where(
        SettlementClassification.settlement_entry_id.in_([entry.id for entry in entries])
    ))).scalars().all()} if entries else {}
    entry_rows = []
    component_keys = ("net_shipping", "taxes", "tax_withholding", "platform_commission", "service_fee",
        "sfp_service_fee", "per_item_fee", "affiliate_commission", "creator_commission",
        "creator_shop_ad_commission", "affiliate_shop_ad_commission", "gmv_max_ad_fee", "adjustment_amount")
    for entry in entries:
        expense = settlement_expense(entry.net_product_sales or ZERO, entry.settlement_total or ZERO, entry.transaction_type)
        classified = classification.get(entry.id)
        allocated = sum((Decimal(v) for v in classified.breakdown.values()), ZERO) if classified else ZERO
        entry_rows.append({"id": entry.id, "order_id": entry.order_id, "payout_id": entry.payout_id,
            "currency": entry.currency or "", "net_sales": entry.net_product_sales,
            "settlement_total": entry.settlement_total, "expense_difference": expense,
            "breakdown": classified.breakdown if classified else {}, "unclassified_difference": expense-allocated,
            "classification": record(classified) if classified else None,
            "can_classify": entry.store_id is not None,
            "raw_components": {key: getattr(entry, key) for key in component_keys},
            "source_file": entry.source_file, "source_row_number": entry.source_row_number})
    month_receipts = list((await db.execute(select(FinancialReceipt).where(
        FinancialReceipt.store_id == store_id, FinancialReceipt.receipt_date >= start,
        FinancialReceipt.receipt_date < end))).scalars().all())
    payouts = {entry.payout_id for entry in entries if entry.payout_id} | {row.payout_id for row in month_receipts}
    all_entries = (await db.execute(scoped_entries.where(
        SettlementEntry.payout_id.in_(payouts)))).scalars().all() if payouts else []
    all_receipts = (await db.execute(select(FinancialReceipt).where(
        FinancialReceipt.store_id == store_id, FinancialReceipt.payout_id.in_(payouts)))).scalars().all() if payouts else []
    groups = {}
    for entry in all_entries:
        key = (entry.payout_id, entry.currency or "")
        group = groups.setdefault(key, {"settlement_amount": ZERO, "received_amount": ZERO})
        group["settlement_amount"] += entry.settlement_total or ZERO
    for row in all_receipts:
        key = (row.payout_id, row.currency)
        group = groups.setdefault(key, {"settlement_amount": ZERO, "received_amount": ZERO})
        group["received_amount"] += row.amount
    warnings = []
    if any(entry.store_id is None for entry in entries):
        warnings.append("部分历史结算由导入批次/账号当前绑定推导店铺，只读展示；请先核对直接店铺归属后分类或关联费用。")
    unclassified = sum(1 for row in entry_rows if row["unclassified_difference"] != 0)
    if unclassified:
        warnings.append(f"{unclassified}条平台结算差额未完成科目分类；金额已按原差额入账，不再次计费。")
    if any(group["settlement_amount"] != group["received_amount"] for group in groups.values()):
        warnings.append("存在结算与回款差额；按付款ID全期间核对，跨月到账或银行扣费需另行解释，不自动补平。")
    if any(not entry.payout_id for entry in entries):
        warnings.append("部分结算缺少付款ID，尚不能逐笔匹配回款。")
    return {"entries": entry_rows, "payouts": [{"payout_id": payout, "currency": currency, **values,
            "difference": values["settlement_amount"]-values["received_amount"]}
            for (payout, currency), values in sorted(groups.items())],
            "receipts": [record(row) for row in month_receipts], "warnings": warnings,
            "scope_note": "结算行按所选月；付款ID核对包含该付款ID所有月份记录，按原币分别比较。"}
