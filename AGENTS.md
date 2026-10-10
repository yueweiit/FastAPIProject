# FastAPIProject 开发约定

本文件是项目级规则，补充全局开发规范。默认使用简体中文；文档与代码不一致时，以核实后的当前实现为准，不覆盖用户已有修改。

## 项目与上下文入口

- 系统是跨境电商 FIFO 库存及财务核算单体：FastAPI/Uvicorn、SQLAlchemy 2 异步 ORM、MySQL、Pydantic 2；前端为原生 HTML/CSS/JavaScript，没有独立前端构建项目。
- `main.py` 注册六组路由；业务部分位于 `routers/`，算法及外部数据服务位于 `services/`，表和契约为 `models.py`、`schemas.py`。OA PostgreSQL 只读取数与 Frankfurter 汇率是外部依赖。
- 先读 [知识库索引](docs/knowledge/README.md)，再按任务读取对应模块的 `overview.md`、`workflow.md`、`code-map.md`，按需补读 `database.md`、`api.md`，不要每次加载全部模块。

| 任务 | 模块入口 |
| --- | --- |
| 登录、账号、角色、店铺绑定 | [认证](docs/modules/auth/overview.md) |
| 商品、图片、图册、安全库存、类型规则 | [商品](docs/modules/products/overview.md) |
| 入库、批次、库存、成本、批次导出 | [库存](docs/modules/inventory/overview.md) |
| 销售、文件导入、FIFO、结算、汇率、撤销 | [销售](docs/modules/sales/overview.md) |
| 店铺、产品线、店铺/平台 SKU 映射 | [财务主数据](docs/modules/finance-masters/overview.md) |
| 存货、损益、产品线、快照、OA、财务 Excel | [报表](docs/modules/reports/overview.md) |

运行、配置、数据库或部署任务还应阅读 [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md)、[PROJECT_RUNBOOK.md](PROJECT_RUNBOOK.md)，部署另读 [DEPLOYMENT_HANDOFF.md](DEPLOYMENT_HANDOFF.md)。财务主数据、销售结算、成本和报表任务另读 [FINANCIAL_REPORTING_TARGET.md](FINANCIAL_REPORTING_TARGET.md)，区分目标与已实现功能、历史环境与当前状态。

## 修改与项目约束

1. 修改前检查 Git 状态；核对实际页面、路由、服务、模型、校验、权限、事务和上下游，只分析实际存在的层次。知识库提供定位，不替代源码核验。
2. 优先最小修改，不擅自增加依赖、重构架构或改变接口。商品/批次/销售列表不传 page 是数组、传 page 是分页对象，保持兼容。
3. 金额用 Decimal 与 DECIMAL；FIFO、导入、撤销、批次修改必须维持行锁和相同锁序，库存、销售、成本明细在同一事务内保持一致。勿随意改写历史成本或直接修正剩余库存。
4. 权限由服务端保证。viewer 当前可写商品与平台 SKU 映射；敏感销售/损益按店铺限制，但共享库存和独立存货接口有例外，修改前读权限文档。批次没有固化店铺字段，FIFO 当前按商品共享扣库。
5. 商品规则有生效历史；快照持久化不覆盖，但报表比较值会按规则历史重算。财务币种、成本分类、差额方向及分摊不得擅自改口径。
6. 实际首页为 `templates/index.html`；根目录 `index.html` 是副本且当前存在差异。页面修改先核对差异和用户改动，按任务范围决定同步，不能用副本替代运行模板。
7. 启动会建表、兼容迁移、修复历史数据、初始化用户和生成快照；部分 GET 报表也会写快照。不要把启动或报表请求当纯只读检查。
8. 不输出或保存密码、Token、JWT 密钥、私钥、完整连接串；保护 `.env`、`uploads/` 和实际数据库。代码存在默认安全配置，不能视为生产可用配置。
9. 未经明确授权，不提交/推送/发布，不操作生产部署/迁移，不删除业务数据、上传文件或 Docker 卷，不执行危险 Git 清理。重要不可逆操作须先说明影响、确认授权和备份/回滚条件。

## 验证与知识库维护

- 使用现有 unittest/IsolatedAsyncioTestCase，优先受影响测试；测试映射见 [conventions.md](docs/knowledge/conventions.md)。常用命令为 `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`，运行前确认已有环境，不在纯分析/文档任务中安装依赖或启动服务。
- `tests/mysql_fifo_concurrency.py` 会建表、迁移并写本地测试库，只在对应运行任务已授权且目标核实后执行；模拟锁测试不等于真实 MySQL/InnoDB 集成验证。
- 纯文档任务验证链接、源码路径/符号、表/接口覆盖和变更范围，不调用有写入副作用的入口。不能执行的检查如实说明。
- 业务、API、表、权限、核心算法或部署方式实质变化后，按需更新相关现有模块文档、全局索引及根目录维护文档；不因格式/注释小改动批量建文档。
- 字段字典维护于各模块 `database.md`，接口清单维护于 `api.md`，全局文档通过链接引用。重要决策按需放 `docs/development/`，大型方案按需放 `docs/plans/`；不重复维护或保存敏感信息。
- 交付报告修改文件、核心逻辑、影响、实际验证结果和待验证事项；不把旧记录或未运行测试描述为当前已验证事实。
