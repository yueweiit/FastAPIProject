# 报表模块代码映射

| 页面/能力 | 前端函数/入口 | 后端/服务 | 表和来源 |
| --- | --- | --- | --- |
| 存货展示/导出 | loadInventoryImpairment、exportInventoryImpairmentReport | inventory_impairment_report、export_inventory_impairment_report、_load_inventory_impairment_report_rows | 批次、商品、规则、逐笔FIFO明细、销售时间及销售店铺 |
| 存货 SKU 汇总 | setInventoryImpairmentView、inventoryReportTotals | inventory_impairment_summary、_summarize_inventory_impairment_rows、_build_inventory_impairment_summary_sheet | 全部截止日批次（含已售完）、明细保存单价、规则历史 |
| 店铺损益 | renderStoreProfitLoss、loadStoreProfitLossStores、loadStoreProfitLoss、saveStoreProfitLoss | get_store_profit_loss、update_store_profit_loss、_store_profit_loss_auto_values、_fifo_cost_components、_build_store_profit_loss_values | stores、sales、保存单件成本明细、批次成本构成、人工报告、OA |
| 产品线 | loadProductLineProfit | product_line_profit_report、_product_line_profit_data、_product_line_mapping_for_sale | StoreProduct、ProductLine、结算来源SKU、保存单件成本明细 |
| 财务附件 | exportFinancialAttachments | export_financial_attachments、_build_inventory_impairment_workbook、_build_store_profit_loss_sheet、_build_store_profit_loss_summary_sheet、_build_product_line_profit_sheet | 存货+店铺损益+产品线 |
| 月度销量/当前库存 | 当前主要报表导航未发现独立月度页，保留 API | monthly_report | 销售、商品、当前全局库存 |
| 月末快照 | 无人工页面/公开 API | main.lifespan、_monthly_snapshot_worker、ensure_period_snapshot、create_period_snapshot | accounting_periods、inventory_period_snapshots |
| 持有期间计提/转销 | 07/09/10/12共用 | _load_inventory_provision_data → replay_inventory_provisions | 批次、逐笔FIFO、规则历史、日内先计提后转销 |
| 原静态准备余额 | 自动快照及历史辅助 | batch_impairments | 不替代当前持有期间变动服务 |
| OA 自动费用 | 损益自动项/默认薪资 | office_space_totals_by_application_date、china_salary_totals_by_store_and_application_date、shared_admin_totals_by_application_date | 外部 PostgreSQL |

持有期间服务为[inventory_provision_movements.py](../../../services/inventory_provision_movements.py)。[旧静态重建](../../../services/inventory_balance_impairment.py)和[旧逐日完整余额合计](../../../services/inventory_daily_impairment.py)仅保留历史回归。

来源：[templates/index.html](../../../templates/index.html)、[routers/reports.py](../../../routers/reports.py)、[main.py](../../../main.py)、[services/accounting_periods.py](../../../services/accounting_periods.py)、[services/inventory_impairment.py](../../../services/inventory_impairment.py)、[services/oa_office_expenses.py](../../../services/oa_office_expenses.py)。销售金额上下文还依赖 [routers/sales.py](../../../routers/sales.py)。

| 验证内容 | 现有测试 |
| --- | --- |
| 准备余额规则及商品历史（07/快照/损益共用） | [test_inventory_impairment_report.py](../../../tests/test_inventory_impairment_report.py) |
| 120/480验收、同日销售、跨月年、售完、规则及上限、月增提与账面差 | [test_inventory_provision_movements.py](../../../tests/test_inventory_provision_movements.py)、[test_provision_reporting.py](../../../tests/test_provision_reporting.py) |
| 入库/出库成本、跨批次汇总、售完余额0、Excel公式缓存、两种导出和财务范围 | [test_inventory_cost_summary.py](../../../tests/test_inventory_cost_summary.py) |
| 两份页面脚本语法及真实渲染函数的新增字段、未知合计、120/480和转义（DOM模拟） | [inventory_page_checks.cjs](../../../tests/inventory_page_checks.cjs)，`node tests/inventory_page_checks.cjs` |
| 快照仅一次与无公开会计接口 | [test_accounting_period_snapshots.py](../../../tests/test_accounting_period_snapshots.py)、[test_accounting_period_removal.py](../../../tests/test_accounting_period_removal.py) |
| 损益公式、入库其他成本、OA默认/人工覆盖 | [test_store_profit_loss_report.py](../../../tests/test_store_profit_loss_report.py)；产品线成本见[test_provision_reporting.py](../../../tests/test_provision_reporting.py) |
| 损益加载/保存竞态、条件切换和会话失效（两份页面DOM模拟） | [profit_loss_page_checks.cjs](../../../tests/profit_loss_page_checks.cjs)，`node tests/profit_loss_page_checks.cjs` |
| OA 过滤、部门和申请日期 | [test_oa_office_expenses.py](../../../tests/test_oa_office_expenses.py) |
| 财务附件、汇总、产品线分摊与歧义 | [test_financial_attachments_export.py](../../../tests/test_financial_attachments_export.py) |
| 非管理员范围 | [test_store_data_isolation.py](../../../tests/test_store_data_isolation.py) |

现有测试多使用模拟会话，不代表本次或生产已验证。07查询不再补建快照，应用启动仍有写入；文档验证不启动服务。
