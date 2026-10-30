"""共享持有期间跌价事实读取；按商品限定计算范围，店铺权限由调用方输出过滤。"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

from sqlalchemy import func, or_, select
from models import InventoryBatch, Product, ProductImpairmentRule, Sale, SaleCostDetail, Store, User
from services.cost_layers import AdjustedBatch, load_cost_layers, unit_adjustment
from services.batch_store_allocations import AssignedBatch, load_batch_store_allocations
from services.batch_impairment_history import load_rules
from services.inventory_impairment import ImpairmentRule, InventoryLayer
from services.inventory_provision_movements import ProvisionSale, replay_inventory_provisions


async def load_inventory_provision_data(db, report_date: date, *, product_ids=None):
    """一次读取全局事实分配共享安全库存，再由调用方按现有权限过滤。"""
    cache = getattr(db, "info", None)
    scope = tuple(sorted(set(product_ids))) if product_ids is not None else None
    key = ("holding_inventory_provisions", report_date) if scope is None else ("holding_inventory_provisions", report_date, scope)
    if isinstance(cache, dict) and key in cache:
        return cache[key]
    cutoff = datetime.combine(report_date + timedelta(days=1), time.min)
    sold_batch_ids = select(SaleCostDetail.batch_id).join(Sale, Sale.id == SaleCostDetail.sale_id).where(Sale.sold_at < cutoff)
    batch_rows = (await db.execute(
        select(InventoryBatch, Product.sku, Product.name.label("product_name"),
               Store.name.label("store_name"), Product.product_type, Product.safe_stock_quantity, User.store_id)
        .join(Product, Product.id == InventoryBatch.product_id)
        .outerjoin(User, User.id == InventoryBatch.user_id)
        .outerjoin(Store, Store.id == User.store_id)
        .where(or_(InventoryBatch.arrived_at < cutoff, InventoryBatch.id.in_(sold_batch_ids)),
               InventoryBatch.voided_at.is_(None),
               *([InventoryBatch.product_id.in_(scope)] if scope is not None else []))
        .order_by(Store.name, Product.sku, InventoryBatch.arrived_at, InventoryBatch.id)
    )).all()
    cost_layers = await load_cost_layers(db, {row[0].id for row in batch_rows})
    if cost_layers:
        batch_rows = [(AdjustedBatch(row[0], cost_layers.get(row[0].id, [])), *row[1:]) for row in batch_rows]
    product_ids = {row[0].product_id for row in batch_rows}
    rule_rows = (await db.execute(select(ProductImpairmentRule).where(
        ProductImpairmentRule.product_id.in_(product_ids)
    ))).scalars().all() if product_ids else []
    rules = defaultdict(list)
    for rule in rule_rows:
        rules[rule.product_id].append(ImpairmentRule(rule.product_type, rule.safe_stock_quantity, rule.effective_date))
    batch_ids = [row[0].id for row in batch_rows]
    sale_rows = (await db.execute(
        select(SaleCostDetail.id.label("detail_id"), SaleCostDetail.sale_id,
               SaleCostDetail.batch_id, SaleCostDetail.quantity.label("report_quantity"),
               SaleCostDetail.unit_cost.label("saved_unit_cost"), Sale.sold_at,
               func.coalesce(Sale.store_id, User.store_id).label("sale_store_id"))
        .join(Sale, Sale.id == SaleCostDetail.sale_id)
        .outerjoin(User, User.id == Sale.user_id)
        .where(SaleCostDetail.batch_id.in_(batch_ids), Sale.sold_at < cutoff)
        .order_by(Sale.sold_at, Sale.id, SaleCostDetail.id)
    )).all() if batch_ids else []
    if cost_layers:
        sale_rows = [SimpleNamespace(**{**dict(row._mapping),
            "saved_unit_cost": row.saved_unit_cost + unit_adjustment(cost_layers.get(row.batch_id, []))})
            for row in sale_rows]
    assignments = await load_batch_store_allocations(db, batch_ids)
    history = await load_rules(db, batch_ids)
    batch_rows = [(AssignedBatch(row[0], assignments.get(row[0].id, []), history.get(row[0].id, []),
                   [{"id": row[6], "name": row[3] or "未关联店铺"}] if row[6] is not None else []), *row[1:])
                  for row in batch_rows]
    layers = [InventoryLayer(batch.id, batch.product_id, owner, batch.arrived_at.date(),
                             batch.quantity, kind, safe, tuple(rules[batch.product_id]))
              for batch, _sku, _name, _store, kind, safe, owner in batch_rows]
    sales = [ProvisionSale(row.detail_id, row.sale_id, row.batch_id, row.sold_at,
                           int(row.report_quantity), row.sale_store_id) for row in sale_rows]
    previous_end = (report_date.replace(day=1) - timedelta(days=1))
    balance_dates = [previous_end, (previous_end.replace(day=1) - timedelta(days=1)), date(report_date.year - 1, 12, 31)]
    boundary_dates = {row[0].id: [rule.effective_date - timedelta(days=1)
                      for rule in row[0].allocation_rules[1:] if rule.effective_date > date.min]
                      for row in batch_rows if row[0].allocation_rules}
    replay = replay_inventory_provisions(layers, sales, report_date, balance_dates=balance_dates,
                                         batch_balance_dates=boundary_dates)
    result = (batch_rows, sale_rows, replay)
    if isinstance(cache, dict):
        cache[key] = result
    return result
