# 项目知识库

本知识库帮助维护者理解跨境电商 FIFO 库存与财务核算系统，并作为后续 Codex 任务的项目上下文入口。初始化日期：2026-10-09；最近全局静态复核：2026-10-19。依据是仓库源码；历史运行结果按各自日期记录，不代表当前本机或生产数据库、服务和网络状态。

## 阅读顺序

初次了解项目：先读[项目概览](project-overview.md)、[业务地图](business-map.md)和[系统架构](architecture.md)，再按任务进入模块。修改前同时阅读根目录 [AGENTS.md](../../AGENTS.md)，核实实际源码；无需每次加载整个知识库。

| 全局文档 | 用途 |
| --- | --- |
| [project-overview.md](project-overview.md) | 项目定位、技术栈、入口、目录 |
| [architecture.md](architecture.md) | 系统关系、启动生命周期、事务与外部依赖 |
| [business-map.md](business-map.md) | 功能清单、模块依赖、尚未实现的目标 |
| [database.md](database.md) | 28张ORM表（其中8张默认关闭）、关系图、迁移与数据边界 |
| [data-dictionary.md](data-dictionary.md) | 模块字段字典导航、跨模块字段及状态编码 |
| [api-overview.md](api-overview.md) | 路由、调用关系、分页、错误与导出 |
| [authentication.md](authentication.md) | 登录、角色矩阵、店铺隔离和例外 |
| [internationalization.md](internationalization.md) | 中文界面现状、外部文本契约及渐进多语言约定 |
| [integrations.md](integrations.md) | EIMS、OA、汇率和平台文件的数据方向、责任及失败边界 |
| [deployment.md](deployment.md) | 源码定义的运行环境、配置和部署边界 |
| [conventions.md](conventions.md) | 现有代码习惯、验证要求与文档维护 |

## 模块入口

每个模块的 `overview.md` 索引其流程、代码映射、数据库和 API 文档。

| 模块 | 主要内容 | 入口 |
| --- | --- | --- |
| 认证与用户 | EIMS/本地登录、Cookie/CSRF、用户启停/管理、店铺绑定 | [auth/overview.md](../modules/auth/overview.md) |
| 商品 | SKU、Excel 批量新增、图片、图册、安全库存与商品类型历史 | [products/overview.md](../modules/products/overview.md) |
| 入库与库存 | 批次、采购和物流成本、库存编辑约束 | [inventory/overview.md](../modules/inventory/overview.md) |
| 销售与导入 | FIFO、平台文件、组合 SKU、人民币金额、待确认和撤销 | [sales/overview.md](../modules/sales/overview.md) |
| 财务主数据 | 店铺、产品线、店铺 SKU 和平台 SKU 映射 | [finance-masters/overview.md](../modules/finance-masters/overview.md) |
| 报表与快照 | 存货跌价、店铺损益、产品线、财务附件、OA 取数 | [reports/overview.md](../modules/reports/overview.md) |
| 财务增强 | 实际成本补差、费用台账、平台分类/回款、不可变月度版本 | [financial-control/overview.md](../modules/financial-control/overview.md) |

## 复用已有文档

以下文件沿用原位置及职责；已核实过时的描述增量修正，历史运行记录不作为当前环境证明：

- [PROJECT_ARCHITECTURE.md](../../PROJECT_ARCHITECTURE.md)：历史架构、改造基线和约束。
- [PROJECT_RUNBOOK.md](../../PROJECT_RUNBOOK.md)：运行维护、备份与历史环境记录。
- [DEPLOYMENT_HANDOFF.md](../../DEPLOYMENT_HANDOFF.md)：部署交接和发布流程。
- [FINANCIAL_REPORTING_TARGET.md](../../FINANCIAL_REPORTING_TARGET.md)：财务模板目标及待确认口径。

旧文档中的历史环境、计划和功能状态可能滞后；以核实后的代码为准。主要差异：当前已有财务主数据、结算导入和财务导出；批次编号使用最大序号及冲突重试；手工销售页面可填写店铺、售价和费用；敏感销售数据主要按店铺过滤；平台订单详情严格使用显式 SKU 映射。快照持久化不重写，但上月比较值会重新计算，详见[报表流程](../modules/reports/workflow.md)。

## 证据与维护规则

1. 业务规则由模块文档维护；全局文档只总结并链接。字段字典由各模块 `database.md` 维护，通过[数据字典导航](data-dictionary.md)检索；接口清单由各模块 `api.md` 维护。
2. 核心结论附相对代码链接及类名、函数名；日期只是最近核实时间，不保证代码以后没有变化。
3. 修改业务、接口、字段、权限、成本、快照或部署约定时，更新对应文档，并检查索引。重要开发决策可按需记录于 `docs/development/`，大型方案可按需记录于 `docs/plans/`，不为小改动批量创建文件。
4. 文档、测试和注释与代码不一致时，先核实当前实现，再注明差异；不得把目标文档中的未来对象描述为已有能力。
5. 不保存密码、Token、JWT 密钥、私钥、完整连接串或业务原始敏感数据。
6. 涉及页面、错误或导出文本先读[国际化](internationalization.md)；涉及外部调用先读[集成关系](integrations.md)。已知风险沿用[根目录风险清单](../../PROJECT_ARCHITECTURE.md#9-当前已知风险)，不重复建立问题台账。

## 待验证清单

2026-10-19四阶段实现与验证见[成本费用销售利润完善计划](../plans/2026-10-19-financial-accuracy.md)：已完成利润完整性、追溯成本、费用/结算核对及不可变月度版本的代码和隔离验证。财务增强默认关闭，目标MySQL结构与真实账单核验、启用/部署尚未执行。

| 项目 | 已知源码事实 | 待验证内容 |
| --- | --- | --- |
| 实际环境 | Docker 声明 Python 3.13；MySQL 为主库 | 本机/服务器 Python、数据库版本、InnoDB、字符集、时区、实际端口和可达性 |
| 数据库 | ORM 定义28表（8张财务增强表默认关闭）；批次修改新增基础日志表；启动迁移同时含DDL和历史修复 | 正式库字段、索引、外键、启动权限和历史数据；新增日志表结构准备 |
| EIMS 接入 | 已实现 PKCE/RS256、后端加密 Token、Cookie/CSRF、绑定/启停、刷新与退出；本地验证通过 | 客户端注册/安全配置、正式用户绑定、门户 start 配合、真实撤权/刷新/退出及日志保护，见[接入说明](../modules/auth/eims-sso-setup.md) |
| 权限 | 商品和平台 SKU 为共享数据；独立存货接口未按店铺过滤 | 这些权限是否符合业务；库存归属是否需要独立店铺字段 |
| 财务口径 | 07/09/10/12按2026-10-15追加确认采用实际持有期间计提及销售转销，新增三项变动，账面=现存成本−期末准备；异常金额传播null | 真实历史规则/流水完整性、生产更新及其他成本分类；快照保留原算法；新口径、跨店及历史待核对边界见[变更记录](../development/2026-10-15-holding-period-impairment.md) |
| 汇率与 OA | 汇率使用 Frankfurter；OA 只执行 SELECT | 外网可用性、汇率来源批准、OA 只读账号权限及表结构/表单组件 |
| 历史报表 | 保存快照；报表比较值可按规则历史重算 | 重复导出与迟到数据、撤销、改绑用户后的审计口径 |
| 平台对接 | 当前支持文件导入；未见授权和订单同步实现 | TikTok API 权限、订单/退款/结算确认规则，见目标文档 |
| 并发 | FIFO和撤销有行锁；order_no普通索引保留，新增order_claim唯一索引兜底新订单；本地InnoDB并发验证通过 | 生产新增结构、全部写入进程更新、批量导入锁等待及跨店同号策略，见[修复记录](../development/2026-10-16-project-safety-fixes.md) |
| 页面副本 | templates/index.html 与根目录 index.html 当前内容不完全一致；2026-10-12 已同步商品导入功能块 | 后续前端任务仍需核对平台 SKU 等已有差异，按授权范围同步 |
| 国际化 | 当前为中文硬编码界面，无统一翻译/语言偏好；外部表单名参与匹配 | 目标语言、默认/回退策略、业务名称翻译与业务时区；见[国际化规范](internationalization.md) |
| 容量与审计 | 销售文件整体读入、报表内存回放/Excel生成；已有来源/撤销记录及默认关闭的财务增强审计；未统一覆盖所有业务操作 | 实际文件/历史规模、并发锁等待、查询及导出资源上限；见[架构](architecture.md#复用能力与容量边界) |

## 初始化验证记录（历史静态检查）

### 2026-10-19 增量复核

复用六个模块和既有全局文档，补充数据字典导航、国际化及系统集成入口；修正现行跌价算法、商品规则依赖、批次编号及 Compose 配置描述。模块字段引用改为路径和模型名，移除已失效的固定源码行号。

静态核对19张ORM表、217个持久化字段的类型/可空性、64个业务API方法与路径，均在模块文档中覆盖；数据库关系图29条边均有ORM外键依据。检查相对链接/标题锚点、代码映射符号及15个Mermaid图块结构；Mermaid未做渲染器验证。未读取实际.env、连接数据库、请求外部服务、运行应用或业务测试；历史集成测试记录不作为本轮结果。

### 2026-10-09 初始化

- 新建40份文档：全局9份、六个模块各5份、根目录AGENTS.md；未覆盖已有根目录文档。
- 检查441个本地链接、模块代码映射符号和Markdown结构；17张ORM表的194个字段、52个业务API均有对应记录，无缺漏或重复字段字典。
- 对35个项目Python文件做AST静态解析；11段Mermaid代码块做围栏/结构检查，未启动服务或安装渲染依赖进行图形渲染。
- 对任务开始时记录的48个原有文件复核SHA256，内容全部保持不变；原有未提交修改保留，新增文件均为本知识库和AGENTS.md。

本次仅静态分析与文档检查；未读取实际 `.env` 内容，未连接数据库，未执行业务测试、依赖安装、服务启动、迁移或部署。已有测试的存在不代表本次执行通过。

## 2026-10-13 认证接入更新

按用户批准的[接入方案](../plans/2026-10-13-eims-sso-integration.md)完成代码和相关文档，同步认证模块、当前表/API 数量及配置约束。已执行离线协议/权限回归、两份页面辅助函数与真实本地 MySQL 并发验证；8010 商品和存货查询、CSRF 拒绝、Cookie 退出验证通过。正式 EIMS 仍待注册配置及端到端验收；详细结果在方案实施记录维护，不将初始化时的静态检查当作本次运行验证。

2026-10-14 修复审查发现的改密竞态、轮换后故障恢复及延迟退出回调三项问题；全量160项测试及真实本地InnoDB双向用户锁验证通过。详细逻辑、验证和未完成的正式联调继续由[方案修复记录](../plans/2026-10-13-eims-sso-integration.md#9-2026-10-14-审查问题修复)及[认证流程](../modules/auth/workflow.md)维护；未新增表/字段或生产迁移。

## 2026-10-16 安全与财务一致性修复

收紧商品图片内容与静态访问；新订单数据库防重；历史销售按到货日筛选；损益页面保存绑定已加载月份/店铺并隔离过期响应，新增current保存范围保留人工比较值；09/10/12纳入入库其他成本。字段、业务流程和回归入口已同步对应模块。实现、已授权本地结构验证及正式环境待办统一见[修复记录](../development/2026-10-16-project-safety-fixes.md)。
