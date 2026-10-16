"""仅核实的本地测试库：默认只读检查；显式授权参数才补结构和写临时记录。"""

import argparse
import asyncio
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from database import DB_HOST, DB_PORT, DB_NAME, DB_USER, async_session, engine
from migrate import ensure_sales_order_schema
from models import Base, InventoryBatch, Product, Sale, SaleCostDetail
from services.fifo import InsufficientStockError, fifo_sell


def guard_target():
    if (DB_HOST, str(DB_PORT), DB_NAME, DB_USER) != ("127.0.0.1", "3309", "inventory_fifo_test", "fifo_local_app"):
        raise RuntimeError("Refusing access outside the dedicated local inventory test database")


async def check_schema():
    async with engine.connect() as connection:
        name, user, storage = (await connection.execute(text(
            "SELECT DATABASE(), CURRENT_USER(), @@default_storage_engine"
        ))).one()
        if name != "inventory_fifo_test" or user.split("@")[0] != "fifo_local_app" or storage.lower() != "innodb":
            raise RuntimeError("Unexpected database identity or storage engine")
        column = (await connection.execute(text(
            "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='sales' AND COLUMN_NAME='order_claim'"
        ))).scalar_one_or_none()
        unique = (await connection.execute(text(
            "SELECT MIN(NON_UNIQUE) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() "
            "AND TABLE_NAME='sales' AND INDEX_NAME='uq_sales_order_claim'"
        ))).scalar_one_or_none()
    return {"column_exists": column is not None, "unique_index": unique == 0}


async def business_fingerprint():
    tables = ("products", "inventory_batches", "sales", "sale_cost_details", "settlement_entries",
              "settlement_entry_allocations", "product_impairment_rules", "inventory_period_snapshots")
    fingerprints = {}
    async with engine.connect() as connection:
        for name in tables:
            table = Base.metadata.tables[name]
            # 新列不属于历史业务事实；使补结构前后的核对可比较。
            columns = ",".join(f"`{column.name}`" for column in table.columns if column.name != "order_claim")
            rows = (await connection.execute(text(f"SELECT {columns} FROM `{name}` ORDER BY id"))).all()
            digest = hashlib.sha256(json.dumps([list(row) for row in rows], default=str, ensure_ascii=False).encode()).hexdigest()
            fingerprints[name] = (len(rows), digest)
    return fingerprints


async def verify():
    prefix = f"GUARD-TEST-{uuid4().hex[:12]}"
    orders = [f"{prefix}-{label}" for label in ("DUP", "BUNDLE", "RETRY", "HISTORY")]
    product_ids, batch_ids = [], []
    contender = None
    try:
        async with async_session() as db:
            for number in range(3):
                product = Product(sku=f"{prefix}-{number}", name="Local sales integrity test", product_type="new", safe_stock_quantity=0)
                db.add(product)
                await db.flush()
                product_ids.append(product.id)
                batch = InventoryBatch(product_id=product.id, batch_no=f"{prefix}-{number}", quantity=100,
                                       remaining_quantity=100, purchase_price=Decimal(10), unit_cost=Decimal(10),
                                       shipping_cost=0, last_mile_cost=0, other_cost=0,
                                       arrived_at=datetime(2030 if number == 2 else 2026,1,1))
                db.add(batch)
                await db.flush()
                batch_ids.append(batch.id)
            await db.commit()

        async with async_session() as first, async_session() as second:
            await fifo_sell(first, product_ids[0], orders[0], 1, Decimal(20), commit=False)
            # 不同商品没有共同批次锁，必须由订单唯一索引阻挡。
            contender = asyncio.create_task(fifo_sell(second, product_ids[1], orders[0], 1, Decimal(20)))
            await asyncio.sleep(0.3)
            blocked = not contender.done()
            await first.commit()
            try:
                await asyncio.wait_for(contender, 10)
            except IntegrityError:
                await second.rollback()
            else:
                raise AssertionError("Concurrent duplicate order was accepted")
            assert blocked, "Unique order key did not wait for the competing transaction"

        async with async_session() as db:
            quantities = dict((await db.execute(select(InventoryBatch.product_id, InventoryBatch.remaining_quantity)
                                                .where(InventoryBatch.id.in_(batch_ids)))).all())
            assert (quantities[product_ids[0]], quantities[product_ids[1]]) == (99,100)
            assert await db.scalar(select(func.count()).select_from(Sale).where(Sale.order_no == orders[0])) == 1

        async with async_session() as db:
            await fifo_sell(db, product_ids[0], orders[1], 2, Decimal(20), commit=False)
            await fifo_sell(db, product_ids[1], orders[1], 3, Decimal(20), commit=False, claim_order=False)
            await db.commit()
            rows = (await db.execute(select(Sale).where(Sale.order_no == orders[1]))).scalars().all()
            assert len(rows) == 2 and sum(row.order_claim is not None for row in rows) == 1

        async with async_session() as db:
            await fifo_sell(db, product_ids[0], orders[2], 1, Decimal(20), commit=False)
            await db.rollback()
            await fifo_sell(db, product_ids[0], orders[2], 1, Decimal(20))
            assert await db.scalar(select(func.count()).select_from(Sale).where(Sale.order_no == orders[2])) == 1

        async with async_session() as db:
            try:
                await fifo_sell(db, product_ids[2], orders[3], 1, Decimal(20), sold_at=datetime(2026,9,30))
            except InsufficientStockError:
                await db.rollback()
            else:
                raise AssertionError("Future receipt fulfilled historical sale")
            assert await db.scalar(select(InventoryBatch.remaining_quantity).where(InventoryBatch.id == batch_ids[2])) == 100
        return {"concurrent_duplicate_rejected": True, "no_double_deduction": True,
                "bundle_details_supported": True, "failed_transaction_releases_claim": True,
                "future_receipt_rejected": True}
    finally:
        if contender is not None and not contender.done():
            contender.cancel()
            await asyncio.gather(contender, return_exceptions=True)
        # 只清理本次随机前缀对应的自建记录，不扫描或删除既有业务记录。
        async with async_session() as db:
            sale_ids = select(Sale.id).where(Sale.order_no.in_(orders))
            await db.execute(delete(SaleCostDetail).where(SaleCostDetail.sale_id.in_(sale_ids)))
            await db.execute(delete(Sale).where(Sale.order_no.in_(orders)))
            if batch_ids:
                await db.execute(delete(InventoryBatch).where(InventoryBatch.id.in_(batch_ids)))
            if product_ids:
                await db.execute(delete(Product).where(Product.id.in_(product_ids)))
            await db.commit()


async def main(apply: bool):
    guard_target()
    try:
        schema = await asyncio.wait_for(check_schema(), 5)
        if not apply:
            print(json.dumps({"mode": "read_only_schema_check", **schema}))
            return
        before = await business_fingerprint()
        async with engine.begin() as connection:
            await ensure_sales_order_schema(connection)
        result = await verify()
        assert before == await business_fingerprint(), "Existing business facts changed"
        print(json.dumps({"mode": "local_mysql_validation", **result, "existing_business_rows_unchanged": True}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-local-order-schema", action="store_true",
                        help="Only after authorization: add the local order column/index and write/clean temporary test records")
    options = parser.parse_args()
    asyncio.run(main(options.apply_local_order_schema))
