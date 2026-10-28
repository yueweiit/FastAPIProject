"""仅生成批次跌价归属关联表DDL；不连接数据库、不回填历史批次。"""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql", type=Path, required=True)
    target = parser.parse_args().write_sql
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "-- 备份并核验目标库、父表引擎/字段、既有表结构后，按授权变更流程执行。\n"
        "CREATE TABLE batch_impairment_stores (\n"
        "  batch_id INT NOT NULL, store_id INT NOT NULL,\n"
        "  PRIMARY KEY (batch_id, store_id),\n"
        "  KEY ix_batch_impairment_stores_store_batch (store_id, batch_id),\n"
        "  FOREIGN KEY (batch_id) REFERENCES inventory_batches(id),\n"
        "  FOREIGN KEY (store_id) REFERENCES stores(id)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n", encoding="utf-8")
    print("DDL generated only; no database connection or migration executed.")


if __name__ == "__main__":
    main()
