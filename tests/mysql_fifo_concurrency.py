"""Initialize the local database and verify FIFO locking against MySQL."""

import asyncio
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, select, text

import models  # Register every ORM table before create_all.
from auth import seed_users
from database import DB_HOST, DB_NAME, DB_PORT, async_session, engine, init_db
from migrate import migrate
from models import InventoryBatch, Product, Sale, SaleCostDetail
from services.fifo import InsufficientStockError, fifo_sell


async def main() -> None:
    if (DB_HOST, DB_PORT, DB_NAME) != ("127.0.0.1", "3308", "inventory_fifo"):
        raise RuntimeError("Refusing to write outside the dedicated local MySQL test database")

    await init_db()
    await migrate()
    await seed_users()

    async with async_session() as db:
        version, storage_engine = (
            await db.execute(text("SELECT VERSION(), @@default_storage_engine"))
        ).one()
        table_count = (
            await db.execute(
                text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()")
            )
        ).scalar_one()
    print(f"Local MySQL {version}, engine {storage_engine}, tables {table_count}")

    suffix = uuid4().hex[:12]
    orders = (f"FIFO-CONCURRENCY-{suffix}-A", f"FIFO-CONCURRENCY-{suffix}-B")
    async with async_session() as db:
        product = Product(sku=f"FIFO-CONCURRENCY-{suffix}", name="FIFO concurrency check")
        db.add(product)
        await db.flush()
        batch = InventoryBatch(
            product_id=product.id,
            batch_no=f"FIFO-CONCURRENCY-{suffix}",
            quantity=1,
            remaining_quantity=1,
            unit_cost=Decimal("10"),
            purchase_price=Decimal("10"),
            shipping_cost=Decimal("0"),
            last_mile_cost=Decimal("0"),
            other_cost=Decimal("0"),
            arrived_at=datetime(2026, 1, 1),
        )
        db.add(batch)
        await db.commit()
        product_id, batch_id = product.id, batch.id

    contender = None
    try:
        async with async_session() as first_db, async_session() as second_db:
            await fifo_sell(
                first_db,
                product_id=product_id,
                order_no=orders[0],
                quantity=1,
                selling_price=Decimal("20"),
                commit=False,
            )
            contender = asyncio.create_task(
                fifo_sell(
                    second_db,
                    product_id=product_id,
                    order_no=orders[1],
                    quantity=1,
                    selling_price=Decimal("20"),
                )
            )
            await asyncio.sleep(0.3)
            blocked_until_commit = not contender.done()
            await first_db.commit()
            try:
                await asyncio.wait_for(contender, timeout=10)
                second_outcome = "sold"
            except InsufficientStockError:
                second_outcome = "insufficient stock"
                await second_db.rollback()

        async with async_session() as db:
            remaining = await db.scalar(
                select(InventoryBatch.remaining_quantity).where(InventoryBatch.id == batch_id)
            )
            sale_count = await db.scalar(
                select(func.count()).select_from(Sale).where(Sale.order_no.in_(orders))
            )
            detail_count = await db.scalar(
                select(func.count()).select_from(SaleCostDetail).where(SaleCostDetail.batch_id == batch_id)
            )

        assert blocked_until_commit, "Second sale did not wait for the first transaction"
        assert second_outcome == "insufficient stock", second_outcome
        assert (remaining, sale_count, detail_count) == (0, 1, 1)
        print("MySQL FIFO concurrency check passed: one sale, one stock rejection, no oversell")
    finally:
        if contender is not None and not contender.done():
            contender.cancel()
            try:
                await contender
            except asyncio.CancelledError:
                pass
        async with async_session() as db:
            await db.execute(delete(SaleCostDetail).where(SaleCostDetail.batch_id == batch_id))
            await db.execute(delete(Sale).where(Sale.order_no.in_(orders)))
            await db.execute(delete(InventoryBatch).where(InventoryBatch.id == batch_id))
            await db.execute(delete(Product).where(Product.id == product_id))
            await db.commit()


async def run() -> None:
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
