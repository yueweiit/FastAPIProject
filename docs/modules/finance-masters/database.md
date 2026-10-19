# 财务主数据表

本模块维护五张表，字段类型/默认来自 ORM；完整外键及实库状态需核验。

## product_lines

来源：[models.py](../../../models.py) 的 `ProductLine`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `code` | `String(64)` | 否 | 主数据编码 | 唯一；索引 |
| `name` | `String(255)` | 否 | 名称 | — |
| `is_active` | `Boolean` | 否 | 启用标志 | Python 默认：True |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |
| `updated_at` | `DateTime` | 否 | 更新时间 | 数据库默认：func.now()；更新时：func.now() |

## stores

来源：[models.py](../../../models.py) 的 `Store`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `platform` | `String(32)` | 否 | 平台标识 | Python 默认：'tiktok_shop' |
| `platform_store_id` | `String(128)` | 否 | 平台店铺 ID | — |
| `name` | `String(255)` | 否 | 名称 | — |
| `country_or_region` | `String(64)` | 否 | 国家或区域 | — |
| `settlement_currency` | `String(3)` | 否 | 店铺结算币种 | — |
| `timezone` | `String(64)` | 否 | 时区标识 | Python 默认：'Asia/Shanghai' |
| `legal_entity` | `String(255)` | 是 | 法人主体 | — |
| `is_active` | `Boolean` | 否 | 启用标志 | Python 默认：True |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |
| `updated_at` | `DateTime` | 否 | 更新时间 | 数据库默认：func.now()；更新时：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('platform', 'platform_store_id', name='uq_stores_platform_store_id'),)
```

## store_products

来源：[models.py](../../../models.py) 的 `StoreProduct`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `store_id` | `Integer` | 否 | 店铺关联；具体归属见模块规则 | 索引；ForeignKey('stores.id') |
| `product_id` | `Integer` | 否 | 本地商品关联 | 索引；ForeignKey('products.id') |
| `product_line_id` | `Integer` | 否 | 产品线关联 | 索引；ForeignKey('product_lines.id') |
| `platform_product_id` | `String(128)` | 是 | 平台商品 ID | — |
| `platform_sku_id` | `String(128)` | 是 | 平台 SKU ID | — |
| `store_sku` | `String(128)` | 否 | 店铺 SKU | — |
| `effective_from` | `Date` | 否 | 生效日期 | — |
| `effective_to` | `Date` | 是 | 失效日期，边界包含该日 | — |
| `is_active` | `Boolean` | 否 | 启用标志 | Python 默认：True |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |
| `updated_at` | `DateTime` | 否 | 更新时间 | 数据库默认：func.now()；更新时：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('store_id', 'store_sku', 'effective_from', name='uq_store_products_sku_effective_from'), Index('ix_store_products_store_product_active', 'store_id', 'product_id', 'is_active'))
```

## platform_sku_mappings

来源：[models.py](../../../models.py) 的 `PlatformSkuMapping`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `platform` | `String(32)` | 否 | 平台标识 | Python 默认：'tiktok_shop' |
| `platform_sku_id` | `String(128)` | 否 | 平台 SKU ID | — |
| `is_active` | `Boolean` | 否 | 启用标志 | Python 默认：True |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |
| `updated_at` | `DateTime` | 否 | 更新时间 | 数据库默认：func.now()；更新时：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('platform', 'platform_sku_id', name='uq_platform_sku_mappings_platform_sku'), Index('ix_platform_sku_mappings_platform_active', 'platform', 'is_active'))
```

## platform_sku_components

来源：[models.py](../../../models.py) 的 `PlatformSkuComponent`。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `platform_sku_mapping_id` | `Integer` | 否 | 见 ORM 定义 | 索引；ForeignKey('platform_sku_mappings.id') |
| `product_id` | `Integer` | 否 | 本地商品关联 | 索引；ForeignKey('products.id') |
| `quantity_per_sale` | `Integer` | 否 | 每次平台商品销售的本地组件数量 | Python 默认：1 |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('platform_sku_mapping_id', 'product_id', name='uq_platform_sku_components_mapping_product'),)
```

## 两套映射的约束

product_lines.code 唯一；stores 的平台+店铺 ID 唯一。store_products 以 (store_id,store_sku,effective_from) 唯一，按店铺/商品/启用建索引；“同 SKU 只有一个启用版本”是路由查询约束，不是完整数据库唯一约束。

platform_sku_mappings 的 (platform,platform_sku_id) 唯一，platform_sku_components 的 (mapping_id,product_id) 唯一；组件 relationship 为 all/delete-orphan。它们与店铺 SKU 映射互不外键关联，平台组件不含产品线、店铺和生效日期。

## 状态和历史

is_active 是布尔启停，没有审批状态。StoreProduct 更新和停用会影响历史报表映射选择；PlatformSkuComponent 的数量倍数被导入固化到 SettlementEntryAllocation.multiplier，已有销售不自动重算。店铺 timezone、settlement_currency 只是主数据字段，不能据此认定所有计算已自动按店铺时区/币种处理。

依据：[models.py](../../../models.py)、[finance_masters.py](../../../routers/finance_masters.py)、[销售数据库](../sales/database.md)。店铺还被 users、sales、导入、结算、月末快照和损益报告引用，删除必须核实全部关联。
