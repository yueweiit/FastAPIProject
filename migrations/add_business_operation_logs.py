"""离线生成扩展业务审计表的MySQL DDL；不连接数据库，不执行迁移。"""
import argparse
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable
from models import BusinessOperationLog


def schema_sql():
    table=BusinessOperationLog.__table__;dialect=mysql.dialect()
    statements=['-- 审核目标库及备份；MySQL DDL不可依赖事务回滚。仅新增审计表，不回填旧操作。',
        str(CreateTable(table).compile(dialect=dialect)).strip()+';']
    statements += [str(CreateIndex(index).compile(dialect=dialect))+';' for index in sorted(table.indexes,key=lambda item:item.name)]
    return '\n\n'.join(statements)+'\n'


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-sql',type=Path,required=True)
    args=parser.parse_args();args.write_sql.parent.mkdir(parents=True,exist_ok=True)
    args.write_sql.write_text(schema_sql(),encoding='utf-8')
    print('已输出扩展业务审计表DDL；未连接数据库或执行迁移。')
