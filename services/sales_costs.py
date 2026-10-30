"""销售展示成本与实际出库商品跌价；不改写FIFO或店铺损益分摊。"""
from decimal import Decimal

from services.inventory_provision_data import load_inventory_provision_data


async def attach_sale_impairment(db, rows):
    sales = {row.id: row for row in rows if row.record_type == "sale"}
    if not sales:
        return
    batches, _details, replay = await load_inventory_provision_data(
        db, max(row.sold_at.date() for row in sales.values()),
        product_ids={row.product_id for row in sales.values()},
    )
    by_batch = {row[0].id: row[0] for row in batches}
    amounts = {key: Decimal(0) for key in sales}
    quantities = {key: 0 for key in sales}
    notes = {key: set() for key in sales}
    for writeoff in replay.writeoffs:
        sale_id = writeoff.sale.sale_id
        if sale_id not in sales:
            continue
        quantities[sale_id] += writeoff.sale.quantity
        if replay.notes.get(writeoff.sale.batch_id):
            notes[sale_id].add(replay.notes[writeoff.sale.batch_id])
        if writeoff.units is None:
            amounts[sale_id] = None
        elif amounts[sale_id] is not None:
            amounts[sale_id] += writeoff.units * by_batch[writeoff.sale.batch_id].unit_cost
    for sale_id, row in sales.items():
        original_cost = sum(((detail.base_unit_cost or Decimal(0)) * Decimal(detail.quantity)
                             for detail in row.cost_details), Decimal(0))
        costs_known = all(detail.base_unit_cost is not None for detail in row.cost_details)
        invalid = (row.cost_status == "needs_review" or not costs_known or original_cost != row.original_total_cost
                   or quantities[sale_id] != row.quantity
                   or amounts[sale_id] is None)
        row.impairment_cny = None if invalid else amounts[sale_id]
        row.impairment_review_reason = ("出库明细缺失、作废或历史流水异常，出库商品跌价待核对" if invalid
                                        else "；".join(sorted(notes[sale_id])) or None)
