import decimal
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from fastapi.responses import JSONResponse

# 加载 .env 文件
load_dotenv(Path(__file__).parent / ".env")

DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = os.getenv("MYSQL_PORT", "3306")
DB_USER = os.getenv("MYSQL_USER", "root")
DB_PASS = os.getenv("MYSQL_PASSWORD", "inventory123")
DB_NAME = os.getenv("MYSQL_DATABASE", "inventory_fifo")

DATABASE_URL = f"mysql+aiomysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}?auth_plugin=mysql_native_password"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    from services.finance_config import FINANCE_TABLES, financial_controls_enabled
    from sqlalchemy import inspect, select
    tables = [table for table in Base.metadata.sorted_tables
              if financial_controls_enabled() or table.name not in FINANCE_TABLES]
    def guard_disabled(connection):
        if financial_controls_enabled():
            return
        existing = set(inspect(connection).get_table_names())
        for name in sorted(existing & FINANCE_TABLES):
            table = Base.metadata.tables[name]
            if connection.execute(select(table.c.id).limit(1)).first() is not None:
                raise RuntimeError("已有财务增强记录，不能关闭财务开关并回退成本/费用口径；请恢复配置后启动。")
    async with engine.begin() as conn:
        await conn.run_sync(guard_disabled)
        await conn.run_sync(lambda connection: Base.metadata.create_all(connection, tables=tables))
