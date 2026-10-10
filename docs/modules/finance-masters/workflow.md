# 财务主数据流程

## 店铺与产品线

admin 创建产品线，code 唯一；创建店铺，(platform,platform_store_id) 唯一。字段 strip，币种 upper，is_active 控制启用。更新直接保存当前记录，无独立变更审计表；重复约束由 _commit 回滚并返回409。

删除产品线先检查 StoreProduct 引用；删除店铺检查店铺映射、用户绑定、销售和结算。导入批次、快照和人工损益等其他引用仍需核对，不可假定删除检查覆盖全部历史外键。依据：[finance_masters.py](../../../routers/finance_masters.py) 的各 CRUD 函数；[models.py](../../../models.py)。

## 店铺 SKU 生效映射

```text
建立店铺/产品线/商品
-> 检查 effective_to >= effective_from
-> 检查引用存在；启用映射要求店铺与产品线启用
-> 检查同店同 SKU 同生效日唯一
-> 检查同店同 SKU 只有一个启用映射
-> 提交当前映射
```

要建立新启用版本需先停用旧映射；这是启用标志限制，不是完整时间区间重叠检查。生效判定包括 effective_to 当日，且要求 is_active。StoreProduct 更新可直接改变关联、产品线和日期；停用历史映射会影响报表对历史销售的有效映射解析，不能默认具备不可变历史版本。

## 平台 SKU 与组合商品

```mermaid
flowchart LR
    A["多个平台 SKU ID"] --> M["批量建立映射"]
    M --> P["组件列表：本地商品 + quantity_per_sale"]
    P --> S["订单详情：按 SKU ID 严格匹配"]
    S --> F["按倍数 FIFO 出库与成本分摊"]
```

创建一次可提交最多100个 SKU ID，共用同一组件列表；不能为空、不能重复或与现有平台 SKU 冲突。组件至少一个、同商品不能重复、商品必须存在、每次销售数量≥1。创建映射启用，更新可停用并按商品 ID 差量增删组件；只有多个不同商品组件才标 is_bundle=true，单一商品倍数>1不因此标组合。

平台映射的唯一性为平台+SKU，没有店铺维度和生效日期。导入只使用启用的 tiktok_shop 映射；平台映射删除级联组件，但不重写过去分摊记录。依据：_validate_platform_sku_components、create_platform_sku_mappings、update_platform_sku_mapping、delete_platform_sku_mapping。
