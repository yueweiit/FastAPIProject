"""只生成盘点记录表DDL；不连接数据库、不执行迁移或历史回填。"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable
from models import InventoryStockCount


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-sql',type=Path,required=True)
    target=parser.parse_args().write_sql
    table=InventoryStockCount.__table__
    statements=[str(CreateTable(table).compile(dialect=mysql.dialect()))]
    statements += [str(CreateIndex(index).compile(dialect=mysql.dialect())) for index in sorted(table.indexes,key=lambda i:i.name)]
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text('-- 核验环境、父表并按授权变更流程执行；无历史回填。\n'+';\n'.join(statements)+';\n',encoding='utf-8')
    print('DDL generated only; no database connection or migration executed.')


if __name__=='__main__': main()
