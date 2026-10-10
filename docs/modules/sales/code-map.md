# 销售模块代码映射

| 功能 | 页面函数 | 后端/服务函数 | 核心数据 |
| --- | --- | --- | --- |
| 手工店铺销售 | loadManualSaleStores、createSale、refreshManualSaleOrderNo | create_sale、fifo_sell | stores、sales、库存批次/明细 |
| 文件上传 | importSalesFile | import_sales_file、_read_import_file、_source_key | 导入批次、结算行 |
| 平台组件匹配 | 商品页映射配置 | _resolve_platform_sku_components | 平台映射、组件、products |
| 库存预检/扣库 | 上传结果提示 | _allocate_available_stock、lock_fifo_batches、fifo_sell | inventory_batches、sale_cost_details |
| 组合金额 | 销售表人民币单价/毛利 | _component_allocation_shares、_sale_import_context、_build_sale_response | allocations、结算金额/汇率 |
| 查询/月度汇总 | loadSales、renderMonthlySalesSummary | list_sales、monthly_sales_summary、_sale_filter_values、_monthly_sales_summary | sales、结算、商品、店铺 |
| 成本详情 | 列表成本提示；独立详情 API | get_sale | sale_cost_details、inventory_batches |
| 待确认 | loadPendingConfirmations、exportPendingConfirmations | list_pending_confirmations、_group_pending_confirmations、export_pending_confirmations | settlement_entries |
| 导入历史/撤销 | loadSalesImports、rollbackSalesImport | list_sales_imports、rollback_sales_import | 全部关联业务表 |
| 汇率 | 单价提示、人民币金额 | get_cny_rates | 结算汇率字段 |

前端来源：[templates/index.html](../../../templates/index.html)。后端：[routers/sales.py](../../../routers/sales.py)、[services/fifo.py](../../../services/fifo.py)、[services/exchange_rates.py](../../../services/exchange_rates.py)；契约：[schemas.py](../../../schemas.py)。

验证定位：[test_sales_rules.py](../../../tests/test_sales_rules.py) 覆盖格式、组件、分摊、库存不足、事件键、汇率、月度和 FIFO；[test_fifo_concurrency.py](../../../tests/test_fifo_concurrency.py) 模拟并发锁；[test_store_data_isolation.py](../../../tests/test_store_data_isolation.py) 检查店铺范围。真实 MySQL 脚本会建表/迁移/写入，不在静态文档验证范围。

修改销售上下文会同时影响 [routers/reports.py](../../../routers/reports.py) 的店铺收入与产品线计算，不能只验证销售列表。
