import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

# Allow `python migrations/<file>.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import engine


SOURCE_USERNAME = "黄丽瑶"
TARGET_USERNAME = "Navi"


async def get_user(connection, username: str) -> dict:
    result = await connection.execute(
        text("SELECT id, username, role FROM users WHERE username = :username"),
        {"username": username},
    )
    user = result.mappings().one_or_none()
    if user is None:
        raise RuntimeError(f"User not found: {username}")
    return dict(user)


async def migrate() -> None:
    try:
        async with engine.begin() as connection:
            source_user = await get_user(connection, SOURCE_USERNAME)
            target_user = await get_user(connection, TARGET_USERNAME)
            if target_user["role"] != "operator":
                raise RuntimeError(
                    f"Target user must be an operator, got role: {target_user['role']}"
                )

            result = await connection.execute(
                text(
                    "SELECT COUNT(*) FROM inventory_batches WHERE user_id = :source_user_id"
                ),
                {"source_user_id": source_user["id"]},
            )
            batch_count = result.scalar_one()
            if batch_count == 0:
                print(f"No batches owned by {SOURCE_USERNAME}; nothing to transfer.")
                return

            update_result = await connection.execute(
                text(
                    "UPDATE inventory_batches "
                    "SET user_id = :target_user_id "
                    "WHERE user_id = :source_user_id"
                ),
                {
                    "source_user_id": source_user["id"],
                    "target_user_id": target_user["id"],
                },
            )
            print(
                f"Transferred {update_result.rowcount} inventory batches "
                f"from {SOURCE_USERNAME} (id={source_user['id']}) "
                f"to {TARGET_USERNAME} (id={target_user['id']})."
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
