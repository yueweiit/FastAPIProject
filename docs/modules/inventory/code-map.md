# 入库模块代码映射

库存内归属管理由[inventory-allocations.js](../../../static/inventory-allocations.js)提供，两份HTML挂载同一模块，总览/盘点按商品ID打开批次分页列表。GET /batches复用活动批次和内容版本；PUT /batches/{id}/impairment-stores通过`_batch_edit_context`复用父批次→成本层→归属/日规则锁序，仅提交归属ID、生效模式和版本，不接受数量/成本字段，成功同事务留BatchChangeLog。新入库无归属由`initialize_unassigned`写显式空基线；分摊及未知传播由batch_store_allocations.py统一处理。测试见[test_batch_store_allocations.py](../../../tests/test_batch_store_allocations.py)、[浏览器回归](../../../tests/inventory_workspace_browser_checks.cjs)。无新增表，沿用两张日规则表。

库存Excel由[services/inventory_export.py](../../../services/inventory_export.py)格式化，只消费页面共用的overview_row/activity_rows，不连接数据库或另算库存。接口测试核对总览/整月/横向格式、全查询范围、指定商品、跨店权限、未知合计空白、文本/公式安全、上限和分组期间一致性；浏览器测试核对下载文件名/参数、忙时重复点击、失败恢复、无效文件和旧会话响应。

库存工作台入口为[routers/inventory.py](../../../routers/inventory.py)、[services/inventory_activity.py](../../../services/inventory_activity.py)、[static/inventory-workspace.js](../../../static/inventory-workspace.js)。两份首页同步菜单、挂载、样式及退出清理，保留此前非本功能差异。`python -m unittest tests.test_inventory_workspace -v`以独立SQLite/ASGI验证数量勾稽、更正/作废时点、取消未来到货、补差、权限、参数、分页及只SELECT。

`node tests/inventory_workspace_browser_checks.cjs`使用现有Playwright、本地静态服务器及模拟API，真实无头浏览器检查两份页面、三视图、弹窗、转义、错误、退出/重登与晚响应、重复重建及窄屏。可用INVENTORY_PLAYWRIGHT_MODULE指定现有模块、INVENTORY_BROWSER_CHANNEL选择已安装浏览器、INVENTORY_QA_DIR保存截图；不安装依赖、不启动main、不连接业务库或外网，不代表目标MySQL/生产验收。

| 页面/流程 | 前端函数 | 后端函数 | 数据/依赖 |
| --- | --- | --- | --- |
| 入库成本预览/创建 | calcUnitCost、createBatch | create_batch、_resolve_purchase_values、_calc_unit_cost、_next_batch_no | products、inventory_batches |
| SKU 下拉 | loadBatchSkuOptions、loadProductOptions | list_product_options | products |
| 库存总览/预警 | InventoryWorkspace.open、refresh、render | inventory.overview；复用products.list_products | 活动批次余量及有效成本 |
| 盘点/跌价页签、批次及导出 | static/inventory-assessment.js | inventory.assessment/export_assessment；services/inventory_assessment.py | 既有日结/持有期间准备及库存盘点版本；services/inventory_export.py格式化 |
| 保存/查询盘点历史 | InventoryAssessment内save/history | inventory.save_stock_count/stock_counts；quantity_basis/latest_counts | inventory_stock_counts；按FIFO顺序锁批次重验数量，幂等/乐观版本/唯一约束 |
| 历史逐批核对/权限入库 | 同一盘点页及Excel | inventory_activity.build_activity(batch_balances)、quantity_basis的入库事件、assessment_rows(include_voided_receipts) | 内部逐批余量和完整数量版本；viewer仅展示可见入库，抵消更正不能确认错误历史金额 |
| 每日进出/三种视图 | inventory-workspace.js | inventory.daily、product_page、activity_data | inventory_activity.load_activity_sources/build_activity |
| 顶部联想/按商品输入检索 | searchField、bindProductSearch → ProductSearch.bind、applyKeywordQuery、traceProduct | products.list_products；inventory.overview/daily/export(product_id) | product-search.js与销售共用候选20项/键盘/防抖，独立请求状态；精确ID、关键字查询、输入法、取消及会话隔离 |
| 来源弹窗 | openDetail、loadDetail、closeDetail | inventory.movements | 分页FIFO、到货基数、更正及作废负调整 |
| 库存/整月/横向Excel导出 | exportInventory；固定条件、会话隔离 | inventory.export_inventory；overview_row、activity_rows | services/inventory_export.py只格式化查询结果，含范围/空值说明 |
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

盘点回归：[test_inventory_assessment.py](../../../tests/test_inventory_assessment.py)用隔离SQLite/ASGI验证字段/批次/准备、0实盘、历史版本/幂等/冲突、正常较晚销售不污染历史盘点、回填日结变更、严格输入/异常回滚、角色/日期及店铺范围、完整Excel/文本安全/未知合计、缺结构503。真实MySQL锁等待仍待验证。无头浏览器测试新增两份页面的录入/差异/重复点击/错误保留输入/历史/转义/下载和返回原库存页。

2026-10-29另在正常登录的本地8010页面完成管理员录入、保存、刷新、三个历史版本及真实Excel下载，后端连接既有MySQL测试库；自建测试数据已清理并核对既有业务记录未变。结构状态、证据与未覆盖范围见[本地盘点核验](../../../PROJECT_RUNBOOK.md#库存盘点与跌价2026-10-29)，不以模拟API浏览器检查代替真实链路结论。

审查回归另覆盖：后续跨批次+20/−20数量更正相互抵消、SKU数量相等但历史金额须置空；更正后日期和无关商品仍可显示已知值；零余量未来/作废批次不误判；admin/operator/viewer页面及Excel入库范围与原日结一致、隐藏异常入库不污染可见统计、盘点版本不因角色改变。两份真实浏览器页面验证保存期间实盘/备注/取消锁定，冲突及网络失败后恢复控件和保留输入、同键重试与退出后新表单隔离。

归属管理审查回归覆盖：待分配批次作废前后历史准备/转销保持未知及警示，创建人以外店铺、入库前/持有期/作废后边界；零成本待分配不污染损益，小额非零成本和未知比例单位保留待核对。两份页面模拟保存期间批次数20→21、末页21→20并回到第一页，分页按钮采用最新结果，失败恢复与旧会话隔离继续验证。

相关测试为 [test_sales_rules.py](../../../tests/test_sales_rules.py)、[test_fifo_concurrency.py](../../../tests/test_fifo_concurrency.py)；真实验证脚本 [mysql_fifo_concurrency.py](../../../tests/mysql_fifo_concurrency.py) 会写测试库，需要独立运行授权。历史批次字段/创建人转移脚本见 [migrations/](../../../migrations/)，不得默认执行。

作废回归见[test_batch_void.py](../../../tests/test_batch_void.py)及[batch_edit_page_checks.cjs](../../../tests/batch_edit_page_checks.cjs)：权限、原因、版本、日志、销售外键、FIFO/商品库存、销售利润待核对、已作废导出及两份页面；另验证旧DELETE对所有写角色返回410、不删记录，未出库批次作废同样留日志。页面覆盖作废成功后刷新仍在等待时退出/同账号重登，旧失败回调不得显示提示。销售前及未销售持有月份的费用待核对见[test_store_profit_loss_report.py](../../../tests/test_store_profit_loss_report.py)的VoidedHoldingPeriodTests。SQLite验证不替代InnoDB并发/生产DDL验证。

批次回归：`python -m unittest tests.test_batch_edit tests.test_batch_restatement -v`使用真实路由和隔离SQLite验证版本、权限、已售更正、金额及原子性；不证明InnoDB等待。`node tests/batch_edit_page_checks.cjs`覆盖两份页面的已售编辑入口、垃圾桶及旧删除函数移除、强制删除及旧接口错误提示、有效金额回填、数量下限、追溯模式、重复提交和错误保留；`node tests/batch_export_page_checks.cjs`覆盖选择/筛选导出、等待/失败提示、下载与会话切换；日期沿用page_interactions_checks.cjs。文案集中在[page-interactions.js](../../../static/page-interactions.js)的zh-CN字典。

test_batch_restatement还覆盖原销售月成本增加、入库数量不重置已售数、入库日后移越过既有销售日后的单号编辑与成本追溯、售完不复活库存、旧月度版本保留/新版、未来FIFO只叠加一次、旧分配数量冲销及财务关闭/旧模式/金额越界拒绝。日志失败回滚数量、依据、所有成本凭证和财务审计。batch_log_page_checks.cjs仍验证日志弹窗分页/转义/过期响应。

精度与重试回归：test_batch_restatement通过实际批次及财务路由验证非整除费用清零、再次保存/零额核对、真实负差拒绝；更正数量后的原成本单重试不新增分配、审计或日志，修改单据/批次/显式权重仍409，非创建人仍403。另验证数量增加后的旧凭证冲销不能将整批负成本舍为零；test_financial_controls验证最大INT数量下的-0.0001元仍拒绝及原有总成本下限。

并发回归：test_batch_edit模拟RR旧快照，编辑锁内当前读调整层；零额凭证也改变版本。旧表单409不覆盖成本、不追加日志；ORM旧对象刷新、缺失/错误版本及POST兼容仍覆盖。旧DELETE已停用，回归验证不进入库存事务。页面固定打开时版本，重试旧表单不自动更新版本。独立DDL脚本[add_batch_cost_basis.py](../../../migrations/add_batch_cost_basis.py)仅输出SQL，不执行迁移。
