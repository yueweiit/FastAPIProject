"""离线生成店铺销售映射版本DDL；不连接数据库、不导入旧全局映射。"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable, CreateIndex
from models import SalesProductMapping, SalesProductMappingComponent


def ddl():
    statements = ["-- 核验目标库、备份及已有同名结构后执行；回退代码时保留映射和历史版本。"]
    for model in (SalesProductMapping, SalesProductMappingComponent):
        statements.append(str(CreateTable(model.__table__).compile(dialect=mysql.dialect())) + ";")
        statements.extend(str(CreateIndex(index).compile(dialect=mysql.dialect())) + ";" for index in sorted(model.__table__.indexes, key=lambda i: i.name))
    return "\n".join(statements) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql", type=Path, required=True)
    target = parser.parse_args().write_sql
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ddl(), encoding="utf-8")
    print("DDL generated only; no database connection or migration executed.")
