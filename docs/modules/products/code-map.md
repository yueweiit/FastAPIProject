# 商品模块代码映射

| 页面/功能 | 前端函数 | 后端方法 | 表/服务 |
| --- | --- | --- | --- |
| 创建/修改 | createProduct、openEditProduct、saveEditProduct | create_product、update_product、_impairment_settings | products、product_impairment_rules |
| 商品批量新增 | openProductImport、previewProductImport、confirmProductImport | preview_products、import_products | services/product_import.py；products、product_impairment_rules |
| 模板/结果清单 | downloadProductImportTemplate、downloadProductImportResult | product_import_template、export_product_import_result | openpyxl；ProductImportResponse、ProductImportResultRequest |
| 搜索/分页/汇总 | loadProducts、searchProducts、renderProductPage | list_products、_product_summary_stmt、_enrich_product | products、inventory_batches |
| 下拉选项 | loadProductOptions、loadSkuOptions | list_product_options | products |
| 图片/图册 | loadGallery、分页与下载处理 | read_product_image、_save_product_image、ProductImageFiles；list_products | services/product_images.py；uploads 文件、products.image |
| 导出 | downloadProductExport、exportProduct、exportAllProducts | export_products | 商品、批次；openpyxl/Pillow |
| 删除 | deleteProduct | delete_product | products、platform_sku_components 等引用 |
| 平台 SKU 设置 | loadPlatformSkuMappings、savePlatformSkuMapping | 财务主数据路由 | 见[主数据代码映射](../finance-masters/code-map.md) |

前端均在 [templates/index.html](../../../templates/index.html)，后端在 [routers/products.py](../../../routers/products.py)，契约在 [schemas.py](../../../schemas.py)，字段在 [models.py](../../../models.py)。创建/修改响应库存填 0，列表汇总返回实际值，应保持两条路径的用途清晰。

规则变更涉及现行报表的 [services/inventory_provision_movements.py](../../../services/inventory_provision_movements.py)，以及快照路径的 [services/inventory_impairment.py](../../../services/inventory_impairment.py)、[services/accounting_periods.py](../../../services/accounting_periods.py)。测试入口为 [test_product_feature_permissions.py](../../../tests/test_product_feature_permissions.py)、[test_inventory_provision_movements.py](../../../tests/test_inventory_provision_movements.py)、[test_inventory_impairment_report.py](../../../tests/test_inventory_impairment_report.py)。

图片校验、重新编码、历史活动文件隔离及失败清理由 [test_product_images.py](../../../tests/test_product_images.py) 验证；不写实际上传目录或业务数据库。

批量导入解析、预览、预览签名及整批提交位于 [services/product_import.py](../../../services/product_import.py)。自动化入口为 [test_product_import.py](../../../tests/test_product_import.py)；手工真实 MySQL 验证为 [mysql_product_import.py](../../../tests/mysql_product_import.py)，只允许显式设置的本机专用库，不能直接复用业务库连接。
