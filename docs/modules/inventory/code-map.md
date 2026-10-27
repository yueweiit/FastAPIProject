# 入库模块代码映射

| 页面/流程 | 前端函数 | 后端函数 | 数据/依赖 |
| --- | --- | --- | --- |
| 入库成本预览/创建 | calcUnitCost、createBatch | create_batch、_resolve_purchase_values、_calc_unit_cost、_next_batch_no | products、inventory_batches |
| SKU 下拉 | loadBatchSkuOptions、loadProductOptions | list_product_options | products |
| 搜索/分页 | loadBatches、searchBatches、renderBatchPage | list_batches | 批次、商品 |
| 字段显示 | toggleBatchColumn、applyBatchColumnSelection | 无后端写入 | 浏览器列状态 |
| 编辑 | batchEditBlockedReason、openEditBatch、saveEditBatch | _locked_batch、update_batch | 库存剩余数、成本调整当前读、InventoryBatch.edit_version |
| 旧删除兼容入口（停用） | 无页面入口 | delete_batch | 角色校验后固定410 batch_delete_retired，不修改批次 |
| 管理员/运营强制删除（作废） | voidBatch、switchBatchVoidFilter、renderBatchPage | void_batch、list_batches | voided_at/by/reason、BatchChangeLog(action=void)；原销售及快照保留 |
| 已售数量/成本更正 | openEditBatch、saveEditBatch | [batch_edits.py](../../../services/batch_edits.py)的freeze_cost_basis/restate_costs；base_cost_components、post_cost_adjustment | cost_basis、调整头/分配/财务审计；保留原FIFO单价 |
| 修改日志 | openBatchLogs、PageInteractions.batchLogs | _batch_change_values、list_batch_change_logs | BatchChangeLog；批次更新与日志同事务 |
| 选择/导出 | downloadBatchExport、exportBatch、exportAllBatches | export_batches | 批次、商品、uploads 图片 |
| FIFO 消耗 | 销售页面 | lock_fifo_batches、fifo_sell | sales、sale_cost_details |
| 撤销恢复 | rollbackSalesImport | rollback_sales_import | 原批次、原成本明细 |

来源：[templates/index.html](../../../templates/index.html)、[routers/batches.py](../../../routers/batches.py)、[routers/products.py](../../../routers/products.py)、[routers/sales.py](../../../routers/sales.py)、[services/fifo.py](../../../services/fifo.py)、[services/cost_layers.py](../../../services/cost_layers.py)。契约为 [schemas.py](../../../schemas.py) 的 BatchCreate/BatchUpdate/BatchVoidRequest/BatchResponse/BatchPageResponse。

相关测试为 [test_sales_rules.py](../../../tests/test_sales_rules.py)、[test_fifo_concurrency.py](../../../tests/test_fifo_concurrency.py)；真实验证脚本 [mysql_fifo_concurrency.py](../../../tests/mysql_fifo_concurrency.py) 会写测试库，需要独立运行授权。历史批次字段/创建人转移脚本见 [migrations/](../../../migrations/)，不得默认执行。

作废回归见[test_batch_void.py](../../../tests/test_batch_void.py)及[batch_edit_page_checks.cjs](../../../tests/batch_edit_page_checks.cjs)：权限、原因、版本、日志、销售外键、FIFO/商品库存、销售利润待核对、已作废导出及两份页面；另验证旧DELETE对所有写角色返回410、不删记录，未出库批次作废同样留日志。销售前及未销售持有月份的费用待核对见[test_store_profit_loss_report.py](../../../tests/test_store_profit_loss_report.py)的VoidedHoldingPeriodTests。SQLite验证不替代InnoDB并发/生产DDL验证。

批次回归：`python -m unittest tests.test_batch_edit tests.test_batch_restatement -v`使用真实路由和隔离SQLite验证版本、权限、已售更正、金额及原子性；不证明InnoDB等待。`node tests/batch_edit_page_checks.cjs`覆盖两份页面的已售编辑入口、垃圾桶及旧删除函数移除、强制删除及旧接口错误提示、有效金额回填、数量下限、追溯模式、重复提交和错误保留；`node tests/batch_export_page_checks.cjs`覆盖选择/筛选导出、等待/失败提示、下载与会话切换；日期沿用page_interactions_checks.cjs。文案集中在[page-interactions.js](../../../static/page-interactions.js)的zh-CN字典。

test_batch_restatement还覆盖原销售月成本增加、入库数量不重置已售数、入库日后移越过既有销售日后的单号编辑与成本追溯、售完不复活库存、旧月度版本保留/新版、未来FIFO只叠加一次、旧分配数量冲销及财务关闭/旧模式/金额越界拒绝。日志失败回滚数量、依据、所有成本凭证和财务审计。batch_log_page_checks.cjs仍验证日志弹窗分页/转义/过期响应。

精度与重试回归：test_batch_restatement通过实际批次及财务路由验证非整除费用清零、再次保存/零额核对、真实负差拒绝；更正数量后的原成本单重试不新增分配、审计或日志，修改单据/批次/显式权重仍409，非创建人仍403。另验证数量增加后的旧凭证冲销不能将整批负成本舍为零；test_financial_controls验证最大INT数量下的-0.0001元仍拒绝及原有总成本下限。

并发回归：test_batch_edit模拟RR旧快照，编辑锁内当前读调整层；零额凭证也改变版本。旧表单409不覆盖成本、不追加日志；ORM旧对象刷新、缺失/错误版本及POST兼容仍覆盖。旧DELETE已停用，回归验证不进入库存事务。页面固定打开时版本，重试旧表单不自动更新版本。独立DDL脚本[add_batch_cost_basis.py](../../../migrations/add_batch_cost_basis.py)仅输出SQL，不执行迁移。
