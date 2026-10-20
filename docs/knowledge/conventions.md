# 现有编码与维护约定

## 实际代码习惯

| 项目 | 当前实现 | 依据 |
| --- | --- | --- |
| 文件组织 | 根目录组合应用/模型/契约，routers 按业务前缀，services 保存部分复杂算法 | [main.py](../../main.py)、[routers/](../../routers/)、[services/](../../services/) |
| 命名 | Python snake_case 函数与变量，PascalCase 类；辅助函数常以 _ 开头；注释中英混用 | [batches.py](../../routers/batches.py)、[fifo.py](../../services/fifo.py) |
| HTTP | APIRouter + Depends(get_db/角色依赖)，HTTPException，Pydantic response_model | [auth.py](../../routers/auth.py)、[schemas.py](../../schemas.py) |
| ORM | SQLAlchemy 2 的 Mapped/mapped_column，异步 select、显式 flush/commit/rollback | [models.py](../../models.py)、[sales.py](../../routers/sales.py) |
| 金额 | Decimal 计算、DECIMAL 存储；不可把金额算法改为二进制 float | [fifo.py](../../services/fifo.py)、[reports.py](../../routers/reports.py) |
| 前端 | 单文件内嵌 CSS/JS，camelCase 函数，原生 fetch、同源相对 API | [templates/index.html](../../templates/index.html) |
| 测试 | unittest、IsolatedAsyncioTestCase、mock/模拟会话，部分签名/源码约束检查 | [tests/](../../tests/) |

未发现 pyproject.toml、格式化/静态检查配置、前端打包配置或 CI 文件；不要宣称已有 Ruff、Black、pytest 或 CI 门禁。依赖以 requirements.txt 为准，不擅自增加框架。

## 修改前必须核实

1. 检查 Git 状态，识别用户已有修改；从[索引](README.md)读相关模块的 overview、workflow、code-map，再按需读数据库/API。
2. 沿实际存在的页面、路由、服务、模型和外部依赖定位，不套用不存在的 Controller 或前端组件层。
3. 核查接口分页兼容、角色/店铺范围、正数数量、非负金额、事务和日期口径。
4. FIFO、导入、撤销和批次修改需共同检查 remaining_quantity、sales、sale_cost_details。涉及行锁必须保持共同锁序及调用方事务边界。
5. 运行首页是 templates/index.html；根目录 index.html 为副本，修改页面时核对同步与差异。服务端依赖是权限依据，按钮显隐不能替代鉴权。
6. 重要变化按需同步已有根目录文档与模块文档；仅格式、注释或无实质影响的变化不批量写新文档。

## 验证选择

用户可见文本、状态标签及导入/导出语言边界见[国际化规范](internationalization.md)；当前无通用翻译框架，不假设已有语言资源。外部接口改动先查[集成关系](integrations.md)，字段/状态变更先从[数据字典导航](data-dictionary.md)进入唯一维护入口。

本节记录后续代码开发的验证要求，不表示知识库初始化执行了业务测试。

| 影响范围 | 优先验证 |
| --- | --- |
| 认证/商品权限/店铺隔离 | test_user_creation_permissions、test_product_feature_permissions、test_store_data_isolation |
| EIMS/Cookie/CSRF/启停/刷新/退出 | test_eims_sso；node tests/auth_page_checks.cjs；授权后 mysql_auth_sessions 专用本地验证 |
| 商品批量新增/模板/预览/结果 | test_product_import、test_product_feature_permissions；必要时授权的 mysql_product_import 专用本地库验证 |
| 商品图片/历史上传隔离/失败清理 | test_product_images（临时目录、独立ASGI应用，不运行main生命周期） |
| FIFO/组合分摊/汇率/导入格式/订单防重 | test_sales_rules、test_fifo_concurrency、test_sales_integrity；必要时授权的mysql_sales_integrity本地InnoDB验证 |
| 商品规则/跌价/快照/批次成本及SKU汇总 | test_inventory_provision_movements、test_provision_reporting、test_inventory_impairment_report（快照历史）、test_inventory_cost_summary、test_accounting_period_snapshots、test_accounting_period_removal；node tests/inventory_page_checks.cjs |
| 店铺损益/OA/财务附件 | test_store_profit_loss_report、test_provision_reporting、test_oa_office_expenses、test_financial_attachments_export；node tests/profit_loss_page_checks.cjs |
| 独立结算、人工年累计、缺失汇率及跨入口勾稽 | test_financial_facts（独立内存SQLite，不连接业务MySQL）；test_sales_rules；覆盖列表分页和Excel未知合计 |
| 前端交互 | 核对模板与副本；在获准运行的环境检查分页、选择、表单、错误和导出 |
| 纯文档 | 链接/代码路径/符号/表和路由覆盖检查，确认旧文件和业务代码未改动 |

测试文件均位于 [tests/](../../tests/)。全量单元测试常用命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

[tests/mysql_fifo_concurrency.py](../../tests/mysql_fifo_concurrency.py) 会连接并写入特定本地测试库，虽有目标限制和清理逻辑，也不能当纯只读检查或默认执行。普通测试主要使用模拟数据，不等同于目标 MySQL/InnoDB 的集成验证。运行前确认依赖已存在，不为文档任务安装依赖或启动应用。

[tests/mysql_sales_integrity.py](../../tests/mysql_sales_integrity.py) 默认只读检查字段/索引；显式`--apply-local-order-schema`才补可空订单占位列及唯一索引、创建/清理自建商品批次销售，核对既有业务表摘要。严格限定127.0.0.1:3309 / inventory_fifo_test / fifo_local_app，需明确本地结构及写入授权；结构保留、自增推进，不回填历史记录，也不执行完整启动迁移。验证不同商品的同订单并发、组合多明细、失败重试及未来批次排除；不得连接生产或自动纳入unittest。

[tests/test_eims_sso.py](../../tests/test_eims_sso.py) 使用模拟提供方、RSA/JWKS 和独立临时 SQLite，不进入 main 生命周期；SQLite 锁仅模拟。`node tests/auth_page_checks.cjs` 验证两份页面脚本和认证辅助函数，不能替代真实浏览器联调。

[tests/mysql_auth_sessions.py](../../tests/mysql_auth_sessions.py) 必须显式 `--apply-local-auth-schema`，且只允许 MYSQL_HOST=127.0.0.1、MYSQL_PORT=3309、MYSQL_DATABASE=inventory_fifo_test、MYSQL_USER=fifo_local_app；会按需补认证结构，写入并清理脚本自建用户/认证记录，对比既有业务表。覆盖一次性消费、并发刷新、轮换后故障恢复，以及 User → AuthSession 锁下“先创建/先改密”的两种 InnoDB 竞争顺序；密码变更只作用于自建测试用户。结构保留、自增序列正常推进，不可作为只读检查或生产脚本。脚本使用现有安全环境配置，不将凭据打印或写入验证报告。

[tests/mysql_product_import.py](../../tests/mysql_product_import.py) 独立读取 PRODUCT_IMPORT_TEST_HOST/PORT/DATABASE/USER/PASSWORD，不复用业务库配置；只允许 127.0.0.1 和以 inventory_fifo_product_import_validation_ 开头的显式专用库。需要事先创建该库并授权，运行后保留测试表/数据，不删除业务数据；不可自动纳入常规 unittest。使用进程环境传递凭据，不把密码写进命令或验证输出。

## 必须保持的业务约束

- 库存扣减、销售和成本明细在同一事务；批次行锁等待后复查最新数据。
- 平台结算事件保存原始 JSON、source_key、来源行、币种及历史汇率，不随意改写已确认成本。
- 商品类型/安全库存用生效历史参与跌价计算；月末快照不覆盖，比较值的重算行为见[报表流程](../modules/reports/workflow.md)。
- 金额、成本分类、费用分摊、差额正负号及店铺归属需要遵循当前实现和已确认业务口径；不把旧目标替换为已实现规则。
- 不读取或输出无关敏感信息；不提交、推送、安装、部署、迁移或执行破坏性操作，除非任务授权相应范围。
- SSO 使用独立认证事务/行锁，不能把认证锁延长到 FIFO；Cookie 写请求覆盖 api 与直接 fetch。app_user_id 只匹配本地 users.id，EIMS 角色不得提升本地权限；Token 只后端加密，认证 URL 不记录查询参数。

新任务应从[项目 AGENTS.md](../../AGENTS.md)开始，用代码验证知识库内容；发现过期记录时更新对应唯一维护入口。

## 文档静态核验方法

1. 先记录 Git 状态；本任务只改 Markdown 时，结束用 `git diff --name-only`、`git status --short` 核查修改及新增范围，用 `git diff --check` 检查空白错误。
2. 以 Python 标准库 AST 解析 main.py 的路由注册、routers 的方法/前缀/路径、models.py 的表/字段/外键，与模块 api.md/database.md 按方法+路径、表+字段逐项匹配；不导入应用来生成清单。
3. 校验相对文件/目录链接及 Markdown 标题锚点；核对 code-map.md 中前后端符号。引用路径和符号优先于容易失效的源码行号。
4. Mermaid 至少核对围栏、图类型及实体/流程是否与代码相符；未使用渲染器时必须明确只做结构检查，不能称为渲染验证通过。
5. 仅扫描文档新增内容中的凭据/连接串风险，不读取实际 .env 或原始业务数据。历史测试数字保留其日期，不作为本轮已执行证据。

## 财务增强回归

- `.\.venv\Scripts\python.exe -m unittest tests.test_sales_import_store -v`：隔离SQLite/ASGI验证导入店铺校验、管理员/运营/查看者边界、批次/销售/待确认归属、待确认导出店铺；不是InnoDB锁验证。`node tests/sales_import_page_checks.cjs`验证两份实际页面店铺选择、重复导入防护、过期加载和会话切换，不代替真实浏览器验收。

- `node tests/page_interactions_checks.cjs`：两份实际页面新增/编辑批次函数在上海、UTC、墨西哥城三种进程时区的午夜边界与重复编辑；未改日期保留秒数；撤销取消、连点及会话切换不提交。页面内dialog的键盘/焦点/显示仍需真实浏览器验收。

- `.\.venv\Scripts\python.exe -m unittest tests.test_financial_controls tests.test_financial_facts -v`：独立内存SQLite，验证真实ORM财务链路，不启动main生命周期。
- `node tests/financial_control_page_checks.cjs`：实际工作台函数的VM检查，含连续切换/过期响应/重复点击/金额字符串/表单变化/退出；不等于浏览器视觉验收。
- `tests/mysql_financial_audit.py --run-local-finance-checks`：先通过进程环境显式设置 `FINANCE_TEST_HOST/PORT/DATABASE/USER/PASSWORD`。仅允许 `127.0.0.1:3309` 且数据库与账号均以 `fifo_browser_` 开头的既有专用副本；不读取业务库凭据作为测试连接，不创建/迁移结构，不启动main，不访问OA。所有随机前缀商品/库存/凭证/审计在同一事务中验证后回滚，自增序列可能推进。覆盖六类默认时间字段、来源onupdate、幂等、费用确认、三类冲销、旧版保留/比较/导出。须事先核实目标并有本地写入测试授权，不自动纳入unittest，不能用于业务库或生产。
- `migrations/add_financial_controls.py --write-sql ...`只离线生成DDL；省略参数默认只读核验指定隔离库，显式apply才加表，前置授权与目标保护见[运行步骤](../../PROJECT_RUNBOOK.md#财务增强结构与启用)。不复用旧并发脚本启动全库迁移。
