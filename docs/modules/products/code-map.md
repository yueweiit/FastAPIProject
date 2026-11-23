# 商品模块代码映射

| 页面/功能 | 前端函数 | 后端方法 | 表/服务 |
| --- | --- | --- | --- |
| 创建/修改 | createProduct、openEditProduct、saveEditProduct | create_product、update_product、_impairment_settings | products、product_impairment_rules |
| 规格/历史空值/必填校验 | readProductSpecification、productTypeText | normalize_product_specification、specification_export_text | services/product_specification.py；products.specification；创建/编辑/列表/Excel |
| 类型/安全库存/旧规则兼容 | productTypeValue、productTypeLabel、productTypeText、toggleSafeStockField | effective_product_type、protected_stock_quantity | services/inventory_impairment.py；商品及规则历史 |
| 商品批量新增 | openProductImport、previewProductImport、confirmProductImport | preview_products、import_products | services/product_import.py；products、product_impairment_rules |
| 模板/结果清单 | downloadProductImportTemplate、downloadProductImportResult | product_import_template、export_product_import_result | openpyxl；ProductImportResponse、ProductImportResultRequest |
| 搜索/分页/汇总 | loadProducts、searchProducts、renderProductPage | list_products、_product_summary_stmt、_enrich_product | products、inventory_batches |
| 库存/销售/图册商品联想 | ProductSearch.bind（static/product-search.js） | list_products(prioritize_exact_sku=true) | CASE精确SKU优先在LIMIT/OFFSET之前；默认列表顺序不变 |
| 下拉选项 | loadProductOptions、loadSkuOptions | list_product_options | products |
| 图片/图册搜索 | initializeGallerySearch、searchGallery、refreshGallery、clearGallerySearch、loadGallery、resetGallerySession；分页与下载处理 | read_product_image、_save_product_image、ProductImageFiles；list_products | services/product_images.py；uploads 文件、products.image；PageInteractions.gallery*文案 |
| 导出 | downloadProductExport、exportProduct、exportAllProducts | export_products | 商品、批次；openpyxl/Pillow |
| 删除 | deleteProduct | delete_product | products、platform_sku_components 等引用 |
| 平台 SKU 设置 | loadPlatformSkuMappings、savePlatformSkuMapping | 财务主数据路由 | 见[主数据代码映射](../finance-masters/code-map.md) |

前端均在 [templates/index.html](../../../templates/index.html)，后端在 [routers/products.py](../../../routers/products.py)，契约在 [schemas.py](../../../schemas.py)，字段在 [models.py](../../../models.py)。创建/修改响应库存填 0，列表汇总返回实际值，应保持两条路径的用途清晰。

规则变更涉及现行报表的 [services/inventory_provision_movements.py](../../../services/inventory_provision_movements.py)，以及快照路径的 [services/inventory_impairment.py](../../../services/inventory_impairment.py)、[services/accounting_periods.py](../../../services/accounting_periods.py)。测试入口为 [test_product_feature_permissions.py](../../../tests/test_product_feature_permissions.py)、[test_inventory_provision_movements.py](../../../tests/test_inventory_provision_movements.py)、[test_inventory_impairment_report.py](../../../tests/test_inventory_impairment_report.py)。

图片校验、重新编码、历史活动文件隔离及失败清理由 [test_product_images.py](../../../tests/test_product_images.py) 验证；不写实际上传目录或业务数据库。

规格回归：[test_product_specification.py](../../../tests/test_product_specification.py)覆盖缺失/空白/超长/特殊字符、三角色、历史空值可读及补齐、导出列位/数值日期格式/公式文本安全、旧模板提示、表头别名和增加可空列后旧数据保留。[product_types_page_checks.cjs](../../../tests/product_types_page_checks.cjs)同时验证两份页面字段顺序、必填、回显、空值提示、预览转义与空表列数；[test_business_audit.py](../../../tests/test_business_audit.py)覆盖规格前后值和审计失败回滚。数据库测试仅隔离SQLite，非真实MySQL迁移或生产验收。常规商品测试进程使用 `FIFO_OPERATION_AUDIT_ENABLED=0` 隔离本地配置，审计用例自行显式启用，未改变应用配置或权限。

2026-11-23初版验证（边界修复前）：全量695项unittest、上述两份页面112项商品检查及认证/批次编辑/销售补录/销售导出页面回归通过；另使用独立本地模拟API及Chrome验证两份真实HTML的必填拦截、历史补齐、保存清空、列表/预览转义，并检查1600及390像素宽度。未启动main、连接业务库、执行MySQL迁移或发布。

同日审查修复：规格使用 textarea 保留录入时的换行，页面/API/Excel写入统一转为空格，原文中的XML非法字符直接拒绝；历史异常规格及结果清单使用可见码点和单元格批注导出，不修改原值。补充创建/编辑拒绝且无写入、CRLF归一后长度边界、Excel预览/确认、异常记录整批导出及源值保留回归。全量702项unittest、两份页面160项商品检查、210项认证检查通过；Chrome再次验证两份页面及390像素宽度，覆盖历史多行规格直接编辑保存和控制字符拦截。无新增迁移、业务库写入或服务重启。

图册浏览器回归：[gallery_search_browser_checks.cjs](../../../tests/gallery_search_browser_checks.cjs)，运行`node tests/gallery_search_browser_checks.cjs`（复用已有Playwright，可通过INVENTORY_PLAYWRIGHT_MODULE及INVENTORY_BROWSER_CHANNEL指定）。两份真实HTML和模拟API覆盖名称/SKU、候选点击/键盘、分页/刷新/清空、特殊字符及文本转义、过期响应/同账号重新登录/旧401、图片下载链接和移动端宽度，不启动main或连接业务库。

联想排序回归：[test_product_search.py](../../../tests/test_product_search.py)用隔离SQLite/ASGI验证超过20个近似SKU时精确项置顶、跨页不丢不重、大小写/空格/特殊字符、默认及内部调用兼容、数组格式、三角色共享权限和仅SELECT。浏览器夹具另验库存选中/导出及销售只填写产品名，真实MySQL排序性能仍需按实际规模验证。

批量导入解析、预览、预览签名及整批提交位于 [services/product_import.py](../../../services/product_import.py)。自动化入口为 [test_product_import.py](../../../tests/test_product_import.py)；手工真实 MySQL 验证为 [mysql_product_import.py](../../../tests/mysql_product_import.py)，只允许显式设置的本机专用库，不能直接复用业务库连接。
