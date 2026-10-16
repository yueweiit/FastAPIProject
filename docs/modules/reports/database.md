# 报表与快照数据表

本模块维护自动期间、批次快照与人工损益保存。销售/FIFO/结算为[销售模块](../sales/database.md)事实来源，商品规则见[商品模块](../products/database.md)，分类见[主数据模块](../finance-masters/database.md)。

存货明细与SKU汇总为动态计算结果，不是独立持久化报表表；原始入库/累计出库数量和成本使用现有批次及销售明细。新增准备、销售转销和期末余额按已有FIFO事实逐日回放，无新增表字段、迁移或报表金额回填。各字段映射与公式见[逐字段来源](inventory-impairment-fields.md)。

## accounting_periods

来源：[models.py](../../../models.py) 的 `AccountingPeriod`（源码第 426 行，行号可能随版本变化）。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `period_start` | `Date` | 否 | 自然月开始日期 | — |
| `period_end` | `Date` | 否 | 自然月结束日期 | — |
| `timezone` | `String(64)` | 否 | 时区标识 | Python 默认：'Asia/Shanghai' |
| `status` | `String(16)` | 否 | 业务状态；见流程文档 | Python 默认：'auto_closed' |
| `snapshot_version` | `Integer` | 否 | 快照版本：0 未生成，1 已生成 | Python 默认：0 |
| `closed_at` | `DateTime` | 是 | 自动快照完成时间 | — |
| `closed_by_user_id` | `Integer` | 是 | 兼容关闭人字段；自动生成时为空 | 索引；ForeignKey('users.id') |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |
| `updated_at` | `DateTime` | 否 | 更新时间 | 数据库默认：func.now()；更新时：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('period_start', 'period_end', name='uq_accounting_periods_dates'),)
```

## inventory_period_snapshots

来源：[models.py](../../../models.py) 的 `InventoryPeriodSnapshot`（源码第 456 行，行号可能随版本变化）。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `accounting_period_id` | `Integer` | 否 | 会计期间关联 | 索引；ForeignKey('accounting_periods.id') |
| `batch_id` | `Integer` | 否 | 库存批次关联 | 索引；ForeignKey('inventory_batches.id') |
| `product_id` | `Integer` | 否 | 本地商品关联 | 索引；ForeignKey('products.id') |
| `store_id` | `Integer` | 是 | 店铺关联；具体归属见模块规则 | 索引；ForeignKey('stores.id') |
| `quantity` | `Integer` | 否 | 数量；业务含义见本表说明 | Python 默认：0 |
| `unit_cost` | `DECIMAL(12, 4)` | 否 | 单件成本 | — |
| `inventory_amount` | `DECIMAL(16, 4)` | 否 | 快照库存原值 | Python 默认：0 |
| `impairment_rate` | `DECIMAL(8, 6)` | 否 | 快照计提比率 | Python 默认：0 |
| `impairment_amount` | `DECIMAL(16, 4)` | 否 | 快照跌价准备金额 | Python 默认：0 |
| `book_value` | `DECIMAL(16, 4)` | 否 | 快照账面价值 | Python 默认：0 |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('accounting_period_id', 'batch_id', name='uq_inventory_period_snapshots_period_batch'), Index('ix_inventory_period_snapshots_period_product', 'accounting_period_id', 'product_id'))
```

## store_profit_loss_reports

来源：[models.py](../../../models.py) 的 `StoreProfitLossReport`（源码第 497 行，行号可能随版本变化）。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `store_id` | `Integer` | 否 | 店铺关联；具体归属见模块规则 | 索引；ForeignKey('stores.id') |
| `report_month` | `Date` | 否 | 核算月第一天 | — |
| `manual_values` | `JSON` | 否 | 人工损益项 JSON | Python 默认：dict |
| `remarks` | `JSON` | 否 | 报表行备注 JSON | Python 默认：dict |
| `metadata` | `JSON` | 否 | 数据库列名 metadata；保留扩展信息；ORM 属性 report_metadata | Python 默认：dict |
| `updated_by_user_id` | `Integer` | 是 | 最近保存人 | 索引；ForeignKey('users.id') |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |
| `updated_at` | `DateTime` | 否 | 更新时间 | 数据库默认：func.now()；更新时：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('store_id', 'report_month', name='uq_store_profit_loss_reports_store_month'), Index('ix_store_profit_loss_reports_month', 'report_month'))
```

## 期间与快照

(period_start,period_end) 唯一，快照 (accounting_period_id,batch_id) 唯一；期间按自然月、auto_closed 和 snapshot_version=1 生成。snapshot_version 已有值就不覆盖，自动关闭人为空。兼容字段不等于提供人工结账或期间编辑功能。

快照保存数量、单价、库存原值、跌价准备和账面值；store_id是生成时从批次创建人当前绑定推导的值。07上月比较从FIFO流水重建，不读取或补写本表；快照保留原静态余额算法，报表采用实际持有期间回放且可因后续历史变更重算，不能直接替换，详见[流程](workflow.md)。

AccountingPeriod.period_snapshots 使用 all/delete-orphan；没有公开删除期间功能。数据库有快照对批次/商品/店铺的外键，不能在业务删除时忽略它们。

## 人工损益保存与外部数据

store_profit_loss_reports以(store_id,report_month)唯一；人工金额按各科目/期间以字符串或空值放入manual_values，remarks保存备注，report_metadata对应数据库列metadata。该表保存人工输入和扩展信息，不保存全部自动计算结果/结账审计副本。默认保存整体替换JSON；页面使用current范围，在已有报表行锁内仅替换本月人工列、保留已有上月/年累计人工值，remarks仍完整替换。范围字段不是新数据库列，具体见[损益保存契约](api.md#损益保存契约)。

OA ding_approval_instance 是外部 PostgreSQL 表，不在本项目 ORM 或迁移中创建，也没有本地费用复制表。汇率事实保存在 settlement_entries，不在报表表中。依据：[models.py](../../../models.py)、[reports.py](../../../routers/reports.py)、[oa_office_expenses.py](../../../services/oa_office_expenses.py)。

兼容迁移的建表/索引是否已在目标库落实待验证，见 [migrate.py](../../../migrate.py)。
