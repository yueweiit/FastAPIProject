"""离线生成商品规格兼容列 DDL；不连接数据库或回填历史商品。"""

import argparse
from pathlib import Path


DDL = """-- 执行前核验目标库、备份及锁表影响；已有同名列时核对结构，不重复 ALTER。
-- 历史商品保留 NULL；新建及编辑必填由服务端校验。回退代码时保留此列和已填规格。
ALTER TABLE products ADD COLUMN specification VARCHAR(255) NULL;
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
