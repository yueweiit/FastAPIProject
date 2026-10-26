# 入库模块数据表

本模块维护 inventory_batches 和 batch_change_logs；历史消耗记录的字段见[销售数据库](../sales/database.md)，月末引用见[报表数据库](../reports/database.md)。

## inventory_batches

来源：[models.py](../../../models.py) 的 `InventoryBatch`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `product_id` | `Integer` | 否 | 本地商品关联 | 索引；ForeignKey('products.id') |
| `batch_no` | `String(64)` | 否 | 批次编号 | 唯一 |
| `quantity` | `Integer` | 否 | 数量；业务含义见本表说明 | — |
| `remaining_quantity` | `Integer` | 否 | 当前剩余可用数量 | — |
| `unit_cost` | `DECIMAL(12, 4)` | 否 | 单件成本 | — |
| `purchase_price` | `DECIMAL(12, 4)` | 否 | 采购单价 | — |
| `dingtalk_order_no` | `String(64)` | 是 | 采购钉钉单号 | — |
| `total_amount` | `DECIMAL(12, 4)` | 是 | 采购总金额 | — |
| `shipping_cost` | `DECIMAL(12, 4)` | 否 | 整批头程费用 | Python 默认：0 |
| `shipping_cost_no` | `String(64)` | 是 | 头程费用单号 | — |
| `last_mile_cost` | `DECIMAL(12, 4)` | 否 | 整批尾程费用 | Python 默认：0 |
| `last_mile_cost_no` | `String(64)` | 是 | 尾程费用单号 | — |
| `other_cost` | `DECIMAL(12, 4)` | 否 | 整批其他成本 | Python 默认：0 |
| `other_cost_no` | `String(64)` | 是 | 其他费用单号 | — |
| `user_id` | `Integer` | 是 | 原始创建人；编辑者另存日志 | 索引；ForeignKey('users.id') |
| `shipping_date` | `DateTime` | 是 | 发货时间 | — |
| `arrived_at` | `DateTime` | 否 | 到货时间 | — |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(Index('ix_inventory_batches_arrived_at_id', 'arrived_at', 'id'), Index('ix_inventory_batches_product_arrived_at', 'product_id', 'arrived_at'))
```

## 数据约束和归属

quantity 为原始入库数量，remaining_quantity 为当前可用数量；数据库没有统一非负 CHECK，路由校验和 FIFO/撤销事务共同保证一致性。batch_no 唯一，按到货时间、ID 及商品/到货时间建索引。

user_id 保存创建人，可为空；没有 store_id、仓库 ID、关税独立列。total_amount 的 ORM 注释“仅留档”滞后于实际逻辑，create/update 会据此计算 purchase_price。成本单号不参与计算。

sale_cost_details、inventory_period_snapshots 引用批次，不能随意改历史成本、删除批次或通过直接 UPDATE 修复库存。库存主数量与按历史销售重建的数量需勾稽，撤销会删除相关明细。依据：[batches.py](../../../routers/batches.py)、[models.py](../../../models.py)、[sales.py](../../../routers/sales.py)。

`InventoryBatch.edit_version`是所有原始持久化列规范序列化后的SHA-256内容摘要，仅作为API编辑前置条件，不是数据库列或授权凭据。Decimal统一数值表示，日期保留微秒，财务调整展示仍委托原批次计算；不新增列或回填历史值。行锁内刷新并比较，防止旧表单覆盖中途修改；接口规则见[编辑并发保护](api.md#编辑并发保护)。

## batch_change_logs

来源：[models.py](../../../models.py) 的 `BatchChangeLog`；InnoDB/utf8mb4，独立于财务增强开关。应用只追加，不提供日志修改或删除接口；与批次修改同事务提交。

| 字段 | ORM 类型 | 可空 | 含义与约束 |
| --- | --- | --- | --- |
| id | Integer | 否 | 自增主键 |
| batch_id | Integer | 否 | 原批次ID，故意不设级联外键 |
| batch_no | String(64) | 否 | 当时批次编号快照 |
| action | String(16) | 否 | 稳定操作码，当前为update |
| actor_id | Integer | 否 | 操作者ID快照，无用户删除级联 |
| actor_username | String(64) | 否 | 当时用户名 |
| actor_role | String(16) | 否 | 当时角色编码 |
| before_values | JSON | 否 | 修改前批次字段；金额字符串、业务日期ISO原值 |
| after_values | JSON | 否 | 数据库实际保存精度的修改后字段 |
| created_at | DateTime | 否 | 应用写入UTC无时区值；API明确标注UTC偏移 |

复合索引 `ix_batch_change_logs_batch_id_id(batch_id,id)` 服务按批次倒序分页。保存原创建人字段但不包含密码、Token或用户敏感配置；批次/账号删除后日志保留。上线前只审核/增加此表，不回填历史修改、不更新既有批次数据；离线DDL见 [add_batch_change_logs.py](../../../migrations/add_batch_change_logs.py)。
