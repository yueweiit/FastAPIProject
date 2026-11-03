# 操作日志代码与测试

| 能力 | 源码 | 来源 |
| --- | --- | --- |
| 选项/列表/详情 | [routers/operation_logs.py](../../../routers/operation_logs.py) | RequireAdmin；服务层查库前再次拒绝非管理员，保留来源/日期及开关校验 |
| HTTP缓存策略 | [log_http.py](../../../services/log_http.py) | PrivateLogRoute仅匹配四个日志读取路由，在响应发送阶段设置private/no-store；BusinessAuditRoute继承策略，不改变业务审计和其他接口 |
| 聚合及摘要 | [services/operation_logs.py](../../../services/operation_logs.py) | 按权限构建UNION ALL，计数后分页，一页按来源批量补摘要，无逐行详情查询 |
| 详情投影 | 同上detail/safe_values/finance_values | 按财务实体投影真实字段，摘要/详情一致；未知旧值、同次操作编号 |
| 业务捕获 | [business_audit.py](../../../services/business_audit.py) | 路由policy、FIELDS、身份上下文、Session事务事件；提交前批量取实际落库值，失败回滚 |
| 写入入口 | auth/products/batches/sales/finance_masters/reports路由的BusinessAuditRoute | 保留既有依赖/权限，新增可选审计；已存在的历史来源不重复记录 |
| 模型/结构 | [models.py](../../../models.py)、[离线DDL](../../../migrations/add_business_operation_logs.py)、[database.py](../../../database.py) | 新表默认不创建，开关开启后原init_db纳入；上线前仍先按授权预检及准备单表 |
| 页面与局部字典 | [operation-logs.js](../../../static/operation-logs.js) | 分类/操作/字段/实体标签，筛选分页、差异详情、历史类型说明、移动端、会话与旧请求保护 |
| 导航/样式/重置 | [templates/index.html](../../../templates/index.html)、[index.html](../../../index.html) | 仅管理员显示导航，移除批次日志按钮；退出/降权清空内容 |
| 注册/脚本回退 | [main.py](../../../main.py)、[page-interactions.js](../../../static/page-interactions.js) | 新只读路由、operation-logs.js，旧后端提示及导航标签回退 |

新增表字典见[database.md](database.md)。批次原始数据见[库存字典](../inventory/database.md#batch_change_logs)，导入/撤销见[销售模块](../sales/overview.md)，可选财务审计见[财务增强](../financial-control/overview.md)。

[test_business_audit.py](../../../tests/test_business_audit.py)用隔离SQLite及实际ASGI路由验证前后快照、批量一组、数据库金额精度、未知旧值、保存点、账号密码排除、删除后保留、关键信息写入，以及审计失败的FIFO库存/销售/成本/待确认回滚；不连接真实库，不证明InnoDB并发或DDL权限。日志查询回归另含新来源权限、组筛选、旧SKU搜索、关闭捕获仍读历史、缺可选表的兼容及启用后503。

2026-11-03修复回归增加旧组件替换/删除前值、同次分组、跨店详情拒绝、仅删除时的来源店铺、审计失败恢复原组件及FIFO；复用已锁对象并在删除后flush，兼顾审计与唯一约束。财务回归调用实际来源切换、结算分类和版本保存服务，再读取统一API，核对mode、breakdown和version的摘要/详情及私密字段过滤。浏览器在两份页面验证三类真实字段和操作码的中文标签、数值及筛选。

本次修复验证：全量611项Python测试通过，审计/查询29项针对性回归通过，两份页面111项浏览器检查通过；新增用例还覆盖来源归属为空时不继承管理员店铺、月度版本待核对状态和计算口径标签。复审未发现新的阻塞问题。仅修改代码、隔离测试及文档，未执行真实库结构/数据变更、服务重启或生产操作；本次无新增DDL，真实MySQL并发写入及运行页面生效仍需按环境验证。

本机.env启用审计后，运行既有隔离测试时在测试进程设置`$env:FIFO_OPERATION_AUDIT_ENABLED='0'`再执行全量unittest，避免本机运行配置影响旧接口模拟Session；新增审计测试会在自身用例显式开启1并验证真实Session事务。此设置仅针对测试进程，不改写.env或运行服务，不关闭新审计用例。结构核验和页面生效仍须分别确认。

2026-11-02本轮验证：全量604项Python测试通过，新增审计及查询22项回归通过；两份页面87项浏览器检查、认证204项及旧批次日志23项通过。真实本地MySQL仅执行获准单表DDL和只读结构/聚合查询核验，确认新表、配置、扩展来源及组合SQL可用；未写入测试业务记录，未启动main或重启8010，真实页面业务写入仍待用户重启后验证。

[test_operation_logs.py](../../../tests/test_operation_logs.py)在隔离SQLite/ASGI覆盖聚合计数、分页夹页、筛选及通配符字面量、UTC/原时间标识、前后值和身份快照、删除批次保留、盘点/导入差异、三角色/未绑定/跨店/历史日期及详情越界、财务开关、私密字段过滤、缺结构503、只SELECT和MySQL查询编译；不证明真实MySQL性能或并发。

[operation_logs_browser_checks.cjs](../../../tests/operation_logs_browser_checks.cjs)运行两份真实页面及局部模块，用模拟API验证管理员筛选/分页/详情、字段转义、移动端、失败恢复、旧会话隔离，以及非管理员导航隐藏、直接调用不请求、降权后迟到响应丢弃和批次列表无日志按钮。旧[batch_log_page_checks.cjs](../../../tests/batch_log_page_checks.cjs)验证仅管理员可用的兼容弹窗；[test_batch_edit.py](../../../tests/test_batch_edit.py)验证旧日志API也拒绝运营/查看者。均不启动main生命周期，不连接业务库。

2026-11-03按要求将操作日志改为管理员专属，并移除批次列表的独立日志按钮。全量611项Python测试、日志与批次48项针对性回归、两份页面175项浏览器检查通过；认证204项、旧批次弹窗23项及批次页面236项检查通过。覆盖非管理员各种店铺绑定下的全部日志API拒绝、服务层查库前拒绝、管理员旧日志读取和原业务写权限保留。无新结构、日志删除、历史回填、实际库变更或服务重启；获准环境需加载后端与共享脚本并刷新页面。

缓存回归另见[operation_logs_cache_checks.cjs](../../../tests/operation_logs_cache_checks.cjs)：启动[log_cache_server.py](../../../tests/log_cache_server.py)的临时127.0.0.1服务，使用实际日志路由及隔离SQLite、测试Cookie身份；不启动main生命周期、不连接业务库，不拦截浏览器网络请求。可缓存对照组先复现旧200数据命中，再验证四个真实接口在降权、切换账号及退出后force-cache请求均重新到达服务器并获403/401。其他前端回归检查两份页面cache参数透传及管理员/会话保护；日志策略不改变普通批次列表缓存响应。

2026-11-03缓存修复验证：全量615项Python测试、50项日志/批次针对性回归通过；两份页面179项浏览器检查、真实HTTP缓存53项、认证助手210项及旧日志助手24项检查通过。响应头覆盖成功、权限、参数校验及来源错误，JSON结构和其他业务API缓存保持；复审未发现新增阻塞问题。测试服务已退出，无实际库迁移/写入或业务服务重启。新策略不追溯清除旧浏览器缓存，升级步骤仍需手动清理旧缓存并刷新。
