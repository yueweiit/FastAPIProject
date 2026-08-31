import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

# Allow `python migrations/<file>.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import engine


NEW_COLUMNS = {
    "dingtalk_order_no": "VARCHAR(64) NULL",
    "total_amount": "DECIMAL(12,4) NULL",
    "shipping_cost_no": "VARCHAR(64) NULL",
    "last_mile_cost_no": "VARCHAR(64) NULL",
    "other_cost_no": "VARCHAR(64) NULL",
}


async def column_exists(connection, column_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'inventory_batches'
              AND COLUMN_NAME = :column_name
            """
        ),
        {"column_name": column_name},
    )
    return result.scalar_one_or_none() is not None


async def migrate() -> None:
    async with engine.begin() as connection:
        for column_name, definition in NEW_COLUMNS.items():
            if await column_exists(connection, column_name):
                print(f"Skip existing column: {column_name}")
                continue
            await connection.execute(
                text(f"ALTER TABLE inventory_batches ADD COLUMN {column_name} {definition}")
            )
            print(f"Added column: {column_name}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
