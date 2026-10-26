# FastAPIProject 运行与维护说明

> 强制约定：以后每次运行、部署、排障或修改本项目之前，先完整阅读本文件。代码、配置或部署方式变化后，同步更新本文件。

> 财务报表改造：涉及店铺、TikTok Shop 同步、订单/结算/费用、库存成本或四张财务 Excel 导出时，还必须阅读 `FINANCIAL_REPORTING_TARGET.md`。该文件定义目标模板、字段映射、待确认财务口径和实施顺序。

## 批次跨创建人编辑与修改日志

2026-10-26起，admin/operator可编辑任意创建人的历史批次并同事务写batch_change_logs；后续已开放已出库及已有补差批次的数量、金额和资料更正，处理规则见下节。原创建人、商品和库存归属不变；operator删除仍仅限原创建人，已出库或已有补差不能删除。页面“日志”可只读核对前后值。

此表属于默认基础结构，**不依赖财务增强开关**。发布必须同时更新后端模型、路由、契约、共享交互脚本及两份页面；只改HTML无法完成授权和留痕。`init_db`会创建缺失日志表，但启动还包含其他既有副作用，不能用启动应用代替生产预检。

1. 离线生成供审核的DDL（不连接数据库）：`python migrations/add_batch_change_logs.py --write-sql output/batch_change_logs.sql`。
2. 正式更新前核实目标库，按既有流程备份并审核新表、InnoDB、utf8mb4、索引和数据库权限；经生产变更授权后再执行DDL及部署。本轮代码开发未操作生产结构或历史业务数据。
3. 表为空时日志页显示暂无记录；不补造历史修改日志。日志保存失败必须让本次批次修改回滚，不能关闭日志校验强行保存。
4. 日志无级联外键，账号改名/删除及批次删除后保留当时身份和批次编号。回退应用版本时保留新增表和日志；旧版可编辑批次但没有此留痕能力，勿让新旧写入进程长期混跑。

同日并发修复：编辑用load_cost_layers(lock=True)、删除用ensure_batch_cost_editable，在批次锁内当前读调整来源，避免RR旧快照漏检；日志刷新也用当前读。edit_version覆盖批次及调整明细，缺少版本428、过期409。计算版本本身无需列，已售成本重述另需下节cost_basis列。

更新时必须统一后端、`static/page-interactions.js`、两份页面及外部PUT调用方。旧浏览器需刷新后重新打开编辑，不能省略版本或自动取新版本重试旧表单；新旧后端写进程不能混跑，以免旧进程绕过并发校验。详细契约和回归边界见[库存API](docs/modules/inventory/api.md#编辑并发保护)和[代码映射](docs/modules/inventory/code-map.md)。本轮自动回归使用隔离SQLite/模拟RR与页面VM，不作为真实MySQL锁等待或生产部署证据。

## 已出库批次编辑与成本重述

2026-10-26按用户追加确认开放数量、采购/物流金额及单号。保存必须保留实际已售数，新余量=新入库数−已售数，数量不能小于已售，入库日不能晚于最早销售日。首次重述在inventory_batches.cost_basis冻结原数量和成本构成，原unit_cost及FIFO明细单价不回写；新金额转为追加差额凭证，追溯原销售月。旧月度版本保留，复核后另存新版。

1. 离线生成加法列DDL：`python migrations/add_batch_cost_basis.py --write-sql output/batch_cost_basis.sql`。不连接数据库、不执行迁移、不回填历史数据；migrate.py也包含兼容补列，但启动仍有其他写入，不能代替预检。
2. 获准更新目标环境前，备份并确认cost_basis JSON NULL、日志表及8张财务增强表已准备，按[财务增强结构与启用](#财务增强结构与启用)启用FIFO_FINANCIAL_CONTROLS_ENABLED。本轮未迁移或启用生产。关闭时只开放单号/发货资料等不改成本的更正；受保护数量/成本修改明确返回503，不假装完成。
3. 统一后端和两份页面/共享JS，外部PUT调用方须采用edit_values回填有效金额，提交打开时edit_version及cost_edit_mode=restate。未声明模式428，防止旧页面拿原字段覆盖已确认成本；没有新增用户确认弹窗。
4. 修改与数量、原成本依据、差额凭证、财务审计和批次日志统一提交，任何失败全回滚。自动差额不等于四项账单已核对；凭证按四位小数保存，单位分摊有正常尾差。
5. 数量更正后的旧补差冲销按原分配行quantity校验与取反，不能用新数量摊薄负成本检查。旧代码不认识原成本依据和新编辑协议，不能与新写入进程混跑，也不能通过关闭财务功能隐藏已有调整。

回归入口：[test_batch_restatement.py](tests/test_batch_restatement.py)、库存[代码映射](docs/modules/inventory/code-map.md)。SQLite/页面模拟验证不等于真实InnoDB锁等待或生产验收。

## 2026-10-16 修复及更新注意

- 本次涉及商品图片服务、销售路由/FIFO、models.py、migrate.py、schemas.py、报表路由和两份页面，不能只上传HTML。损益页面新增update_scope=current，保留人工上月/年累计值；默认API仍整体替换，必须前后端一起更新。详细逻辑和验证见[修复记录](docs/development/2026-10-16-project-safety-fixes.md)。
- 启动兼容迁移新增sales.order_claim（可空VARCHAR(64)）和uq_sales_order_claim唯一索引；历史值保持NULL，不改写订单/成本。正式库先备份并确认DDL权限、索引创建的锁等待及回滚安排，统一更新所有销售写入进程；旧代码不会写占位值，不宜混跑。启动仍有其他既存迁移/历史修复副作用，不可当只读验证。
- 仅本地127.0.0.1:3309 / inventory_fifo_test已在明确授权后补订单结构、完成InnoDB验证，临时业务记录已清理且既有数据摘要一致。新增结构保留、自增推进。tests/mysql_sales_integrity.py默认只读检查；--apply-local-order-schema才有写入，不得用于生产。
- 入库其他成本现在自动计入09/10/12；财务应重新导出，并核对历史人工科目是否重复录过同笔费用。历史已用未来入库扣减的销售不会自动重写，需要业务核对后另定处理方案。
- 图片更新保留旧文件；历史HTML/SVG访问返回404，正常新图片重新编码，动图保留首帧。没有清理历史uploads。本次没有重启应用或发布服务器，运行中进程是否已加载本次代码需在获准更新时确认。

## 项目概览

- FastAPI + SQLAlchemy 异步 ORM + MySQL 的 FIFO 库存成本核算系统。
- 入口为 `main.py`；根路径 `/` 返回 `templates/index.html`，接口文档为 `/docs`。
- Compose 服务名 `web`，容器名 `fifo-inventory`，端口映射为宿主机 `8006` 到容器 `8000`。
- 商品图片在项目根目录 `uploads/`，挂载到容器 `/app/uploads`，访问路径 `/uploads/<文件名>`。
- `tests/` 是 `unittest` 自动化测试；`test_main.http` 是手工请求示例。

## 每次运行前检查

1. 阅读本文件和项目根目录的 `.env`，不要把密码复制到日志、截图或文档。
2. 检查 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。当前本地备份测试库位于 Docker `fifo-mysql8-test` 容器（MySQL 8.0.46），宿主机连接为 `127.0.0.1:3309`、用户 `fifo_local_app`、库名 `inventory_fifo_test`；本地 `.env` 使用已有的独立本地凭据。原 `mysql57` 与 `127.0.0.1:3308` 的 `inventory_fifo` 保留。Web 容器不能直接使用此宿主机回环地址，Docker 部署须另配容器可达的数据库地址。
3. 店铺损益表的“房租+水电+网费”还需要 OA PostgreSQL 的只读配置：`OA_DB_HOST`、`OA_DB_PORT`、`OA_DB_DATABASE`、`OA_DB_USER`、`OA_DB_PASSWORD`。FIFO 不写入 OA；只从 `dingtalk_oa.ding_approval_instance` 读取已完成且同意的指定办公场地审批明细。
   如果 OA PostgreSQL 与服务器同机且 OA 项目的 `PGHOST=localhost`，Docker 部署时将 `OA_DB_HOST` 配为 `host.docker.internal`；Compose 已加入 Linux 所需的 host-gateway 映射。若 PostgreSQL 在内网主机，则填写该内网地址。
4. 确认运行服务器能连接 MySQL，且数据库账号有建表和改表权限。应用启动会创建缺失表。
5. 确认 `uploads/` 可写、磁盘空间足够、宿主机端口 `8006` 未被占用。
6. 生产环境必须更换数据库密码、默认账号密码和 JWT 密钥，并使用 HTTPS/反向代理。
7. 启动后检查日志，确认数据库连接、建表和默认用户初始化没有异常。

## 启动与停止

在 `docker-compose.yml` 所在目录执行：

```bash
docker compose up -d --build
docker compose logs -f web
```

访问 `http://服务器地址:8006/`；接口文档为 `http://服务器地址:8006/docs`。

```bash
docker compose stop
docker compose restart
docker compose up -d --build
```

本地开发或排障：准备 `.env` 和可用 MySQL、安装 `requirements.txt` 后运行：

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8010
```

`tests/mysql_fifo_concurrency.py` 的真实 MySQL 并发验证固定只接受 `127.0.0.1:3308` 的 `inventory_fifo`，会创建并清理测试业务记录；当前 `.env` 指向新的备份测试库，直接执行该脚本会被目标校验阻止。需要运行时，先核实并切换到旧的独立测试连接，不能取消保护后对备份副本执行。普通单元测试命令为 `\.venv\Scripts\python.exe -m unittest discover -s tests -v`。本机端口 `8006` 当前已由其他项目占用，因此上面的直接运行示例使用 `8010`。

## 服务器备份的本地恢复

2026-10-12 已将 `D:\inventory_fifo_20261010_115703.sql` 恢复到独立的本地测试库。备份约 3.4 MB，包含完成标记；其服务器版本为 MySQL 8.0.46，表排序规则为 `utf8mb4_0900_ai_ci`。没有修改备份文件内容，也没有连接生产数据库执行恢复。

| 项目 | 本次核实的本地配置 |
| --- | --- |
| Docker 容器 / 镜像 | `fifo-mysql8-test` / `mysql:8.0.46` |
| 连接地址 / 库名 | `127.0.0.1:3309` / `inventory_fifo_test` |
| 本地账号 | `fifo_local_app`，凭据保存在 Git 忽略的 `.env` |
| 数据卷 | `fifo-mysql8-test-data`，挂载 `/var/lib/mysql` |
| 旧测试环境 | `mysql57` / `127.0.0.1:3308` / `inventory_fifo`，保留 |

恢复后逐表核对 17 张表，共 5,631 条记录，与 SQL 备份中的插入数量一致；其中商品 48、入库批次 72、销售 1,396、销售成本明细 1,396、用户 12。17 张表均为 InnoDB；外键关联没有孤立记录，批次没有负库存或剩余数量超过入库数量的记录。当前 ORM 所需字段均存在，应用的 SQLAlchemy/aiomysql 异步连接已成功读取导入数据，旧测试库逐表数量保持不变。核验摘要保存在 Git 忽略的 `output/local-db-restore/verification.json` 与 `application-connection.json`，不包含凭据或业务原始行。

恢复阶段仅导入 SQL 并切换本地 `.env`。随后于 2026-10-12 在 `http://127.0.0.1:8010` 成功启动应用，正常执行 `main.py` 的建表、兼容迁移、用户初始化和月末快照检查；首页及 `/openapi.json` 均返回 HTTP 200。启动后再次读取本地库，商品 48、入库批次 72、销售 1,396、用户 12、存货快照 105、会计期间 3、导入批次 5，数量与恢复时一致。

后续对 `2026-09-30` 存货报表执行只读数据库计算核验、本地 HTTP 查询与 Excel 导出核对：68 行金额一致，相关 34 项回归测试通过，已有快照内容未改变。其中 15 行缺少对应的 8 月末批次快照，上月账面差额保留未知；规则历史及缺失原因仍待业务核实。独立核验范围与限制见[报表流程](docs/modules/reports/workflow.md#自动月末快照与比较值)，完整财务业务验收及生产更新仍待验证。

OA 未配置，店铺损益中需要 OA 自动取数的字段不代表生产完整结果。SQL 只包含商品图片路径；本次检查的 48 个图片引用在本机均缺少对应文件，服务器 `uploads/` 图片文件需要单独备份和复制。

重启本机后如测试库未运行，可以执行 `docker start fifo-mysql8-test`。在现有虚拟环境中，按上方 `8010` 端口命令启动本地项目即可使用导入的数据；使用前仍须核对实际 `.env` 和端口。

Windows PowerShell 终端已位于 `D:\product\FastAPIProject` 时，只需执行以下一条命令。切换目录与启动命令应分别执行，不能拼接成同一条 `Set-Location` 命令：

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8010
```

## 启动逻辑与配置

`main.py` 启动时先执行 `database.init_db()`，用 SQLAlchemy `create_all` 创建缺失表；再执行 `migrate()` 添加缺失的兼容字段；最后执行 `auth.seed_users()`，仅在 `users` 表为空时创建默认用户。这不是完整的版本化迁移。

`database.py` 从项目根目录 `.env` 读取 MySQL 配置，使用 `mysql+aiomysql` 异步连接。当前 `.env` 适合直接在本机运行 FastAPI；Docker 容器内的 `127.0.0.1` 指向容器自身，因此 Docker 或服务器部署必须单独配置数据库地址。浏览器使用可撤销的24小时Cookie会话；local/hybrid兼容无Cookie的HS256 Bearer。Compose 已要求 JWT_SECRET 并传入认证配置，生产显式使用强随机值。EIMS 参数与回调注册见[接入说明](docs/modules/auth/eims-sso-setup.md)。

`services/oa_office_expenses.py` 使用独立的 `OA_DB_*` 配置以只读方式连接 OA PostgreSQL。它只统计流程 `PROC-E7BC3316-E618-4812-BDCC-7A655A7C694B` 中申请日期落在报表期间、状态为已完成且同意的记录。管理支出为“办公场地总费用”时，仅取 `LatínGo拉丁购`（部门 ID `1089990115`）的租金和电费明细，合计后均分给当前有效店铺；管理支出为“工资中国Salario en China”时，按明细中的部门名称与系统店铺名称精确匹配并带入“人员薪资”，该值仍可人工修改并保存，已保存人工值优先；顶层组件“平摊人事+财务管理费用”非零时，按当前有效店铺数均分并带入报表同名行，该字段只允许自动取数，不允许人工修改。OA 配置缺失时，本地开发报表对应自动值为 0；配置存在但读取失败时接口必须报错，不能以 0 掩盖生产连接故障。

## 文件职责

```text
main.py                 应用入口、生命周期、首页、静态 uploads
database.py             .env、异步 MySQL 引擎、会话、建表
auth.py                 Cookie/Bearer 认证、密码、角色与初始用户
models.py               28 张 ORM 表，含批次修改日志；8 张财务增强表默认关闭
schemas.py              Pydantic 请求和响应模型
migrate.py              启动时自动执行的兼容迁移
routers/auth.py         本地登录、建号、启停、角色/店铺和密码管理
routers/eims_auth.py    EIMS 登录、回调、退出和登录配置
services/auth_sessions.py  Cookie/CSRF、独立认证事务、刷新和撤销
routers/products.py     商品和图片上传
routers/batches.py      入库批次与单件成本
routers/sales.py        销售记录和 FIFO 出库
routers/reports.py      月度报表
services/fifo.py        FIFO 扣减和利润计算核心
templates/index.html    单页中文管理界面
uploads/                商品图片的持久化目录
mysql-init/init.sql     root 密码初始化 SQL，web 服务不会自动执行
Dockerfile              Python 3.13-slim 构建和 uvicorn 启动
docker-compose.yml      web 服务、8006:8000、uploads 挂载、MySQL 环境变量
```

## 数据模型与业务规则

数据库 ORM 定义了 28 张表（8 张财务增强表默认关闭）。以下五张是 FIFO 核心表，其余用于店铺、导入、结算、财务报表与认证状态；完整字典见[数据库总览](docs/knowledge/database.md)：

- `users`：用户名唯一、密码哈希、角色 `admin`、`operator`、`viewer`，非管理员可绑定一个店铺。
- `products`：SKU 唯一、名称、图片路径。
- `inventory_batches`：批次、入库和剩余数量、采购价、钉钉单号、总金额、头程/尾程/其他成本及其可选单号、单件成本、发货和入库时间、创建者。总金额由用户填写，后端固定计算采购单价为 `总金额 ÷ 数量`，不接受前端覆盖；旧接口未传总金额时仍兼容采购单价输入。
- `sales`：订单、数量、销售单价、FIFO 总成本、平台费、利润、创建者及导入时固化的店铺。
- `sale_cost_details`：一笔销售对应的批次扣减明细。

单件成本：

```text
purchase_price + (shipping_cost + last_mile_cost + other_cost) / quantity
```

利润：

```text
selling_price * quantity - total_cost - platform_fee
```

FIFO 依据批次 `arrived_at`、批次 ID 升序，从 `remaining_quantity > 0` 的批次扣减。`services/fifo.py` 使用 `SELECT ... FOR UPDATE` 锁定商品的可用批次，在事务提交或回滚前保持锁；等待中的销售随后读取最新剩余数量，再决定是否扣减。销售导入在逐行分配库存前锁定所有涉及商品的可用批次，批次编辑、删除也先锁定目标行并复查是否已被消耗。此规则依赖 MySQL/InnoDB，新增直接修改库存的代码必须沿用事务内加锁和复查。一次销售跨多个采购成本不同的批次时，`total_cost` 为各批次 `unit_cost × 扣减数量` 之和，页面展示的 FIFO 成本单价为 `total_cost ÷ 销售数量`；这里使用包含采购价和物流费用的单件成本，保证与库存计价口径一致。库存不足时，手工销售返回 HTTP 400；订单详情批量导入按 Excel 行顺序逐行判断，每一行及其所有组合商品只有在库存能够完整满足时才整行扣减，否则整行进入待确认且不扣任何库存，剩余库存可继续匹配后续数量更小的行。

订单详情导入会解析 `A*3`、`A+B*3` 等商品名称。跨商品组合按同一导入文件中、同币种且日期最接近的单独销售价格分摊组合销售额和平台费；单独价格优先取 `商品原价小计 ÷ 数量`，缺失时回退到 `净商品销售额 ÷ 数量`，组成数量会计入权重。任一组成商品没有可用的单独售价时，整条结算行进入待确认区，不创建销售或扣减库存。

## API 与角色

业务接口按 Cookie 会话（写请求带 CSRF）或允许模式下的传统 Bearer 鉴权；用户仍由管理员创建，EIMS 不自动建号。模式和16个认证接口见[认证 API](docs/modules/auth/api.md)。

- `admin`：全部功能和用户管理。
- `operator`：共享商品读写，批次编辑可跨创建人并留日志，删除仍受创建人/消耗状态约束，敏感销售和损益按当前店铺限制。
- `viewer`：可写共享商品/平台SKU；批次、销售及损益只读。完整权限和隔离例外以[权限矩阵](docs/knowledge/authentication.md)为准。

主要接口：`/auth/*` 登录和用户管理；`/products` 商品和图片；`/products/options` 商品下拉框轻量数据；`/batches` 入库批次；`/batches/export?batch_ids=1&batch_ids=2` 导出一个或多个批次为带图片 Excel；`/sales` 销售和成本明细；`/reports/monthly?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` 月度报表。

商品、批次和销售列表在传入 `page`（从 1 开始）与 `page_size`（1-100）时返回 `{items,total,page,page_size}`，供页面服务端分页使用；超过末页会自动返回最后一页。不传 `page` 时保留原有数组响应，兼容旧调用。商品/批次未勾选时的批量导出仍导出全部匹配数据。应用启动迁移会补齐批次按到货时间和 ID、批次商品加到货时间、销售时间和销售创建人加时间的索引；首次生产部署前应先备份数据库。

07存货导出按FIFO销售重建报告日及上月末剩余库存，期末准备按累计比例计算一次；账面为现存库存成本减期末准备，本月计提仅为现存库存本月新增准备。上月用相同余额规则重算，不读/补建准备余额快照，不能导出未来日。后台仍按Asia/Shanghai生成上一完整自然月的原准备余额快照，已有快照不覆盖；无人工结账页面或公开会计期间接口。

## 前端行为

前端有商品、入库、图册、销售、报表和管理员用户管理。入库表单在头程运费、尾程派送费和其他成本后各提供一个可选的钉钉单号字段：头程运费钉钉单号、尾程派送费钉钉单号、其他成本钉钉单号；批次列表、“显示字段”面板和 Excel 导出表头同步使用这些名称。批次操作栏可单条导出，列表工具栏可批量导出当前列表，导出 Excel 会嵌入商品图片。图册图片右下角提供下载按钮。批次字段勾选只影响页面列显示，不筛掉批次、修改数据库或影响 FIFO 成本。管理员可在用户管理中建立店铺，并将非管理员账号绑定到一个有效店铺；未绑定店铺的运营账号不能执行销售文件导入。导入时会把用户当时绑定的店铺同时固化到销售和结算记录，之后改绑不会改变历史店铺归属。销售页面按天录入，会自动把 `selling_price` 与 `platform_fee` 传为 `0`，订单号为 `日期-SKU`。CSV 格式为 `SKU,YYYY-MM-DD,数量`，逐行调用销售接口。销售列表按“销售单录入模板”展示店铺、日期、SKU、产品名称、销售数量、人民币销售单价、FIFO 成本单价、人民币销售收入、销售成本、人民币平台费和人民币毛利，并在表尾汇总当前页。非 CNY 导入按订单日期从 Frankfurter/ECB 获取历史汇率，非交易日使用该日之前最近的可用交易日；汇率、实际汇率日期和来源在导入时固化到结算行，页面只显示人民币金额并在销售单价悬浮提示中保留原币与汇率依据。汇率缺失会阻止导入，不能静默按零处理。每次成功上传会创建 `sales_import_batches` 记录；导入人可撤销自己的文件，管理员可撤销全部文件，撤销会按 `sale_cost_details` 恢复原 FIFO 批次库存并删除该批次产生的销售、分摊和结算行，导入批次保留为已撤销状态。新导入记录优先展示导入时固化的店铺，旧记录没有固化店铺时才尝试通过有效店铺 SKU 映射推断，否则标记为未关联店铺。需要准确利润时，必须通过 API 传入真实售价和平台费，不能把当前页面录入的利润当真实利润。

界面样式以桌面端为主：统一使用浅灰画布、白色内容卡片、深色标题栏和红色主操作色；批次列表使用固定最小宽度的横向滚动宽表，避免字段标题被压缩成竖排；商品、销售、报表和用户列表也使用统一的表格容器、表头、边框和按钮样式。该次视觉调整只修改前端 HTML/CSS，不改变 API、数据库字段、导出逻辑或权限行为。`templates/index.html` 是运行时首页，根目录 `index.html` 与其保持同步。

批次与商品列表默认每页显示 20 条，可切换为每页 10、20、50 或 100 条并通过页码翻页。复选框选择会跨页保留，表头全选仅选择当前页；选中一条或多条后点击“批量导出”只导出所选记录，没有任何勾选时导出当前完整列表。批次列表提供“搜索商品”输入框，可按商品名称、SKU 或批次号模糊搜索；搜索结果同样可分页、勾选和导出。批次宽表使用固定高度的独立滚动区域：仅保留表格底部原生横向滚动条，数据较多时横向滚动条固定在该区域底部、表头固定在区域顶部，无需滚到整个列表底部即可左右查看字段。编辑商品和编辑批次均在页面中央固定窗口中打开，不再调用 `scrollIntoView` 改变页面滚动位置。

商品列表同样提供左侧复选框、表头全选、单条导出和批量导出；勾选商品后批量导出只导出所选商品，未勾选时导出当前商品列表全部商品。`GET /products/export?product_ids=1&product_ids=2` 返回带商品图片的 Excel，权限与商品查询一致。

### 商品批量新增

2026-10-12 新增商品列表“批量导入”入口。下载模板，填写 SKU、商品名称、商品类型、安全库存四列，选择 .xlsx 后校验预览，核对新增/跳过/错误行，确认导入并下载结果。有错误须修正后重新预览；已有 SKU 保留原资料。每次最多 2,000 条、5 MB，SKU 按文本填写以保留前导零；新品安全库存填 0，预览有效 15 分钟。导入不包含图片、批次库存、成本和历史日期。

三种现有角色均可使用，商品与当日规则统一提交，失败整批回滚。无需新增依赖或数据库结构迁移；更新 Python 文件后需要重启应用，页面需刷新。具体契约与维护入口见[商品导入流程](docs/modules/products/workflow.md#商品-excel-批量新增)、[商品 API](docs/modules/products/api.md#批量新增契约)。

真实 MySQL 手工验证脚本为 `python -m tests.mysql_product_import`，运行前显式设置专用 PRODUCT_IMPORT_TEST_* 变量并核实本机目标，不能复用 `inventory_fifo_test` 或生产数据库。脚本保留专用库测试记录，常规 unittest 不会执行它。

### 存货成本追溯与 SKU 汇总

以下为2026-10-12调整前的功能/运行验证历史记录，不代表2026-10-15新口径验收。最新实现见下方持有期间计提及销售转销一节。

2026-10-12 报表增加原始入库数量/成本、累计销售出库数量/成本四列；现存库存成本、累计准备和账面价值明确标注现存口径。选择“批次明细”或“按SKU汇总”后可导出当前视图；财务附件保留原有工作表顺序，末尾追加 `07-存货SKU汇总表`（管理员共5张，非管理员4张）。SKU 汇总纳入截止日前全部批次，包括已售完旧批；各批沿用自己的成本与规则，累计入库/出库合计可能不同于批次明细。

接口新增 `GET /reports/inventory-impairment/summary`，独立导出增加可选 `view=batch/sku`，默认兼容原批次导出。无需增加依赖或迁移数据库；更新 `routers/reports.py` 和两份首页后重启应用并刷新页面。批次/FIFO/跌价算法和账面价值公式保留，历史快照未知值不补零；新增查询同样可能补建快照，不能默认视为只读。完整说明见[存货字段说明](docs/modules/reports/inventory-impairment-fields.md)。

如果新增数量列显示 `undefined`、对应金额显示 `-`，先排查页面与后端版本是否一致，不要直接补录历史数据。首页每次请求读取模板文件，而后端 Python 路由在进程启动时加载，因此只刷新页面可能出现新页面配旧接口。可只读检查 `/openapi.json` 是否包含 `/reports/inventory-impairment/summary` 路径，以及独立导出的 `view` 参数；缺少这些定义时应更新并重启后端，再刷新报表。四个新字段由已有批次入库量、批次单价和销售成本明细动态计算，不需要新增数据库列或回填报表金额。若数据库不可用，应先恢复对应环境的数据库；重启整个 Docker Desktop 会影响其他本地容器，须确认范围。

功能实现阶段59项受影响模块测试、两份页面脚本语法及10项DOM模拟交互检查通过。对独立本地备份库做只读核验，替换快照生成并禁止写 SQL/commit：68行明细、72个全部批次、48行SKU汇总，976个Excel数值与计算一致，成本勾稽差异0，快照内容校验值未变。核验摘要和Excel位于Git忽略的 `output/inventory-cost-summary/`。

2026-10-12 完成本地运行排查：旧进程未加载新增接口定义；经授权重启本机 Docker Desktop，恢复测试库后启动更新后的 8010 应用。实际鉴权 HTTP 验证中，9月30日为68行明细/48行SKU汇总，10月12日为64行明细/48行SKU汇总，四个新增字段全部有值；912个接口数值及448个实际导出Excel数值与数据库来源一致。应用启动与查询前后，入库批次、销售、销售成本明细、会计期间和库存快照共五张表的数量及内容哈希一致，没有回填历史金额。证据为 `output/inventory-cost-summary/live-backend-verification.json`，两份当前视图导出为 `live_20261012_batch.xlsx`、`live_20261012_sku.xlsx`。浏览器自动检查因无法可靠识别当前URL被工具中止，真实页面视觉验收仍待验证；本次未操作生产或部署服务器。

## 持有期间计提及销售转销（2026-10-15现行）

07新增期初准备、本月总计提、本月销售转销；每天按实际在库层增加准备，销售当天先计提再出库。新增服务 `services/inventory_provision_movements.py`，同步 `routers/reports.py`、`schemas.py`、`templates/index.html` 和根目录副本；不能只上传页面。无需新依赖或数据库迁移/金额回填，Python更新后重启已核实的目标应用并重新导出Excel。旧快照保持原样，财务数据会按现有流水重算。

历史时序异常金额及不完整合计留空；跨店/缺失归属的损益准备和转销待核对，不猜测承担店铺。查看状态、比较说明、损益warnings及Excel备注，不能用0填补。最新验证及边界见[实施记录](docs/development/2026-10-15-holding-period-impairment.md)。本地8010已加载此版本，服务器未发布。

## 历史：静态期末准备余额（2026-10-15早期版本）

账面=报告日剩余数量×批次单价−期末累计准备；新品100件×10元，售40件、库龄40天时，现存成本600、累计准备120、账面480。本月列只统计期末仍在库商品本月新增准备；上月账面按上月末真实余量重算，差额含销售/入库影响。原始入库/销售成本追溯列保留，售完期末准备/账面为0。

同步更新 `services/inventory_balance_impairment.py`、`routers/reports.py`、`templates/index.html` 和根目录副本，保留共享 `services/inventory_impairment.py` 规则。无需新依赖、数据库迁移或金额回填；更新Python代码后需重启已核实的目标应用，刷新报表并重新导出Excel。07查询/导出不补建快照；启动仍有原有写入，重启前必须核实本地/获准目标。当前未发布服务器。字段和验证见[字段说明](docs/modules/reports/inventory-impairment-fields.md)及[开发决策](docs/development/2026-10-15-inventory-balance-impairment.md)。

本次170项单元测试及两份页面70项模拟检查通过；已核实本地MySQL备份两期各72批次/48个SKU，独立复算、实际鉴权HTTP及Excel结果一致，8010已重启加载余额版本。七张核心事实/规则/快照表在核验及重启前后哈希一致。此结果不代表生产或真实SSO/密码登录已验收，详见上述开发决策。

## 历史：财务自定义逐日累计（2026-10-12，已被2026-10-15替代）

以下部署文件及验收记录仅对应当时版本，不能作为当前操作清单或当前金额依据。

07明细/汇总/独立导出与财务附件同步改为逐日金额累计：每日销售后库存×当天累计比例×批次单价；原始入库成本减累计跌价，可为负。单日比例最多90%，合计金额不封顶；本月只加总当月各天金额，已售完批次保留售前累计。两种视图包含全部已到货批次。

无需安装依赖、数据库迁移或回填报表金额。发布文件包含 `services/inventory_daily_impairment.py`、`routers/reports.py`、`templates/index.html` 和根目录 `index.html`，连同相关测试/文档同步；不能只更新页面。更新后重启应用并刷新报表，接口字段名不变但金额语义已改变，详细见[字段说明](docs/modules/reports/inventory-impairment-fields.md)。

上月比较从相同保存流水重算，不依赖旧准备余额快照。应用启动仍有迁移/快照检查，执行前核实本地或获准目标。自动快照和店铺损益继续用原余额口径。旧导出文件不会自动重算，财务应重新导出所需日期。只导入盘点库存且缺少先前业务流水时不能自动补齐真实历史。

新口径已通过58项相关回归、两份页面脚本语法和22项DOM模拟检查；本地备份的两个截止日各72个批次/48行SKU，直接SQL复算与实际鉴权HTTP及Excel一致。8010已重启加载新版本，七张核心事实/快照表在重启和核验前后数量/内容哈希相同。证据位于Git忽略的 `output/daily-finance/`，详细验证边界见[开发决策](docs/development/2026-10-12-daily-accumulated-impairment.md#本次实际验证)。这些结果不代表生产已更新或历史事实完整。

## 默认账号与敏感信息

仅 users 为空时初始化 admin/operator/viewer 三种角色；首次部署立即设置安全密码，文档和初始化日志不记录密码。源码的初始账号、数据库及 JWT 默认配置不能用于长期生产；部署密钥仅在受控后端配置保存，禁止复制到聊天/Git。EIMS 客户端和 Token 加密密钥与数据库/JWT 密钥分别维护。

## 已知问题与维护风险

1. Compose 已要求 JWT_SECRET；源码仍有默认配置和初始账号，生产需显式设置并改密。SSO 真实客户端/日志/撤权行为待联调。
2. `viewer` 当前可修改商品和平台 SKU 映射；若该角色应只读，需调整服务端权限。
3. 月度报表会过滤 operator 的销售，但库存汇总未按用户过滤，可能泄露全局库存。
4. 删除商品或用户没有显式级联策略；有批次或销售关联时，删除前必须备份并确认外键影响。
5. FIFO 扣库、导入预检和批次编辑/删除已有行锁；并发库存写入必须沿用该事务约束，且大批量导入可能增加锁等待时间。
6. 批次号按到货日前缀的最大有效序号加一，唯一约束冲突后最多尝试三次，仍冲突返回409；目标环境并发入库待验证，见[库存流程](docs/modules/inventory/workflow.md#新建批次)。
7. `test_main.http` 使用过时字段 `duty_cost` 且缺少认证头；当前字段为 `last_mile_cost`。
8. `migrate.py` 在应用启动时运行，尚非完整的版本化迁移；部署含结构变更的版本前仍须备份数据库。新增批次留档字段（含三个成本单号）的历史独立脚本为 `migrations/20260820_add_batch_dingtalk_fields.py`，执行前应核对目标库与现有列。
9. 商品图片现有限制大小/像素、实际解码和重新编码，静态/uploads有扩展名及响应头保护；销售文件等其他入口和反向代理上传额度仍需单独核查，见[商品流程](docs/modules/products/workflow.md#创建与图片)。

## 备份与排障

先备份，再迁移、删除或批量导入：

```bash
mysqldump -h $MYSQL_HOST -P $MYSQL_PORT -u $MYSQL_USER -p $MYSQL_DATABASE > inventory_fifo_YYYYMMDD.sql
docker compose ps
docker compose logs --tail=200 web
```

数据库核对：

```sql
SHOW TABLES;
SHOW CREATE TABLE inventory_batches;
SELECT COUNT(*) FROM products;
SELECT COUNT(*) FROM inventory_batches;
SELECT COUNT(*) FROM sales;
```

仅当目标库仍缺少钉钉单号、总金额或成本单号字段，且未通过应用启动迁移补齐时，先备份目标库，再执行历史脚本：

```bash
python migrations/20260820_add_batch_dingtalk_fields.py
```

将黄丽瑶名下入库批次的操作归属转给 `Navi` 前，先备份目标库；然后在已更新的 `web` 容器中执行一次：

```bash
docker compose exec web python migrations/20260909_transfer_huang_liyao_batches_to_navi.py
```

该历史脚本仅转移创建人，不改数量/成本；当前跨创建人编辑已无需执行此脚本。已出库批次编辑依照上述成本重述规则，删除仍受限制。不得为打开编辑入口而迁移历史归属。

容器无法启动时，依次检查 `.env` 是否完整、MySQL 是否可达、用户是否允许该来源和具备权限、端口 `8006` 是否被占用。不要为了重置问题直接删除容器、数据卷或数据库。

## EIMS 统一登录运行配置

缺省 hybrid；客户端未配置时本地登录仍可用，SSO 按钮不可用。生产域名为 https://fifo.yueweiportal.com，issuer 为 https://eims.yueweiportal.com。配置/注册/账号绑定/验收统一见[接入说明](docs/modules/auth/eims-sso-setup.md)。不要在本地 .env 使用生产公开来源；8010 开发使用空来源或 loopback。

新增 users.is_active 和两张认证表会随启动创建/兼容迁移，旧用户默认启用；启动仍有原修复/快照副作用，发布前先备份。每小时清过期认证记录，提供方网络请求按需发生，不在启动时认证真实用户。环境或加密密钥变更后重启应用；密钥更换需重新登录。改密/停用撤销 Cookie，传统 Bearer 吊销限制见[认证概览](docs/modules/auth/overview.md)。

2026-10-13 本地验证：150项 unittest、两份页面48项认证检查通过；真实本地InnoDB验证单次消费/并发一次刷新/撤销，校验既有业务记录一致。8010已更新，Cookie me、商品/72条存货、CSRF和退出通过；旧登录缓存已清理，浏览器需重新登录一次。未安装依赖、修改生产配置/数据库或发布；真实 EIMS 授权、绑定及退出尚未验收。

## GitHub 代码发布与服务器更新

当前 GitHub 公有仓库为 `https://github.com/yueweiit/FastAPIProject`，默认分支为 `main`。服务器通过 `git pull --ff-only` 获取新版本，公有仓库无需在服务器配置 GitHub Token 或 Deploy Key。`.gitignore` 排除了 `.env`、`uploads/`、`output/`、`.deploy/`、虚拟环境和压缩包；仓库只保存代码、Docker 配置和文档。首次发布可参考 `DEPLOYMENT_HANDOFF.md` 第 13 节。服务器更新前必须先确认 `.env` 和 `uploads/` 仍在项目目录，并备份数据库；更新后执行 `docker compose build web` 和 `docker compose up -d --force-recreate web`，再检查容器、日志和 `curl` 页面状态。禁止将个人 Token、数据库密码或服务器密码提交到 GitHub。

## 审阅来源

本文件基于 `FastAPIProject_mefrn.tar.gz` 于 2026-08-19 解压后审阅的源码、部署文件、配置、前端和手工示例整理。`.venv/`、`.idea/`、`__pycache__/` 为第三方或本地生成内容，未作为业务逻辑审阅。

## 变更记录

- 2026-10-14：修复认证的改密/会话竞态、轮换Token后临时故障恢复、延迟退出回调；未新增表/字段，未改变库存/财务口径。160项单元测试、两份页面48项检查及真实本地InnoDB双向用户锁/刷新恢复通过，8010已加载修复版并通过商品、存货、Cookie、CSRF和退出检查。正式EIMS仍待配置和联调；详细逻辑及边界维护于[认证流程](docs/modules/auth/workflow.md)和[方案修复记录](docs/plans/2026-10-13-eims-sso-integration.md#9-2026-10-14-审查问题修复)，未生产部署或提交推送。
- 2026-10-08：FIFO 销售改为锁定可用库存批次后复查数量；销售文件导入在库存预检前锁定相关批次；批次编辑、删除及导入撤销读取批次时使用行锁。无需数据库结构迁移。
- 2026-10-08：在本机 Docker `mysql57` 建立 `inventory_fifo` 和独立测试账号，项目本地 `.env` 指向 `127.0.0.1:3308`。已初始化 17 张表；真实 MySQL 并发验证确认两笔销售争用最后一件库存时仅一笔成功，测试业务记录已清理。
- 2026-10-12：从下载的 MySQL 8.0.46 服务器备份恢复独立本地测试库 `inventory_fifo_test`（`127.0.0.1:3309`），逐表核对 17 张表、5,631 条记录及外键、库存边界，项目 `.env` 已切换，旧测试库保留；随后在本机 `8010` 端口成功启动应用，首页和接口定义检查通过。
- 2026-10-12：增加商品 Excel 批量新增的模板、预览、确认及结果下载，已有 SKU 跳过，商品与规则统一提交；96 项单元测试通过，独立本地 MySQL 8.0.46 验证 2,000 条导入、重复/并发冲突和整批回滚。原业务测试库在验证期间数量和校验值未变化；服务器尚未更新，浏览器视觉验收因自动化工具启动失败待验证。

## 财务增强结构与启用

2026-10-19新增[财务增强模块](docs/modules/financial-control/overview.md)。FIFO_FINANCIAL_CONTROLS_ENABLED默认0，Compose已传递；尚未对目标库执行加表、启用或部署。

1. 先审核基础业务/认证/批次日志表及8张财务增强表和外键/唯一约束/DECIMAL精度，备份并确认InnoDB、utf8mb4及会话REPEATABLE READ。保存版本会拒绝MySQL非REPEATABLE READ；不修改服务器隔离配置。
2. 离线生成DDL（不连接数据库）：

   ```powershell
   .\.venv\Scripts\python.exe migrations/add_financial_controls.py --write-sql output/financial-controls/schema.mysql.sql
   ```

3. 隔离库默认只读核验：` .\.venv\Scripts\python.exe migrations/add_financial_controls.py `。脚本仅接受已核实127.0.0.1:3309 / inventory_fifo_test / fifo_local_app；凭证在本地环境，不输出连接串。只有该库的结构修改已获授权，才增加`--apply-local-finance-schema`创建8表；不创建数据库，不运行migrate/种子/历史修复，不写业务记录。脚本核验字段清单/可空性/外键/唯一键/引擎/隔离级别；精度、字符集、权限及执行计划仍需审核DDL和实际结构。
4. 正式环境由有权限维护者另行审核和执行加法DDL，脚本的隔离目标保护不可移除；本次没有生产授权。MySQL DDL不能依赖事务回滚。已有同名表不自动改造，结构差异需逐项处理。
5. 验证完成后持久配置FIFO_FINANCIAL_CONTROLS_ENABLED=1，按既有发布流程加载新代码。应用启动会运行既有迁移、初始化、快照等写入副作用；不能用启动代替预检。确认实际静态脚本、22个新API、角色/店铺、实际账单和旧版导出。
6. 补录9月成本后到9月工作台比较旧版，复核并保存新版；旧版不覆盖。已有财务记录时不能通过关闭开关回退版本：init_db会拒绝启动，避免成本/费用静默恢复旧值。不要删除表/凭证或批量回填原FIFO成本，回滚需要保持可读取调整层的兼容代码及备份方案。

本次仅离线DDL与隔离SQLite/VM验证；真实MySQL锁等待、并发成本/FIFO、旧库结构及浏览器/OA/平台凭证待环境验收。测试命令见[约定](docs/knowledge/conventions.md)。

2026-10-19后续回归：在本机3309的独立副本`fifo_browser_20261019`及8011临时服务中定位并修复财务保存500。公共audit现显式异步加载MySQL默认时间字段，包括更新时的updated_at；无需DDL，更新相关Python文件后重启获准目标服务。原8010服务及inventory_fifo_test结构未修改；生产未迁移、启用或部署。专用MySQL脚本运行限制、事务回滚和命令见[财务增强回归](docs/knowledge/conventions.md#财务增强回归)，不得用它连接业务库。
