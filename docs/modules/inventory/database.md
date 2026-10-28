# 入库模块数据表

本模块维护 inventory_batches、batch_change_logs 和 batch_impairment_stores；历史消耗记录的字段见[销售数据库](../sales/database.md)，月末引用见[报表数据库](../reports/database.md)。

## inventory_batches

来源：[models.py](../../../models.py) 的 `InventoryBatch`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `product_id` | `Integer` | 否 | 本地商品关联 | 索引；ForeignKey('products.id') |
| `batch_no` | `String(64)` | 否 | 批次编号 | 唯一 |
| `quantity` | `Integer` | 否 | 数量；业务含义见本表说明 | — |
| `remaining_quantity` | `Integer` | 否 | 活动批次的当前余量；作废后保留作废前历史余量，不代表可售数量 | — |
| `unit_cost` | `DECIMAL(12, 4)` | 否 | 原FIFO基准单价；有效成本另叠加调整层 | — |
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
| `voided_at` | `DateTime` | 是 | 管理员或运营作废时间，UTC无时区值；非空则不计可用库存 | 默认NULL |
| `voided_by` | `Integer` | 是 | 作废操作者ID快照 | 默认NULL，不设级联外键 |
| `void_reason` | `String(1000)` | 是 | 作废原因 | 默认NULL |
| `cost_basis` | `JSON` | 是 | 首次成本重述冻结的原quantity、unit_cost、四科目单位成本components | 默认空；仅服务端写入，后续不覆盖 |

复合唯一约束与索引（ORM 声明）：

```python
(Index('ix_inventory_batches_arrived_at_id', 'arrived_at', 'id'), Index('ix_inventory_batches_product_arrived_at', 'product_id', 'arrived_at'))
```

## 数据约束和归属

quantity为当前更正后的入库数量，remaining_quantity为当前余量；更正保留quantity−remaining_quantity及实际出库明细一致。cost_basis.quantity保留冻结时的原分摊依据，不随更正变化。数据库没有统一非负CHECK，路由/FIFO/撤销事务共同保证一致性。batch_no唯一。

作废不清零remaining_quantity，也不删除SaleCostDetail、InventoryPeriodSnapshot或InventoryCostAdjustmentLine；这些字段保留原始追溯值。当前可售库存及动态存货计算必须过滤voided_at非空的批次，避免把原余量重新出售。既有快照/月度版本保持不变。三列为可空加法迁移，历史批次默认活动；离线DDL见[add_batch_void_fields.py](../../../migrations/add_batch_void_fields.py)，兼容补列见[migrate.py](../../../migrate.py)。

user_id保存创建人，可为空；批次没有独占store_id、仓库ID、关税独立列。跌价归属另存关联表，不替代创建人或销售店铺。total_amount用于计算purchase_price；成本单号不参与计算。已售成本重述的原构成由cost_basis独立冻结，不再按最近目标金额重新分配旧FIFO成本。

## batch_impairment_stores

`BatchImpairmentStore`保存批次跌价金额的手工均分对象。batch_id和store_id组成主键，均为非空Integer，分别外键引用inventory_batches.id、stores.id；另有(store_id,batch_id)索引，InnoDB/utf8mb4。不存固定店铺数量、比例或金额，不参与FIFO。创建/编辑校验有效店铺，已有停用店铺可保留，但不允许新增停用对象；被引用店铺拒绝删除。

AssignedBatch.edit_version包含排序后店铺ID；修改/作废先锁批次、成本层和关联行，店铺校验按ID共享锁当前读。关联变更与批次日志同事务，日志记录前后ID/名称。作为整批资料更正，从入库日起重算动态报表，旧版本/快照不重写。离线DDL见[add_batch_impairment_stores.py](../../../migrations/add_batch_impairment_stores.py)，仅生成SQL，不执行或回填。新表为默认基础结构，不依赖财务增强开关，目标库部署需先核验。

sale_cost_details、inventory_period_snapshots 引用批次，不能随意改历史成本、删除批次或通过直接 UPDATE 修复库存。库存主数量与按历史销售重建的数量需勾稽，撤销会删除相关明细。依据：[batches.py](../../../routers/batches.py)、[models.py](../../../models.py)、[sales.py](../../../routers/sales.py)。

InventoryBatch.edit_version是原持久化字段的基础摘要；AdjustedBatch另纳入调整明细指纹。均是API前置条件，不是数据库列或授权凭据。新cost_basis列是可空加法迁移，无历史回填，首次受保护成本编辑才冻结；DDL见[add_batch_cost_basis.py](../../../migrations/add_batch_cost_basis.py)，migrate.py兼容补列。原FIFO单价及财务调整分配的quantity不回写；接口见[编辑并发保护](api.md#编辑并发保护)。

## batch_change_logs

库存工作台无新增表或持久化字段。opening_quantity/inbound/outbound/adjustment/closing_quantity及stock_status由批次、sales/sale_cost_details和已有日志只读派生，不写回remaining_quantity。日志用于恢复数量基数/调整，业务日期按当前记录；已撤销删除的销售不反推完整订单历史。字段与空值定义见[库存API](api.md#库存查看-api)。

来源：[models.py](../../../models.py) 的 `BatchChangeLog`；InnoDB/utf8mb4，独立于财务增强开关。应用只追加，不提供日志修改或删除接口；与批次修改同事务提交。

| 字段 | ORM 类型 | 可空 | 含义与约束 |
| --- | --- | --- | --- |
| id | Integer | 否 | 自增主键 |
| batch_id | Integer | 否 | 原批次ID，故意不设级联外键 |
| batch_no | String(64) | 否 | 当时批次编号快照 |
| action | String(16) | 否 | 稳定操作码：update或void |
| actor_id | Integer | 否 | 操作者ID快照，无用户删除级联 |
| actor_username | String(64) | 否 | 当时用户名 |
| actor_role | String(16) | 否 | 当时角色编码 |
| before_values | JSON | 否 | 修改前批次字段；金额字符串、业务日期ISO原值 |
| after_values | JSON | 否 | 数据库实际保存精度的修改后字段 |
| created_at | DateTime | 否 | 应用写入UTC无时区值；API明确标注UTC偏移 |

复合索引 `ix_batch_change_logs_batch_id_id(batch_id,id)` 服务按批次倒序分页。保存原创建人字段但不包含密码、Token或用户敏感配置；批次/账号删除后日志保留。上线前只审核/增加此表，不回填历史修改、不更新既有批次数据；离线DDL见 [add_batch_change_logs.py](../../../migrations/add_batch_change_logs.py)。
