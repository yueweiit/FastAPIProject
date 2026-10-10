# 财务主数据 API

前缀 /finance-masters，共16个端点。表中权限为当前服务端依赖，列表是共享主数据范围，没有统一店铺过滤。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `GET /finance-masters/product-lines` | [list_product_lines](../../../routers/finance_masters.py) | admin/operator/viewer | active_only | list[ProductLineResponse] |
| `POST /finance-masters/product-lines` | [create_product_line](../../../routers/finance_masters.py) | admin | ProductLineRequest | ProductLineResponse |
| `PUT /finance-masters/product-lines/{product_line_id}` | [update_product_line](../../../routers/finance_masters.py) | admin | product_line_id、ProductLineRequest | ProductLineResponse |
| `DELETE /finance-masters/product-lines/{product_line_id}` | [delete_product_line](../../../routers/finance_masters.py) | admin | product_line_id | ok |
| `GET /finance-masters/stores` | [list_stores](../../../routers/finance_masters.py) | admin/operator/viewer | active_only | list[StoreResponse] |
| `POST /finance-masters/stores` | [create_store](../../../routers/finance_masters.py) | admin | StoreRequest | StoreResponse |
| `PUT /finance-masters/stores/{store_id}` | [update_store](../../../routers/finance_masters.py) | admin | store_id、StoreRequest | StoreResponse |
| `DELETE /finance-masters/stores/{store_id}` | [delete_store](../../../routers/finance_masters.py) | admin | store_id | ok |
| `GET /finance-masters/store-products` | [list_store_products](../../../routers/finance_masters.py) | admin/operator/viewer | store_id、active_only | list[StoreProductResponse] |
| `POST /finance-masters/store-products` | [create_store_product](../../../routers/finance_masters.py) | admin | StoreProductRequest | StoreProductResponse |
| `PUT /finance-masters/store-products/{mapping_id}` | [update_store_product](../../../routers/finance_masters.py) | admin | mapping_id、StoreProductRequest | StoreProductResponse |
| `DELETE /finance-masters/store-products/{mapping_id}` | [delete_store_product](../../../routers/finance_masters.py) | admin | mapping_id | ok |
| `GET /finance-masters/platform-sku-mappings` | [list_platform_sku_mappings](../../../routers/finance_masters.py) | admin/operator/viewer | active_only | list[PlatformSkuMappingResponse] |
| `POST /finance-masters/platform-sku-mappings` | [create_platform_sku_mappings](../../../routers/finance_masters.py) | admin/operator/viewer | PlatformSkuMappingCreateRequest | list[PlatformSkuMappingResponse] |
| `PUT /finance-masters/platform-sku-mappings/{mapping_id}` | [update_platform_sku_mapping](../../../routers/finance_masters.py) | admin/operator/viewer | mapping_id、PlatformSkuMappingUpdateRequest | PlatformSkuMappingResponse |
| `DELETE /finance-masters/platform-sku-mappings/{mapping_id}` | [delete_platform_sku_mapping](../../../routers/finance_masters.py) | admin/operator/viewer | mapping_id | ok |

## 请求契约与关键校验

- ProductLineRequest：code（1–64）、name（1–255）、is_active（默认true）。
- StoreRequest：platform（默认tiktok_shop）、platform_store_id、name、country_or_region、三字母 settlement_currency、timezone（默认Asia/Shanghai）、可空 legal_entity、is_active。
- StoreProductRequest：store_id、product_id、product_line_id、store_sku、effective_from 必填，effective_to、platform_product_id、platform_sku_id 可空，is_active 默认true。日期范围、引用和唯一/启用映射校验见[流程](workflow.md)。
- PlatformSkuMappingCreateRequest：platform、platform_sku_ids（1–100项）、components（至少1项）；每组件 product_id、quantity_per_sale≥1。可一次为多个 SKU 建相同组件映射。
- PlatformSkuMappingUpdateRequest：platform_sku_id、components、is_active；更新不能改变 platform。
- 各列表支持 active_only，店铺 SKU 列表另支持 store_id≥1；未实现通用 page/page_size。

创建通常201；更新查询200；重复或约束冲突409，业务校验400，引用不存在404，框架字段校验422。平台映射响应附商品 SKU/名称、数量倍数、is_bundle；店铺 SKU 响应附店铺、商品、产品线显示字段。

类型和长度的最终依据是 [schemas.py](../../../schemas.py)，更新和删除边界以 [finance_masters.py](../../../routers/finance_masters.py) 为准。当前 API 不包含 OAuth、平台凭据或同步任务。
