"""离线生成导入结果快照兼容列 DDL；不连接数据库或补造历史结果。"""
import argparse
from pathlib import Path


DDL = """-- 执行前核验目标库、备份和加列锁表影响；已有同名列须核对结构，不重复 ALTER。
-- 历史 NULL 表示未保存完整结果；撤销导入和回退代码时保留快照及此列。
ALTER TABLE sales_import_batches ADD COLUMN result_snapshot JSON NULL;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql", type=Path, required=True)
    target = parser.parse_args().write_sql
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DDL, encoding="utf-8")
    print("DDL generated only; no database connection or migration executed.")


if __name__ == "__main__":
    main()
