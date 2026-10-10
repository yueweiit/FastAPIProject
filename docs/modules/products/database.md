# 商品模块数据表

本模块定义商品与规则生效历史，当前库存来自[库存批次](../inventory/database.md)，完整关系见[数据库总览](../../knowledge/database.md)。类型/可空/默认来自 ORM，实库待验证。

## products

来源：[models.py](../../../models.py) 的 `Product`（源码第 42 行，行号可能随版本变化）。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `sku` | `String(64)` | 否 | 本地商品 SKU | 唯一；索引 |
| `name` | `String(255)` | 否 | 名称 | — |
| `image` | `String(500)` | 是 | 图片访问路径 | — |
| `product_type` | `String(16)` | 否 | stable 或 new | Python 默认：'stable' |
| `safe_stock_quantity` | `Integer` | 否 | 安全库存数量 | Python 默认：0 |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |

## product_impairment_rules

来源：[models.py](../../../models.py) 的 `ProductImpairmentRule`（源码第 65 行，行号可能随版本变化）。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `product_id` | `Integer` | 否 | 本地商品关联 | 索引；ForeignKey('products.id', ondelete='CASCADE') |
| `product_type` | `String(16)` | 否 | stable 或 new | Python 默认：'stable' |
| `safe_stock_quantity` | `Integer` | 否 | 安全库存数量 | Python 默认：0 |
| `effective_date` | `Date` | 否 | 规则生效日 | — |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |

复合唯一约束与索引（ORM 声明）：

```python
(UniqueConstraint('product_id', 'effective_date', name='uq_product_impairment_rules_product_effective_date'), Index('ix_product_impairment_rules_product_effective', 'product_id', 'effective_date'))
```

## 规则历史与引用

ProductImpairmentRule 的 (product_id,effective_date) 唯一，按商品和生效日建复合索引；relationship 使用 all/delete-orphan，外键 ON DELETE CASCADE。同日修改覆盖当日规则，按日而非精确时间保留版本。

products.sku 唯一；name 没有唯一约束，名称匹配可出现歧义。product_type/safe_stock_quantity 没有数据库级枚举或非负约束，主要依赖路由校验。商品不含 store_id、user_id，不能推定是单店商品。

products 被批次、销售、店铺映射、组件、结算及快照引用，除规则历史外不能假定级联删除。依据：[models.py](../../../models.py) 的 Product、ProductImpairmentRule；[products.py](../../../routers/products.py)。
