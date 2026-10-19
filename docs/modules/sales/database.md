# 销售与结算数据表

本模块维护销售、FIFO 明细、文件导入、原始结算和组件分摊。字段类型/默认基于 ORM，实际数据库约束需核验。通用关系见[数据库总览](../../knowledge/database.md)。

## sales

来源：[models.py](../../../models.py) 的 `Sale`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `product_id` | `Integer` | 否 | 本地商品关联 | 索引；ForeignKey('products.id') |
| `user_id` | `Integer` | 是 | 业务创建人/操作人关联 | 索引；ForeignKey('users.id') |
| `store_id` | `Integer` | 是 | 店铺关联；具体归属见模块规则 | 索引；ForeignKey('stores.id') |
| `order_no` | `String(64)` | 否 | 销售订单标识 | 索引 |
| `order_claim` | `String(64)` | 是 | 新订单首条明细的全局占位；等于order_no，组合后续明细/历史记录为NULL | 唯一约束uq_sales_order_claim |
| `quantity` | `Integer` | 否 | 数量；业务含义见本表说明 | — |
| `selling_price` | `DECIMAL(12, 4)` | 否 | 销售单价 | — |
| `total_cost` | `DECIMAL(12, 4)` | 否 | FIFO 销售总成本 | — |
| `platform_fee` | `DECIMAL(12, 4)` | 否 | 平台/其他费用金额 | Python 默认：0 |
| `profit` | `DECIMAL(12, 4)` | 否 | 持久化利润值 | — |
| `sold_at` | `DateTime` | 否 | 销售发生时间 | — |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(Index('ix_sales_sold_at', 'sold_at'), Index('ix_sales_user_sold_at', 'user_id', 'sold_at'), UniqueConstraint('order_claim', name='uq_sales_order_claim'))
```

## sale_cost_details

来源：[models.py](../../../models.py) 的 `SaleCostDetail`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `sale_id` | `Integer` | 否 | 销售关联 | 索引；ForeignKey('sales.id') |
| `batch_id` | `Integer` | 否 | 库存批次关联 | 索引；ForeignKey('inventory_batches.id') |
| `quantity` | `Integer` | 否 | 数量；业务含义见本表说明 | — |
| `unit_cost` | `DECIMAL(12, 4)` | 否 | 单件成本 | — |

## sales_import_batches

来源：[models.py](../../../models.py) 的 `SalesImportBatch`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `user_id` | `Integer` | 是 | 业务创建人/操作人关联 | 索引；ForeignKey('users.id') |
| `store_id` | `Integer` | 是 | 店铺关联；具体归属见模块规则 | 索引；ForeignKey('stores.id') |
| `file_name` | `String(255)` | 否 | 来源文件名 | — |
| `file_type` | `String(32)` | 否 | order_detail 或 legacy_csv | — |
| `sheet_name` | `String(64)` | 是 | 来源工作表 | — |
| `total_rows` | `Integer` | 否 | 文件数据行数 | Python 默认：0 |
| `settlement_rows` | `Integer` | 否 | 保存的结算行数 | Python 默认：0 |
| `sales_created` | `Integer` | 否 | 生成销售数量 | Python 默认：0 |
| `skipped_duplicates` | `Integer` | 否 | 跳过的已导入事件数 | Python 默认：0 |
| `pending_confirmation` | `Integer` | 否 | 待确认行数 | Python 默认：0 |
| `status` | `String(24)` | 否 | 业务状态；见流程文档 | 索引；Python 默认：'completed' |
| `imported_at` | `DateTime` | 否 | 导入时间 | 数据库默认：func.now() |
| `rolled_back_at` | `DateTime` | 是 | 撤销时间 | — |
| `rolled_back_by_user_id` | `Integer` | 是 | 撤销操作者 | ForeignKey('users.id') |

复合唯一约束与索引（ORM 声明）：

```python
(Index('ix_sales_import_batches_imported_at', 'imported_at'), Index('ix_sales_import_batches_user_imported_at', 'user_id', 'imported_at'))
```

## settlement_entries

来源：[models.py](../../../models.py) 的 `SettlementEntry`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `source_key` | `String(128)` | 否 | 事件幂等键 | 唯一 |
| `source_file` | `String(255)` | 是 | 来源文件名 | — |
| `source_sheet` | `String(64)` | 是 | 来源工作表 | — |
| `source_row_number` | `Integer` | 否 | 来源行号 | — |
| `settlement_id` | `String(128)` | 是 | 平台结算单 ID | 索引 |
| `payout_id` | `String(128)` | 是 | 平台付款 ID | 索引 |
| `order_id` | `String(128)` | 否 | 平台订单/调整单 ID | 索引 |
| `related_order_id` | `String(128)` | 是 | 相关订单 ID | — |
| `platform_sku_id` | `String(128)` | 否 | 平台 SKU ID | 索引 |
| `product_id` | `Integer` | 是 | 本地商品关联 | 索引；ForeignKey('products.id') |
| `sale_id` | `Integer` | 是 | 销售关联 | 索引；ForeignKey('sales.id') |
| `store_id` | `Integer` | 是 | 店铺关联；具体归属见模块规则 | 索引；ForeignKey('stores.id') |
| `import_batch_id` | `Integer` | 是 | 文件导入批次关联 | 索引；ForeignKey('sales_import_batches.id') |
| `mapping_status` | `String(32)` | 否 | confirmed 或 pending_confirmation | 索引；Python 默认：'confirmed'；数据库默认：'confirmed' |
| `mapping_error` | `String(500)` | 是 | 映射/库存待确认原因 | — |
| `status` | `String(32)` | 是 | 业务状态；见流程文档 | — |
| `currency` | `String(8)` | 是 | 原币代码 | — |
| `transaction_type` | `String(32)` | 是 | 订单、退款、调整、赔付等来源交易类型 | — |
| `exchange_rate_to_cny` | `DECIMAL(18, 8)` | 是 | 原币兑换人民币的固化汇率 | — |
| `exchange_rate_date` | `Date` | 是 | 实际使用的汇率日期 | — |
| `exchange_rate_source` | `String(64)` | 是 | 汇率来源标识 | — |
| `settlement_date` | `DateTime` | 是 | 结算日期时间 | — |
| `order_created_at` | `DateTime` | 是 | 订单创建时间 | — |
| `delivered_at` | `DateTime` | 是 | 订单送达时间 | — |
| `quantity` | `Integer` | 否 | 数量；业务含义见本表说明 | Python 默认：0 |
| `product_name` | `String(500)` | 是 | 来源商品名称 | — |
| `sku_name` | `String(255)` | 是 | 来源 SKU 名称 | — |
| `settlement_total` | `DECIMAL(16, 4)` | 否 | 原币结算总金额 | Python 默认：0 |
| `net_product_sales` | `DECIMAL(16, 4)` | 否 | 原币净商品销售额 | Python 默认：0 |
| `net_shipping` | `DECIMAL(16, 4)` | 否 | 原币净运费 | Python 默认：0 |
| `taxes` | `DECIMAL(16, 4)` | 否 | 原币税费 | Python 默认：0 |
| `platform_commission` | `DECIMAL(16, 4)` | 否 | 原币平台佣金 | Python 默认：0 |
| `service_fee` | `DECIMAL(16, 4)` | 否 | 原币服务费 | Python 默认：0 |
| `sfp_service_fee` | `DECIMAL(16, 4)` | 否 | 原币 SFP 服务费 | Python 默认：0 |
| `per_item_fee` | `DECIMAL(16, 4)` | 否 | 原币按件成交费用 | Python 默认：0 |
| `tax_withholding` | `DECIMAL(16, 4)` | 否 | 原币代扣税款 | Python 默认：0 |
| `affiliate_commission` | `DECIMAL(16, 4)` | 否 | 原币联盟佣金 | Python 默认：0 |
| `creator_commission` | `DECIMAL(16, 4)` | 否 | 原币达人佣金 | Python 默认：0 |
| `creator_shop_ad_commission` | `DECIMAL(16, 4)` | 否 | 原币达人店铺广告佣金 | Python 默认：0 |
| `affiliate_shop_ad_commission` | `DECIMAL(16, 4)` | 否 | 原币联盟服务商店铺广告佣金 | Python 默认：0 |
| `gmv_max_ad_fee` | `DECIMAL(16, 4)` | 否 | 原币 GMV Max 广告费 | Python 默认：0 |
| `adjustment_amount` | `DECIMAL(16, 4)` | 否 | 原币调整金额 | Python 默认：0 |
| `customer_payment` | `DECIMAL(16, 4)` | 否 | 原币客户付款 | Python 默认：0 |
| `customer_refund` | `DECIMAL(16, 4)` | 否 | 原币客户退款 | Python 默认：0 |
| `raw_data` | `JSON` | 否 | 原始来源行 JSON；应按敏感数据管理 | — |
| `imported_at` | `DateTime` | 否 | 导入时间 | 数据库默认：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(Index('ix_settlement_entries_settlement_date', 'settlement_date'), Index('ix_settlement_entries_order_sku', 'order_id', 'platform_sku_id'))
```

## settlement_entry_allocations

来源：[models.py](../../../models.py) 的 `SettlementEntryAllocation`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `settlement_entry_id` | `Integer` | 否 | 结算行关联 | 索引；ForeignKey('settlement_entries.id') |
| `product_id` | `Integer` | 否 | 本地商品关联 | 索引；ForeignKey('products.id') |
| `sale_id` | `Integer` | 是 | 销售关联 | 索引；ForeignKey('sales.id') |
| `component_name` | `String(500)` | 否 | 组件名称快照 | — |
| `multiplier` | `Integer` | 否 | 每个来源商品包含的组件倍数 | Python 默认：1 |
| `quantity` | `Integer` | 否 | 数量；业务含义见本表说明 | Python 默认：0 |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('settlement_entry_id', 'product_id', name='uq_settlement_entry_allocations_entry_product'), Index('ix_settlement_entry_allocations_product', 'product_id'))
```

## 业务关系与数量

sales.quantity 是本地商品出库件数，sale_cost_details.quantity 是从单个批次扣减件数；同一 Sale 的明细数量之和应等于销售数量，成本之和应等于 total_cost。组合一行产生多个 Sale，settlement_entries.product_id/sale_id 可以为空，通过 allocations 关联，不代表必然映射失败。

settlement_entries.quantity 为平台来源数量，可为负数；allocations.quantity 是乘倍数后的组件数量。sales_import_batches.sales_created 是生成的销售记录数，不是源行数，也不是售出件数。

## 店铺、幂等和审计

新销售/结算/导入保存 store_id，用户后续改绑不更改它；管理员无店铺导入仍可能为空。旧销售根据创建人当前绑定进行兼容查询，旧结算的 store_id 是否完整需核实。

source_key 全局唯一，不含店铺维度；order_no 普通索引且非唯一，组合销售可共享订单号。新订单通过order_claim唯一索引兜底，NULL可有多条；只由首条明细占位，同一事务回滚/撤销删除销售会释放占位。迁移只补可空列和唯一索引，不回填历史记录；历史order_no查重仍保留。allocations 的 (settlement_entry_id,product_id) 唯一，避免同来源行重复组件；批次删除和撤销必须保留/正确处理所有外键关系。

raw_data 保存原始行，不保存原文件内容；source_file/sheet/row_number 是追溯线索。撤销删掉结算、分摊、销售和成本明细，仅保留导入批次状态/统计/撤销人，并不保留完整被撤销业务行审计副本。成本字段和原币/人民币差异见[流程](workflow.md)。

依据：[models.py](../../../models.py)、[sales.py](../../../routers/sales.py) 的 _settlement_entry、rollback_sales_import。启动 [migrate.py](../../../migrate.py) 还回填历史导入批次、修复映射状态，实库与 ORM 外键可能不同。
