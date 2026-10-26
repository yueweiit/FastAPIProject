"""仅离线生成批次原成本依据列DDL，不连接数据库或回填历史数据。"""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql", type=Path, required=True)
    target = parser.parse_args().write_sql
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("-- 先备份并核实目标库及列是否已存在，审核后单独执行。\n"
                      "ALTER TABLE inventory_batches ADD COLUMN cost_basis JSON NULL;\n", encoding="utf-8")
    print("DDL generated only; no database connection or migration executed.")


if __name__ == "__main__":
    main()
