"""新增八张财务表：默认只读核验；DDL输出不连接数据库；写入只允许指定隔离库。"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable
from database import Base, engine
import models  # 注册模型，不运行应用生命周期
from services.finance_config import FINANCE_TABLES


def finance_tables():
    return [table for table in Base.metadata.sorted_tables if table.name in FINANCE_TABLES]


def schema_sql():
    dialect = mysql.dialect()
    statements = ["-- 先备份、核对父表为InnoDB并审核DDL；MySQL DDL不能依赖事务回滚。"]
    for table in finance_tables():
        statements.append(str(CreateTable(table).compile(dialect=dialect)).strip()+";")
        statements.extend(str(CreateIndex(index).compile(dialect=dialect))+";" for index in sorted(table.indexes, key=lambda i:i.name))
    return "\n\n".join(statements)+"\n"


def verified_local_target():
    target = engine.url
    return (target.host == "127.0.0.1" and target.port == 3309 and
            target.database == "inventory_fifo_test" and target.username == "fifo_local_app")


def inspect_schema(connection):
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    required_parents = {fk.column.table.name for table in finance_tables() for fk in table.foreign_keys} - FINANCE_TABLES
    problems = []
    isolation = connection.get_isolation_level()
    if isolation != "REPEATABLE READ":
        problems.append("保存月度版本要求REPEATABLE READ，请核验会话/服务器隔离级别")
    for name in sorted((FINANCE_TABLES | required_parents) & existing):
        options = inspector.get_table_options(name)
        if str(options.get("mysql_engine", "")).lower() != "innodb":
            problems.append(f"{name}: 不是InnoDB")
        if name in FINANCE_TABLES:
            table = Base.metadata.tables[name]
            actual = {column["name"]: column for column in inspector.get_columns(name)}
            if set(actual) != set(table.columns.keys()):
                problems.append(f"{name}: 字段清单不一致")
            for column in table.columns:
                if column.name in actual and bool(actual[column.name]["nullable"]) != column.nullable:
                    problems.append(f"{name}.{column.name}: 可空性不一致")
            unique_actual = {tuple(row["column_names"]) for row in inspector.get_unique_constraints(name)}
            unique_expected = {tuple(c.name for c in constraint.columns) for constraint in table.constraints
                               if constraint.__class__.__name__ == "UniqueConstraint"}
            if not unique_expected <= unique_actual:
                problems.append(f"{name}: 缺少唯一约束")
            fk_actual = {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                         for fk in inspector.get_foreign_keys(name)}
            fk_expected = {((fk.parent.name,), fk.column.table.name, (fk.column.name,)) for fk in table.foreign_keys}
            if not fk_expected <= fk_actual:
                problems.append(f"{name}: 缺少外键")
    return {"missing_parents": sorted(required_parents-existing), "missing_finance_tables": sorted(FINANCE_TABLES-existing),
            "problems": problems, "table_count": len(existing), "isolation": isolation}


async def run(apply):
    if not verified_local_target():
        raise RuntimeError("target_rejected")
    async with engine.begin() as connection:
        before = await connection.run_sync(inspect_schema)
        if apply:
            if before["missing_parents"] or before["problems"]:
                raise RuntimeError("schema_requires_review")
            await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=finance_tables()))
        result = await connection.run_sync(inspect_schema) if apply else before
    await engine.dispose()
    print(json.dumps({"mode": "apply-local" if apply else "read-only", **result}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql", type=Path, help="离线输出供审核的MySQL DDL，不连接数据库")
    parser.add_argument("--apply-local-finance-schema", action="store_true", help="只在指定隔离库增加八张表，不初始化/修复业务记录")
    args = parser.parse_args()
    if args.write_sql:
        if args.apply_local_finance_schema:
            parser.error("DDL输出与实际执行不能同时指定")
        args.write_sql.parent.mkdir(parents=True, exist_ok=True)
        args.write_sql.write_text(schema_sql(), encoding="utf-8")
        print("已输出8张新增表的DDL；未连接数据库。")
        return
    try:
        asyncio.run(run(args.apply_local_finance_schema))
    except Exception as exc:
        # 不打印驱动错误正文，避免连接串和凭证进入日志。
        print(f"未完成：{type(exc).__name__}；请核验隔离目标、连接及父表结构。", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
