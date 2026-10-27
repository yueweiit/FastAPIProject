"""仅离线生成批次作废字段DDL，不连接数据库或修改历史记录。"""
import argparse
from pathlib import import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql", type=Path, required=True)
    target = parser.parse_args().write_sql
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "-- 先备份并核实目标库及列是否已存在；审核后按目标环境变更流程执行。\n"
        "ALTER TABLE inventory_batches ADD COLUMN voided_at DATETIME NULL;\n"
        "ALTER TABLE inventory_batches ADD COLUMN voided_by INT NULL;\n"
        "ALTER TABLE inventory_batches ADD COLUMN void_reason VARCHAR(1000) NULL;\n",
        encoding="utf-8",
    )
    print("DDL generated only; no database connection or migration executed.")


if __name__ == "__main__":
    main()
