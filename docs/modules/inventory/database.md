# 入库模块数据表

本模块维护 inventory_batches；历史消耗记录的字段见[销售数据库](../sales/database.md)，月末引用见[报表数据库](../reports/database.md)。

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
| `user_id` | `Integer` | 是 | 业务创建人/操作人关联 | 索引；ForeignKey('users.id') |
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
