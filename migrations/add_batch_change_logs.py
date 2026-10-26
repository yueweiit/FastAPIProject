"""离线生成批次修改日志表的 MySQL DDL；不连接数据库，不执行迁移。"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable
from models import BatchChangeLog


def schema_sql():
    table = BatchChangeLog.__table__
    dialect = mysql.dialect()
    statements = ["-- 先备份并审核DDL；确认目标库、InnoDB和权限后另行执行，MySQL DDL不可依赖事务回滚。",
                  str(CreateTable(table).compile(dialect=dialect)).strip() + ";"]
    statements.extend(str(CreateIndex(index).compile(dialect=dialect)) + ";"
                      for index in sorted(table.indexes, key=lambda item: item.name))
    return "\n\n".join(statements) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql", type=Path, required=True, help="离线DDL输出位置")
    args = parser.parse_args()
    args.write_sql.parent.mkdir(parents=True, exist_ok=True)
    args.write_sql.write_text(schema_sql(), encoding="utf-8")
    print("已输出批次修改日志表DDL；未连接数据库或执行迁移。")


if __name__ == "__main__":
    main()
