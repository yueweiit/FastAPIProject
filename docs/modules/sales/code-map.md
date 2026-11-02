# 销售模块代码映射

| 功能 | 页面函数 | 后端/服务函数 | 核心数据 |
| --- | --- | --- | --- |
| 手工结算补录 | loadManualSaleStores、createSale → SalesLedger.createManualSettlement | create_manual_settlement | stores、sales_import_batches、settlement_entries；不扣库存 |
| 补录产品名联想 | initializeManualProductSearch → ProductSearch.bind（static/product-search.js） | products.list_products | 复用库存候选/键盘/会话保护；选择仅填产品名，不改来源SKU或库存关联 |
| 文件上传/真实进度 | importSalesFile → SalesImportProgress（static/sales-import-progress.js） | import_sales_stream / import_sales_file → _import_sales_file；services/import_progress.py | 同一导入核心、独立会话及单槽进度队列；无新表 |
| 纯金额订单调整 | 导入结果、实际出库数量0的台账行 | _order_settlement_only_reason、_settlement_entry；financial_facts._unlinked_settlement_stmt | raw_data._settlement_only_reason；原数量/交易类型保留，不生成Sale/FIFO，所有财务入口共用 |
| 产品名识别 | Excel产品名表达式 | _resolve_product_components(strict_names=True)、_resolve_product_name、_product_expression_parts | products、来源产品名；不读取平台映射 |
| 中文产品名/历史展示 | 台账产品名、详情和Excel | _sale_import_context、_financial_event_response；FinancialEvent.display_product_name | 原始产品名优先，历史缺列用已关联Product.name；保存标题及历史成本不改 |
| 原始订单号展示 | sales-ledger.js的orderNumber、计算标题与按钮；sales_export.py | _sale_import_context → _build_sale_response.display_order_no；_order_cost_data汇总展示字段 | 来源订单ID优先，无明确来源保留原编号；内部order_no/order_claim、同SKU组件分组与幂等不变 |
| 库存预检/扣库 | 上传结果提示 | _allocate_available_stock、lock_fifo_batches、fifo_sell | inventory_batches、sale_cost_details |
| 组合金额 | 销售表人民币单价/毛利 | _component_allocation_shares、_sale_import_context、_build_sale_response | allocations、结算金额/汇率 |
| 查询/月度汇总 | loadSales、renderMonthlySalesSummary | list_sales、monthly_sales_summary、_sale_filter_values、_monthly_sales_summary | sales、结算、商品、店铺 |
| 原币金额与汇率核对 | sales-ledger.js、sales-fields.zh-CN.json；sales_export.py | _sale_import_context/_financial_event_response → SaleResponse.source_row_net_product_sales/source_row_settlement_total | 来源整行原币值、组合重复但不合计；保存汇率/日期/来源；未知保留空白；回归test_sales_currency_reconciliation.py |
| 多列固定/账号偏好 | sales-ledger.js initializePin/displayColumns/changePin/updatePinControls/headers/render；两份页面sales-pin-picker | 无新增接口 | 全部数据字段多选，按原顺序前置；累计位置固定，窗口限宽有明确反馈，v2数组偏好兼容v1 |
| 显示/隐藏字段 | sales-ledger.js changeVisibility/saveVisibility/refreshColumns/displayColumns；两份页面sales-visible-picker | 无新增接口 | 同步隐藏表头/明细/合计/列宽，至少保留一列；独立账号偏好，保留隐藏列的固定选择，全部显示恢复；导出不变 |
| 商品/SKU/订单号统一搜索 | searchSales、同一keyword与回车事件；两份页面 | _sale_filter_values；financial_facts._unlinked_settlement_stmt及共用计数 | 来源原始订单、内部历史编号、独立结算；month/product_id及店铺权限同时保留 |
| 全筛选销售Excel | exportSalesRecords、resetSalesExportSession | export_sales_records → list_sales；count_unlinked_settlement_events → 与事件读取共用SQL；services/sales_export.py格式化 | 保存成本/汇率、独立事件；只读、权限不变 |
| 成本详情 | 列表成本提示；独立详情 API | get_sale | sale_cost_details、inventory_batches |
| 四项成本/本行总额 | loadSales，字段资源驱动的台账及本页合计 | sale_cost_context → fifo_cost_components/rounded_cost_components；_build_sale_response | 保存FIFO成本、冻结cost_basis、财务补差；原成本不改写 |
| 出库商品跌价参考 | loadSales、销售Excel追加列 | services/sales_costs.py attach_sale_impairment → services/inventory_provision_data.py load_inventory_provision_data | 同商品全局事实回放，仅输出获准销售；与店铺损益归属分开 |
| 待确认筛选/导出/重试 | SalesPending（static/sales-pending.js），两份首页的原函数委托 | list_pending_confirmations、export_pending_confirmations、retry_pending_confirmations → services/sales_pending.py | 原settlement_entries及分配、原导入头；共用_write_candidate_sales/FIFO，不重建结算来源 |
| 导入历史/撤销 | loadSalesImports、rollbackSalesImport | list_sales_imports、rollback_sales_import | 全部关联业务表 |
| 汇率 | 单价提示、人民币金额 | get_cny_rates | 结算汇率字段 |

前端来源：[templates/index.html](../../../templates/index.html)。后端：[routers/sales.py](../../../routers/sales.py)、[services/fifo.py](../../../services/fifo.py)、[services/exchange_rates.py](../../../services/exchange_rates.py)；契约：[schemas.py](../../../schemas.py)。

多列固定回归：[sales_pinned_column_checks.cjs](../../../tests/sales_pinned_column_checks.cjs)执行共享实际模块，遍历所有数据列，验证多列原顺序、累计位置、清空、合计/未知/非合计字段、单行表头与字段ID、账号/刷新隔离、v1兼容/坏JSON/存储失败、宽度限制与恢复、Escape以及失败/退出后不恢复旧数据。现有浏览器脚本另验证两份页面取消上层分组行后的字段名/问号保留、勾选/清空/关闭操作、跨组累计几何、配色、单行吸顶、表头/数据/合计对齐、手机限制提示与保存设置不丢失及刷新持久化。字段分组仍用于设置面板和Excel。均不连接业务库。

同一回归脚本还遍历所有字段的隐藏/恢复、双表列宽同步、固定保留与位置重算、操作列隐藏、至少一列保护、全部显示、账号/刷新隔离、非法偏好及存储异常、空结果和失败后调整。浏览器脚本验证两份实际页面的整组隐藏、金额不变、重载恢复、手机面板边界、键盘关闭、最后一列提示及失败跨度恢复；使用模拟API，不写业务库。

待确认回归：[test_sales_pending.py](../../../tests/test_sales_pending.py)在隔离SQLite/ASGI覆盖原因数量/筛选/Excel、店铺与角色、原金额日期汇率/事件键不变、人工组合倍数、整行库存不足及晚到库存、重复跳过、重名指定、财务引用保护、整次异常回滚及原文件撤销；不证明真实InnoDB并发。[sales_pending_browser_checks.cjs](../../../tests/sales_pending_browser_checks.cjs)使用两份真实页面与模拟API覆盖筛选、所选/全部筛选重试、组件确认/倍数换算、失败保留、取消、重复提交、旧401/同账号重登、viewer及移动端。前端本地字典和资源入口见[国际化](../../knowledge/internationalization.md)。

原订单号回归：[test_sales_order_display.py](../../../tests/test_sales_order_display.py)验证同订单多SKU不合并、组合组件、列表/单条/计算/Excel一致、直接旧来源/缺少来源/旧CSV/手工回退、前导零/连字符/公式文本、防重重传、库存成本保持和店铺隔离。页面VM与浏览器脚本同步核对原始编号、安全转义及计算按钮/标题，不连接业务库。

订单号搜索回归：[test_sales_order_search.py](../../../tests/test_sales_order_search.py)覆盖原编号/部分编号/空格、同订单多来源SKU、直接旧关联与展示原始编号、内部/手工/CSV回退、独立赔付、数组/分页/月度/全筛选导出一致、月份/product_id/店铺叠加、无绑定拒绝及查询仅SELECT。现有导出VM和浏览器脚本核对两份页面的翻译标签、回车重置分页、同条件统计及无分页导出。

进度回归：[test_sales_import_progress.py](../../../tests/test_sales_import_progress.py)覆盖提交后成功、回滚、店铺/角色边界、幂等、跨月查询及断开视图后任务继续，仅使用隔离SQLite；[sales_import_progress_checks.cjs](../../../tests/sales_import_progress_checks.cjs)验证分块UTF-8、未完整返回、会话隔离及提交阶段；[sales_import_page_checks.cjs](../../../tests/sales_import_page_checks.cjs)运行两份实际页面导入函数；现有[inventory_workspace_browser_checks.cjs](../../../tests/inventory_workspace_browser_checks.cjs)补充两份页面的真实HTTP分块进度、错误恢复、月份说明、移动端及文件名转义，使用本地模拟服务，不连接业务库。

验证定位：[test_sales_rules.py](../../../tests/test_sales_rules.py) 覆盖格式、组件、分摊、库存不足、事件键、汇率、月度和 FIFO；[test_fifo_concurrency.py](../../../tests/test_fifo_concurrency.py) 模拟并发锁；[test_store_data_isolation.py](../../../tests/test_store_data_isolation.py) 检查店铺范围。真实 MySQL 脚本会建表/迁移/写入，不在静态文档验证范围。

修改销售上下文会同时影响 [routers/reports.py](../../../routers/reports.py) 的店铺收入与产品线计算，不能只验证销售列表。

拆分回归：[test_sales_costs.py](../../../tests/test_sales_costs.py)覆盖原FIFO精度、冻结依据、补差/反向、小额舍入、多批次组合真实导入、手工销售、四种规则/生效历史、跨店共享安全库存、缺失汇率及异常空值，并勾稽列表/详情/Excel。[sales_cost_page_checks.cjs](../../../tests/sales_cost_page_checks.cjs)执行两份真实loadSales函数，验证金额/合计/未知传播/空态/转义/字典；无头浏览器夹具另验24列分组DOM和单件/整单解释、横向滚动、提示及下载。报表旧三组分配和跌价入口继续运行既有财务回归。

导出回归：[test_sales_export.py](../../../tests/test_sales_export.py)使用隔离SQLite/ASGI核对超过一页的完整记录、月份/名称/SKU/商品ID、全部月份、admin/operator/viewer权限及未绑定拒绝、独立财务事件勾稽、汇率/作废成本空值、追溯成本、Excel文本安全、空结果/超限与仅SELECT。[sales_export_page_checks.cjs](../../../tests/sales_export_page_checks.cjs)执行两份真实页面函数，覆盖筛选/无分页、重复点击、下载、错误恢复、旧401与正文/blob晚响应；[inventory_workspace_browser_checks.cjs](../../../tests/inventory_workspace_browser_checks.cjs)复用既有无头浏览器，另验两份销售页面的真实下载、文件名/查询参数、控件恢复及空结果提示。不启动main或连接业务数据库，不等于生产历史数据验收。

单件与解释入口：[sales-ledger.js](../../../static/sales-ledger.js)负责分组、公式问号、代入/批次/整单弹窗及会话代次；[sales-fields.zh-CN.json](../../../static/sales-fields.zh-CN.json)与[sales_fields.py](../../../services/sales_fields.py)为页面/Excel的单一字段说明资源。后端`_sale_response_rows/_order_cost_data/sale_cost_breakdown`复用原分项和回放，跨筛选取同店完整订单。回归[test_sales_cost_explanations.py](../../../tests/test_sales_cost_explanations.py)覆盖未舍入除法、同号跨店/旧绑定、映射缺失或冲突下整单ID/名称一致、未知范围、完整订单和导出资源；页面VM及浏览器夹具另覆盖待确认区域失败保留台账、合计/分页及重试恢复，VM覆盖旧会话失败隔离。原销售/财务回归继续执行。

来源与补录回归：[test_sales_name_import.py](../../../tests/test_sales_name_import.py)覆盖名称/倍数/组合、无SKU映射、精确/歧义识别、份额跨筛选一致、来源字段/Excel、赔付、补录权限/幂等/唯一键竞态/撤销不扣库；另验已识别退款的商品筛选/月度/导出/产品线一致、组合/未知退款不部分关联且不改变库存。[sales_manual_page_checks.cjs](../../../tests/sales_manual_page_checks.cjs)执行两份页面补录模块和实际api/刷新函数，验证精度、重复点击、网络重试，以及保存后五类子请求的旧响应/正文/401/网络异常在同账号重登及换账号后的隔离，当前会话刷新失败仍反馈。真实浏览器另验来源列、补录重试和布局，以及产品名联想的名称/SKU、点击/键盘/输入法、空值/失败/自由输入、移动端和旧请求隔离；选中不会改来源SKU ID。

其中SettlementOnlyImportTests覆盖同订单运费正负调整与商品销售并存、只扣一次库存/成本、全额退款及赠品区别、跨文件/跨月调整、组合财务事件不猜测份额、伪造元字段、重导去重、分页/导出及撤销数量。NameParserTests另验缺失依据和非有限金额，FinancialFactsTests核对标记事件、计数与损益勾稽，关联销售仍排除以免重复入账。
