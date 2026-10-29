"""仅生成批次归属生效历史DDL；不连接数据库，不修改或推定历史归属。"""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql", type=Path, required=True)
    target = parser.parse_args().write_sql
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "-- 核验目标库及父表，备份后按授权变更流程执行；先于新版服务上线。\n"
        "CREATE TABLE batch_impairment_rules (\n"
        "  id INT NOT NULL AUTO_INCREMENT, batch_id INT NOT NULL,\n"
        "  effective_date DATE NOT NULL, is_explicit BOOL NOT NULL DEFAULT TRUE,\n"
        "  PRIMARY KEY (id), KEY ix_batch_impairment_rules_batch_id (batch_id),\n"
        "  CONSTRAINT uq_batch_impairment_rule_date UNIQUE (batch_id, effective_date),\n"
        "  FOREIGN KEY (batch_id) REFERENCES inventory_batches(id)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"
        "CREATE TABLE batch_impairment_rule_stores (\n"
        "  rule_id INT NOT NULL, store_id INT NOT NULL,\n"
        "  PRIMARY KEY (rule_id, store_id),\n"
        "  KEY ix_batch_impairment_rule_stores_store (store_id, rule_id),\n"
        "  FOREIGN KEY (rule_id) REFERENCES batch_impairment_rules(id),\n"
        "  FOREIGN KEY (store_id) REFERENCES stores(id)\n"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n", encoding="utf-8")
    print("DDL generated only; no database connection or migration executed.")


if __name__ == "__main__":
    main()
