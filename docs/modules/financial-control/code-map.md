# 财务增强代码映射

| 能力 | 入口/符号 | 依赖 |
| --- | --- | --- |
| 开关/结构范围 | [finance_config.py](../../../services/finance_config.py)：financial_controls_enabled、FINANCE_TABLES | 环境变量；默认关闭 |
| 路由/鉴权/事务 | [financial_control.py](../../../routers/financial_control.py)：router、write | 既有RequireAnyRole/Operator/Admin、get_db |
| 审计/默认字段 | [financial_controls.py](../../../services/financial_controls.py)：audit、record | audit为异步；事务内flush并显式refresh未加载列，避免MySQL默认字段隐式IO |
| 凭证/校验/分摊 | [financial_controls.py](../../../services/financial_controls.py)：prepare_cost_adjustment、validate_adjusted_cost、post_cost_adjustment、reverse_cost_adjustment | 固定锁序、当前读、整数余数分摊、原分配反向、唯一约束、audit |
| 查询成本层 | [cost_layers.py](../../../services/cost_layers.py)：load_cost_layers、AdjustedBatch、sale_cost_context、adjusted_cost_expression | 分配/调整头；会话内缓存，写入后失效 |
| 已售批次绝对金额更正 | [batch_edits.py](../../../services/batch_edits.py)：freeze_cost_basis、restate_costs；routers/batches.py | 原单位成本依据、保留已售数、批次锁、差额凭证/日志同事务；独立补差权限不变 |
| 销售/库存/财务 | [sales.py](../../../routers/sales.py)、[batches.py](../../../routers/batches.py)、[products.py](../../../routers/products.py)、[reports.py](../../../routers/reports.py) | 原始FIFO＋补差、持有期间准备回放 |
| 费用与来源 | financial_controls.create_expense、post_expense、reverse_expense、ledger_manual_periods | financial_facts.manual_period_values；原人工JSON保留 |
| 分类/回款 | financial_controls.classify_settlement、create_receipt、reverse_receipt、reconciliation | 原结算差额；跨月付款ID核对 |
| 不可变版本 | [financial_versions.py](../../../services/financial_versions.py)：collect_payload、save_version、compare_version、version_workbook | 报表公共计算及Excel构建，冻结payload |
| 页面 | [financial-control.js](../../../static/financial-control.js)：FinanceControl.open/reset/text | 基础筛选与业务表单分开初始化；独立配置/店铺加载、批次失败提示及查询重试；原生fetch封装、CSRF、串号过滤旧响应、重复写入保护、集中zh-CN文案 |
| 更正/确认弹窗 | [page-interactions.js](../../../static/page-interactions.js)：reason、confirmSalesRollback、cancel | 页面内dialog、必填原因、取消不提交；同时服务销售撤销；固定路径/page-interactions.js |
| 契约/表 | [schemas.py](../../../schemas.py)、[models.py](../../../models.py) | Decimal；稳定内部状态；加法8表 |
| DDL/核验 | [add_financial_controls.py](../../../migrations/add_financial_controls.py) | 离线SQL输出；默认只读；显式指定隔离库加表 |
| 回归 | [test_financial_controls.py](../../../tests/test_financial_controls.py)、[financial_control_page_checks.cjs](../../../tests/financial_control_page_checks.cjs)、[mysql_financial_audit.py](../../../tests/mysql_financial_audit.py) | SQLite含关闭RETURNING的默认列读取/更新/回滚检查；VM覆盖开关关闭/启用、加载失败重试、批次失败、慢请求/重登及角色店铺范围，不等于浏览器或生产验收；MySQL脚本仅显式指定本机专用副本并回滚自建数据 |

main.py注册第八组路由，并以固定路径返回/financial-control.js。两份首页只同步本次入口/只读费用/重置逻辑，原有无关差异保留。数据库默认不开新表；配置启用后init_db允许创建这些表，故必须在启用前审核结构与启动副作用。
