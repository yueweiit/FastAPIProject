"""已售批次的数量/金额更正；调用方先锁原批次并负责统一提交与日志。"""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from hashlib import sha256

from fastapi import HTTPException
from pydantic import ValidationError

from schemas import CostAdjustmentRequest, CostAllocationRequest
from services.cost_layers import COST_CATEGORIES, base_cost_components, category_adjustments
from services.financial_controls import post_cost_adjustment, today

MONEY = Decimal("0.0001")


def freeze_cost_basis(batch):
    if batch.cost_basis is not None:
        return
    parts = base_cost_components(batch)
    total = sum(parts.values(), Decimal(0))
    # 保存FIFO单价舍入后的原分配依据，尾差承接在最后一个非零科目。
    normalized = {key: Decimal(0) for key in COST_CATEGORIES}
    nonzero = [key for key in COST_CATEGORIES if parts[key]]
    if nonzero:
        for key in nonzero[:-1]:
            normalized[key] = batch.unit_cost * parts[key] / total
        normalized[nonzero[-1]] = batch.unit_cost - sum(normalized.values(), Decimal(0))
    else:
        normalized["purchase"] = batch.unit_cost
    batch.cost_basis = {"quantity": batch.quantity, "unit_cost": str(batch.unit_cost),
        "components": {key: str(value) for key, value in normalized.items()}}


async def restate_costs(db, user, batch, layers, target, edit_version, changed_categories):
    """绝对目标金额转为新增差额凭证；原批次/FIFO保存单价均不改写。"""
    base = base_cost_components(batch)
    delta = category_adjustments(layers)
    quantity = Decimal(batch.quantity)
    for key in COST_CATEGORIES:
        current = (base[key] + delta[key]) * quantity
        amount = (target[key] - current).quantize(MONEY, rounding=ROUND_HALF_UP)
        # 零金额目标不能因四位小数差额舍入而把某科目扣成微小负数。
        if current + amount < 0:
            amount = (-current).quantize(MONEY, rounding=ROUND_CEILING)
        if not amount and key not in changed_categories:
            continue
        source = f"batch-edit:{batch.id}:{edit_version}:{key}"
        request_key = sha256(source.encode()).hexdigest()
        try:
            payload = CostAdjustmentRequest(request_key=request_key, source_ref=f"batch-edit:{request_key}",
                category=key, allocation_basis="quantity", amount=amount, currency="CNY",
                rate_date=today(), document_date=today(),
                allocations=[CostAllocationRequest(batch_id=batch.id)], is_final=False,
                note=f"批次编辑更正；基于编辑版本{edit_version}；数量{batch.quantity}。")
        except ValidationError:
            raise HTTPException(422, detail={"code": "batch_cost_out_of_range",
                "message": "更正后的金额或成本差额超出字段范围，请核对入库数量和金额。"}) from None
        # 仅本批次编辑入口授权跨创建人；普通财务API仍保留原创建人权限。
        await post_cost_adjustment(db, user, payload, shared_batch_edit=True)
