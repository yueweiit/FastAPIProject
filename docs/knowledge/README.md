# 项目知识库

本知识库帮助维护者理解跨境电商 FIFO 库存与财务核算系统，并作为后续 Codex 任务的项目上下文入口。初始化日期：2026-10-09；依据是当前工作区源码，包含任务开始前已有的未提交修改。本文档描述源码实现，不代表已核验本机或生产数据库、服务和网络状态。

## 阅读顺序

初次了解项目：先读[项目概览](project-overview.md)、[业务地图](business-map.md)和[系统架构](architecture.md)，再按任务进入模块。修改前同时阅读根目录 [AGENTS.md](../../AGENTS.md)，核实实际源码；无需每次加载整个知识库。

| 全局文档 | 用途 |
| --- | --- |
| [project-overview.md](project-overview.md) | 项目定位、技术栈、入口、目录 |
| [architecture.md](architecture.md) | 系统关系、启动生命周期、事务与外部依赖 |
| [business-map.md](business-map.md) | 功能清单、模块依赖、尚未实现的目标 |
| [database.md](database.md) | 17 张 ORM 表、关系图、迁移与数据边界 |
| [api-overview.md](api-overview.md) | 路由、调用关系、分页、错误与导出 |
| [authentication.md](authentication.md) | 登录、角色矩阵、店铺隔离和例外 |
| [deployment.md](deployment.md) | 源码定义的运行环境、配置和部署边界 |
| [conventions.md](conventions.md) | 现有代码习惯、验证要求与文档维护 |

## 模块入口

每个模块的 `overview.md` 索引其流程、代码映射、数据库和 API 文档。

| 模块 | 主要内容 | 入口 |
| --- | --- | --- |
| 认证与用户 | JWT、用户管理、店铺绑定 | [auth/overview.md](../modules/auth/overview.md) |
| 商品 | SKU、Excel 批量新增、图片、图册、安全库存与商品类型历史 | [products/overview.md](../modules/products/overview.md) |
| 入库与库存 | 批次、采购和物流成本、库存编辑约束 | [inventory/overview.md](../modules/inventory/overview.md) |
| 销售与导入 | FIFO、平台文件、组合 SKU、人民币金额、待确认和撤销 | [sales/overview.md](../modules/sales/overview.md) |
| 财务主数据 | 店铺、产品线、店铺 SKU 和平台 SKU 映射 | [finance-masters/overview.md](../modules/finance-masters/overview.md) |
| 报表与快照 | 存货跌价、店铺损益、产品线、财务附件、OA 取数 | [reports/overview.md](../modules/reports/overview.md) |

## 复用已有文档

以下文件保持原位置、原内容，不迁移、不覆盖任务开始前的修改：

- [PROJECT_ARCHITECTURE.md](../../PROJECT_ARCHITECTURE.md)：历史架构、改造基线和约束。
- [PROJECT_RUNBOOK.md](../../PROJECT_RUNBOOK.md)：运行维护、备份与历史环境记录。
- [DEPLOYMENT_HANDOFF.md](../../DEPLOYMENT_HANDOFF.md)：部署交接和发布流程。
- [FINANCIAL_REPORTING_TARGET.md](../../FINANCIAL_REPORTING_TARGET.md)：财务模板目标及待确认口径。

旧文档中的历史环境、计划和功能状态可能滞后；以核实后的代码为准。主要差异：当前已有财务主数据、结算导入和财务导出；批次编号使用最大序号及冲突重试；手工销售页面可填写店铺、售价和费用；敏感销售数据主要按店铺过滤；平台订单详情严格使用显式 SKU 映射。快照持久化不重写，但上月比较值会重新计算，详见[报表流程](../modules/reports/workflow.md)。

## 证据与维护规则

1. 业务规则由模块文档维护；全局文档只总结并链接。字段字典由各模块 `database.md` 维护，接口清单由各模块 `api.md` 维护。
2. 核心结论附相对代码链接及类名、函数名；日期只是最近核实时间，不保证代码以后没有变化。
3. 修改业务、接口、字段、权限、成本、快照或部署约定时，更新对应文档，并检查索引。重要开发决策可按需记录于 `docs/development/`，大型方案可按需记录于 `docs/plans/`，不为小改动批量创建文件。
4. 文档、测试和注释与代码不一致时，先核实当前实现，再注明差异；不得把目标文档中的未来对象描述为已有能力。
5. 不保存密码、Token、JWT 密钥、私钥、完整连接串或业务原始敏感数据。

## 待验证清单

| 项目 | 已知源码事实 | 待验证内容 |
| --- | --- | --- |
| 实际环境 | Docker 声明 Python 3.13；MySQL 为主库 | 本机/服务器 Python、数据库版本、InnoDB、字符集、时区、实际端口和可达性 |
| 数据库 | ORM 定义 17 表；启动迁移同时含 DDL 和历史数据更新 | 实库字段、索引、外键及历史数据是否与模型一致 |
| 权限 | 商品和平台 SKU 为共享数据；独立存货接口未按店铺过滤 | 这些权限是否符合业务；库存归属是否需要独立店铺字段 |
| 财务口径 | 新品每日 0.5%，稳健商品每日 1%；比较差额为本期减上期 | 财务是否批准；与旧目标模板、尾程/其他成本分类的差异 |
| 汇率与 OA | 汇率使用 Frankfurter；OA 只执行 SELECT | 外网可用性、汇率来源批准、OA 只读账号权限及表结构/表单组件 |
| 历史报表 | 保存快照；报表比较值可按规则历史重算 | 重复导出与迟到数据、撤销、改绑用户后的审计口径 |
| 平台对接 | 当前支持文件导入；未见授权和订单同步实现 | TikTok API 权限、订单/退款/结算确认规则，见目标文档 |
| 并发 | FIFO 和撤销有行锁；订单号只有普通索引 | 真实 MySQL 并发、批量导入锁等待、跨店重复事件和幂等策略 |
| 页面副本 | templates/index.html 与根目录 index.html 当前内容不完全一致；2026-10-12 已同步商品导入功能块 | 后续前端任务仍需核对平台 SKU 等已有差异，按授权范围同步 |

## 初始化验证记录

- 新建40份文档：全局9份、六个模块各5份、根目录AGENTS.md；未覆盖已有根目录文档。
- 检查441个本地链接、模块代码映射符号和Markdown结构；17张ORM表的194个字段、52个业务API均有对应记录，无缺漏或重复字段字典。
- 对35个项目Python文件做AST静态解析；11段Mermaid代码块做围栏/结构检查，未启动服务或安装渲染依赖进行图形渲染。
- 对任务开始时记录的48个原有文件复核SHA256，内容全部保持不变；原有未提交修改保留，新增文件均为本知识库和AGENTS.md。

本次仅静态分析与文档检查；未读取实际 `.env` 内容，未连接数据库，未执行业务测试、依赖安装、服务启动、迁移或部署。已有测试的存在不代表本次执行通过。
