# 报表模块代码映射

| 页面/能力 | 前端函数/入口 | 后端/服务 | 表和来源 |
| --- | --- | --- | --- |
| 存货展示/导出 | loadInventoryImpairment、exportInventoryImpairmentReport | inventory_impairment_report、export_inventory_impairment_report、_load_inventory_impairment_report_rows | 批次、商品、规则、按销售日分组的FIFO明细 |
| 存货 SKU 汇总 | setInventoryImpairmentView、inventoryReportTotals | inventory_impairment_summary、_summarize_inventory_impairment_rows、_build_inventory_impairment_summary_sheet | 全部截止日批次（含已售完）、明细保存单价、规则历史 |
| 店铺损益 | loadStoreProfitLossStores、loadStoreProfitLoss、saveStoreProfitLoss | get_store_profit_loss、update_store_profit_loss、_store_profit_loss_auto_values、_build_store_profit_loss_values | stores、sales、明细、人工报告、OA |
| 产品线 | loadProductLineProfit | product_line_profit_report、_product_line_profit_data、_product_line_mapping_for_sale | StoreProduct、ProductLine、结算来源SKU |
| 财务附件 | exportFinancialAttachments | export_financial_attachments、_build_inventory_impairment_workbook、_build_store_profit_loss_sheet、_build_store_profit_loss_summary_sheet、_build_product_line_profit_sheet | 存货+店铺损益+产品线 |
| 月度销量/当前库存 | 当前主要报表导航未发现独立月度页，保留 API | monthly_report | 销售、商品、当前全局库存 |
| 月末快照 | 无人工页面/公开 API | main.lifespan、_monthly_snapshot_worker、ensure_period_snapshot、create_period_snapshot | accounting_periods、inventory_period_snapshots |
| 07逐日累计 | 财务自定义存货金额 | daily_accumulated_impairments → batch_impairments | 原始库存层、每天FIFO出库、商品规则历史 |
| 原准备余额算法 | 快照/店铺损益及07单日基础 | batch_impairments | 商品规则历史、库存层 |
| OA 自动费用 | 损益自动项/默认薪资 | office_space_totals_by_application_date、china_salary_totals_by_store_and_application_date、shared_admin_totals_by_application_date | 外部 PostgreSQL |

逐日累计服务为[inventory_daily_impairment.py](../../../services/inventory_daily_impairment.py)。

来源：[templates/index.html](../../../templates/index.html)、[routers/reports.py](../../../routers/reports.py)、[main.py](../../../main.py)、[services/accounting_periods.py](../../../services/accounting_periods.py)、[services/inventory_impairment.py](../../../services/inventory_impairment.py)、[services/oa_office_expenses.py](../../../services/oa_office_expenses.py)。销售金额上下文还依赖 [routers/sales.py](../../../routers/sales.py)。

| 验证内容 | 现有测试 |
| --- | --- |
| 原准备余额算法及商品规则历史（快照/损益基础） | [test_inventory_impairment_report.py](../../../tests/test_inventory_impairment_report.py) |
| 逐日累计验收、同日销售、跨月、规则与比例上限 | [test_inventory_daily_impairment.py](../../../tests/test_inventory_daily_impairment.py) |
| 入库/出库成本、跨批次汇总、已售完旧批、负值Excel、两种导出和财务范围 | [test_inventory_cost_summary.py](../../../tests/test_inventory_cost_summary.py) |
| 快照仅一次与无公开会计接口 | [test_accounting_period_snapshots.py](../../../tests/test_accounting_period_snapshots.py)、[test_accounting_period_removal.py](../../../tests/test_accounting_period_removal.py) |
| 损益公式、OA默认/人工覆盖 | [test_store_profit_loss_report.py](../../../tests/test_store_profit_loss_report.py) |
| OA 过滤、部门和申请日期 | [test_oa_office_expenses.py](../../../tests/test_oa_office_expenses.py) |
| 财务附件、汇总、产品线分摊与歧义 | [test_financial_attachments_export.py](../../../tests/test_financial_attachments_export.py) |
| 非管理员范围 | [test_store_data_isolation.py](../../../tests/test_store_data_isolation.py) |

现有测试多使用模拟会话，不代表本次或生产已验证。07查询不再补建快照，应用启动仍有写入；文档验证不启动服务。
