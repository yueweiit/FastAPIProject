# 财务主数据代码映射

| 功能 | 页面入口/函数 | 后端方法 | 表与影响 |
| --- | --- | --- | --- |
| 店铺创建/列表 | 用户管理：adminCreateStore、loadUsers | create_store、list_stores | stores、用户绑定 |
| 店铺删除 | 用户管理：deleteStore | delete_store | 店铺及历史引用 |
| 平台 SKU 创建/修改 | 商品区：loadPlatformSkuMappings、savePlatformSkuMapping | create_platform_sku_mappings、update_platform_sku_mapping | platform_sku_mappings/components |
| 组件编辑 | addPlatformSkuComponent、removePlatformSkuComponent、editPlatformSkuMapping | _validate_platform_sku_components | 本地商品、每次销售倍数 |
| 平台映射删除 | deletePlatformSkuMapping | delete_platform_sku_mapping | 映射和级联组件 |
| 产品线 CRUD | 当前模板未发现独立维护页面，API 已存在 | list_product_lines、create_product_line、update_product_line、delete_product_line | product_lines |
| 店铺 SKU CRUD | 当前模板未发现独立维护页面，API 已存在 | list_store_products、create_store_product、update_store_product、delete_store_product | store_products |
| 店铺修改 | API 能力 | update_store | stores |
| 导入匹配 | 销售上传 | _resolve_platform_sku_components | 平台 SKU |
| 产品线归属 | 产品线报表 | _product_line_mapping_for_sale | 店铺 SKU、生效时间、来源 SKU |

页面：[templates/index.html](../../../templates/index.html)。后端：[routers/finance_masters.py](../../../routers/finance_masters.py)，请求/响应：[schemas.py](../../../schemas.py)，模型：[models.py](../../../models.py)。

下游：[routers/sales.py](../../../routers/sales.py) 的导入与 _sale_import_context；[routers/reports.py](../../../routers/reports.py) 的产品线归属。权限相关测试为 [test_product_feature_permissions.py](../../../tests/test_product_feature_permissions.py)、[test_store_data_isolation.py](../../../tests/test_store_data_isolation.py)，产品线匹配测试见 [test_financial_attachments_export.py](../../../tests/test_financial_attachments_export.py)。
