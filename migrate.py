import asyncio

from sqlalchemy import text

from database import engine


MIGRATIONS = (
    ("inventory_batches", "last_mile_cost", "DECIMAL(12,4) NOT NULL DEFAULT 0"),
    ("inventory_batches", "shipping_date", "DATETIME NULL"),
    ("inventory_batches", "user_id", "INT NULL"),
    ("inventory_batches", "dingtalk_order_no", "VARCHAR(64) NULL"),
    ("inventory_batches", "total_amount", "DECIMAL(12,4) NULL"),
    ("inventory_batches", "shipping_cost_no", "VARCHAR(64) NULL"),
    ("inventory_batches", "last_mile_cost_no", "VARCHAR(64) NULL"),
    ("inventory_batches", "other_cost_no", "VARCHAR(64) NULL"),
    ("sales", "user_id", "INT NULL"),
    ("products", "image", "VARCHAR(500) NULL"),
)

INDEX_MIGRATIONS = (
    ("inventory_batches", "ix_inventory_batches_arrived_at_id", "arrived_at, id"),
    (
        "inventory_batches",
        "ix_inventory_batches_product_arrived_at",
        "product_id, arrived_at",
    ),
    ("sales", "ix_sales_sold_at", "sold_at"),
    ("sales", "ix_sales_user_sold_at", "user_id, sold_at"),
)


async def _column_exists(connection, table_name: str, column_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND COLUMN_NAME = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return result.scalar_one_or_none() is not None


async def _index_exists(connection, table_name: str, index_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = :table_name
              AND INDEX_NAME = :index_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "index_name": index_name},
    )
    return result.scalar_one_or_none() is not None


async def migrate() -> None:
    """Add known backwards-compatible columns before serving requests."""
    async with engine.begin() as connection:
        for table_name, column_name, definition in MIGRATIONS:
            if await _column_exists(connection, table_name, column_name):
                continue
            await connection.execute(
                text(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {definition}")
            )
            print(f"Added {table_name}.{column_name}")

        for table_name, index_name, columns in INDEX_MIGRATIONS:
            if await _index_exists(connection, table_name, index_name):
                continue
            await connection.execute(
                text(f"CREATE INDEX `{index_name}` ON `{table_name}` ({columns})")
            )
            print(f"Added index {table_name}.{index_name}")

        await connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(64) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(16) DEFAULT 'operator',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )


if __name__ == "__main__":
    async def run_migration() -> None:
        try:
            await migrate()
        finally:
            await engine.dispose()

    asyncio.run(run_migration())
