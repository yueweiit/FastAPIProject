"""离线生成非实物规则表与销售业务分类元数据DDL；不连接数据库。"""
import argparse
from pathlib import Path

DDL = """-- 先备份、停写并核验目标库；已存在的列/表须核对结构，不重复ALTER。
ALTER TABLE sales ADD COLUMN business_metadata JSON NULL;
CREATE TABLE non_inventory_sku_rules (
 id INT NOT NULL AUTO_INCREMENT,
 store_id INT NOT NULL,
 platform_sku_id VARCHAR(128) NOT NULL,
 is_active BOOL NOT NULL DEFAULT TRUE,
 reason VARCHAR(500) NOT NULL,
 created_by INT NOT NULL,
 created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY (id),
 KEY ix_non_inventory_sku_rules_store_id (store_id),
 CONSTRAINT uq_non_inventory_store_sku UNIQUE (store_id,platform_sku_id),
 FOREIGN KEY (store_id) REFERENCES stores(id),
 FOREIGN KEY (created_by) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-sql",type=Path,required=True)
    target=parser.parse_args().write_sql
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(DDL,encoding="utf-8")
    print("DDL generated only; no database connection or migration executed.")


if __name__=="__main__":
    main()
