# FastAPIProject 项目架构与改造基线

> 本文档是本项目后续开发、排障和部署前的必读说明。任何代码、数据库结构、配置或部署方式发生变化后，必须同步更新本文档和 `PROJECT_RUNBOOK.md`。

## 2026-10-16 安全及财务一致性更新

本次增加商品图片有界解码/重新编码及历史活动文件隔离；销售新增可空order_claim及唯一索引，只由新订单首条明细占位，仍为19张ORM表。FIFO和导入预分配排除销售日之后到货的批次，保持原锁序和事务；损益页面隔离过期响应、保存绑定已加载条件，以新增current保存范围保留人工比较值（默认API仍完整替换）；09/10/12成本纳入批次other_cost。源代码和模块入口见[修复记录](docs/development/2026-10-16-project-safety-fixes.md)。

本地测试库已获准补结构并通过真实InnoDB验证，既有业务数据核对未变；服务器未迁移或部署。正式更新需要先备份、核验建列/唯一索引权限及锁表影响，统一更新全部写入进程。该可空字段不回填历史订单。图片更新保留旧文件，本次未清理历史上传目录。

## 1. 项目定位

`FastAPIProject` 是一个基于 FastAPI 的跨境电商库存与 FIFO 成本核算系统，当前主要负责：

- 商品和 SKU 管理；
- 商品图片上传与访问；
- 入库批次管理；
- 按批次计算单件成本；
- 按 FIFO（先进先出）规则处理销售出库；
- 记录销售成本明细和利润；
- 提供月度销售与库存报表；
- 提供本地/EIMS登录、可撤销会话、CSRF、角色/店铺权限、账号启停与密码管理。

本项目与钉钉审批、`dingtalk-oa`、`dingtalk-expense-sync`、`dingtalk-budget` 没有代码级依赖。当前数据库是本项目独立使用的 MySQL 库 `inventory_fifo`。

## 2. 本地测试拓扑

```text
本机直接运行的 FastAPI / Uvicorn
  -> 127.0.0.1:3309
     -> Docker 容器 fifo-mysql8-test:3306（MySQL 8.0.46）
        -> inventory_fifo_test 本地备份测试库
```

项目 Docker Compose 的 Web 服务配置为：

```text
宿主机 8006 -> 容器 8000
```

历史服务器部署信息：

```text
容器名：fifo-inventory
容器地址：172.26.0.2（Docker 内网地址）
服务器历史数据库地址：172.19.49.226:3306
数据库名：inventory_fifo
```

服务器历史配置中的 `172.19.49.226` 是独立的远端 MySQL 地址。2026-10-12 已将下载的服务器 SQL 备份恢复到本机 `fifo-mysql8-test` 容器，使用独立的本地账号；原 `mysql57` 容器及 `127.0.0.1:3308` 的旧测试库保留。当前 `.env` 的 `127.0.0.1:3309` 适用于宿主机直接运行 Python；在 Web 容器内，`127.0.0.1` 指向 Web 容器自身，不能直接复用此地址。恢复与验证记录见 [PROJECT_RUNBOOK.md](PROJECT_RUNBOOK.md#服务器备份的本地恢复)。

## 3. 数据和存储边界

### 3.1 MySQL 数据库

数据库连接由根目录 `.env` 提供：

```text
MYSQL_HOST（MySQL 主机）
MYSQL_PORT（MySQL 端口）
MYSQL_DATABASE（数据库名）
MYSQL_USER（数据库用户）
MYSQL_PASSWORD（数据库密码）
```

当前本地测试连接为：

```text
主机：127.0.0.1
端口：3309（映射到 fifo-mysql8-test 容器的 3306）
用户：fifo_local_app
数据库：inventory_fifo_test（2026-10-12 从服务器备份恢复）
```

该数据库名是应用配置中的目标库。本机 `inventory_fifo` 仅供测试，不能与服务器数据库混用。本地 `.env` 保存随机生成的测试账号密码和 JWT 密钥，并被 Git 忽略。应用启动依次执行 `create_all` 和 `migrate()`；较复杂的结构变更仍需专门迁移。

注意：当前 `.env` 适合直接在本机运行 FastAPI。若改用 Docker 运行 Web 服务，须配置该 Web 容器实际可访问的 MySQL 地址和端口；本机 `127.0.0.1:3309` 不能直接作为容器内的数据库地址。

数据库真实数据不等同于 Docker 存储卷。必须通过 MySQL 工具或 DataGrip 进行数据库备份，例如 `mysqldump`。

### 3.2 上传文件

Compose 只挂载：

```text
./uploads:/app/uploads
```

商品图片保存在项目根目录 `uploads/`，数据库只保存类似 `/uploads/<文件名>` 的访问路径。

删除图片文件或替换图片时，必须同时核对数据库中的图片路径，避免产生失效链接。

### 3.3 Docker 存储卷

Docker 存储卷可能保存其他项目的 PostgreSQL、Kafka、Redis 或应用数据，不能因为看到存储卷名称就删除。删除前必须确认：

1. 所属容器；
2. 是否仍被使用；
3. 是否已完成数据库和文件备份；
4. 是否会影响其他项目。

## 4. 启动和初始化流程

应用入口是 `main.py`。FastAPI 启动生命周期按以下顺序执行：

1. `database.init_db()` 调用 SQLAlchemy `create_all`，只创建不存在的表；
2. `migrate()` 检查并添加缺失的兼容字段；
3. `auth.seed_users()` 检查 `users` 表；
4. 只有用户表为空时，才创建默认用户；
5. 检查上一自然月快照，启动每小时快照和过期认证记录清理任务；
6. 提供HTTP接口，停止时取消后台任务、关闭EIMS HTTP客户端。EIMS请求在登录/复核时按需发起。

当前不是完整的版本化数据库迁移系统。`create_all` 不会安全地自动补齐已有表的字段变更；启动时会自动执行 `migrate.py` 中的兼容迁移。`migrations/20260820_add_batch_dingtalk_fields.py` 是批次钉钉单号、总金额和三个成本单号字段的历史独立脚本，仅在核对目标库确有需要时执行。

## 5. 目录与模块职责

```text
main.py                 FastAPI 应用入口、生命周期、首页和 uploads 静态目录
database.py             .env 加载、异步 MySQL 引擎、数据库会话、建表
models.py               SQLAlchemy ORM（对象关系映射）模型
schemas.py              Pydantic 请求和响应模型
auth.py                 Cookie/Bearer 认证、密码和角色依赖
migrate.py              启动时执行的兼容迁移脚本
routers/auth.py         本地登录、用户启停、角色/店铺和密码管理
routers/eims_auth.py    EIMS 登录、回调与退出
services/auth_sessions.py  独立认证事务、Cookie/CSRF、刷新和撤销
services/eims_sso.py    PKCE、RS256、Discovery/JWKS与提供方协议
routers/products.py     商品增删改查和图片上传
routers/batches.py      入库批次增删改查
routers/sales.py        销售记录、出库和 FIFO 成本明细
routers/reports.py      月度销售和库存报表
services/fifo.py        FIFO 扣减、成本和利润计算核心
templates/index.html    单页中文管理界面
uploads/                商品图片文件
mysql-init/init.sql     MySQL root（根用户）初始化 SQL，不由 Web 服务自动执行
Dockerfile              Python 3.13 镜像和 Uvicorn 启动方式
docker-compose.yml      Web 容器、端口映射、uploads 挂载和数据库环境变量
tests/                  unittest 自动化测试，含 FIFO 并发回归测试
test_main.http          手工请求示例
```

批次列表的“显示字段”是纯前端列可见性控制，默认显示全部字段；它不调用后端接口，也不改变批次查询、数据库或 FIFO 计算。

前端视觉基线：页面面向桌面端使用，采用统一的深色顶部栏、浅色导航和白色卡片布局；批次宽表设置最小列宽和固定高度的独立滚动区域，保留底部原生横向滚动条。数据较多时表头固定在区域顶部、横向滚动条固定在区域底部，避免用户滚到整个列表底部才可横向查看长字段和成本单号。商品和批次列表均使用前端分页（默认每页 20 条，可选 10、50、100 条）；已勾选的导出记录跨页保留，表头全选仅影响当前页。此次界面美化仅涉及 `templates/index.html` 和同步的根目录 `index.html`，不涉及后端接口、数据库结构和业务计算。

批次导出交互：批次表最左侧提供逐行复选框和表头全选框；`selectedBatchIds` 仅保存当前页面的选择状态，批量导出优先导出已选 ID，选择为空时导出当前列表全部 ID。编辑窗口通过固定定位居中显示，避免编辑操作导致页面跳转。

商品导出交互：商品表最左侧提供逐行复选框和表头全选框，工具栏提供批量导出，操作列提供单条导出；`selectedProductIds` 仅保存当前页面选择，`GET /products/export` 按传入商品 ID 导出 SKU、名称、库存、库存价值、最近入库、创建时间和嵌入图片。

## 6. 数据库表和业务关系

批次修改权限与日志：运营/管理员可编辑任意历史批次，保留已售数；已售或已补差批次的数量/金额更正须启用财务增强，以cost_basis冻结原依据、追加差额追溯原销售月，不回写FIFO单价或旧月度版本。编辑与日志/凭证同事务，删除仍限未消耗且无补差；日志不级联删除。规则见[库存模块](docs/modules/inventory/overview.md)，结构准备见[运行说明](PROJECT_RUNBOOK.md#已出库批次编辑与成本重述)。

当前 ORM 定义28张表（8张财务增强表默认关闭）；以下为FIFO核心的五张，其余为店铺、平台SKU、导入、结算、财务及两张认证状态表，完整字典见[数据库总览](docs/knowledge/database.md)：

```text
users
  -> inventory_batches.user_id
  -> sales.user_id

products
  -> inventory_batches.product_id
  -> sales.product_id

inventory_batches
  -> sale_cost_details.batch_id

sales
  -> sale_cost_details.sale_id
```

### 6.1 `users`（用户表）

- `username`（用户名）唯一；
- `password_hash`（密码哈希）不保存明文密码；
- `is_active` 控制所有认证方式，旧用户迁移默认启用；EIMS app_user_id 精确匹配本地 id；
- `role`（角色）包括 `admin`（管理员）、`operator`（运营）、`viewer`（查看者）。

### 6.2 `products`（商品表）

- `sku`（库存单位编码）唯一；
- `name`（商品名称）；
- `image`（图片访问路径）。

### 6.3 `inventory_batches`（入库批次表）

保存每批入库数量、剩余数量、采购价、钉钉单号、总金额、头程运费及单号、尾程费用及单号、其他费用及单号、单件成本、发货时间、入库时间和创建者。成本单号可为空，只做留档和导出展示，不参与成本计算。总金额由用户填写，采购单价固定为总金额除以数量；两者均参与单件成本计算。

单件成本公式：

```text
unit_cost（单件成本）
= purchase_price（采购单价）
  + (shipping_cost（头程费用）
     + last_mile_cost（尾程费用）
     + other_cost（其他费用）) / quantity（入库数量）
```

采购单价由后端按 `total_amount ÷ quantity` 自动计算，总金额或数量变化时重新计算，不接受客户端传入的采购单价覆盖。金额字段保存四位小数，无法整除时采购单价和单件成本按四位小数保存，总金额保持用户填写值。为兼容旧接口，未传总金额的请求仍可使用采购单价，并由系统换算总金额。

### 6.4 `sales`（销售表）

保存订单号、销售数量、销售单价、FIFO 总成本、平台费用、利润、销售时间和创建者。

利润公式：

```text
profit（利润）
= selling_price（销售单价） * quantity（数量）
  - total_cost（FIFO 总成本）
  - platform_fee（平台费用）
```

### 6.5 `sale_cost_details`（销售成本明细表）

记录一笔销售从哪些入库批次扣减了多少数量，以及对应批次单件成本。这张表是审计 FIFO 成本来源的关键，不能随意删除或重算覆盖。

## 7. FIFO（先进先出）规则

销售创建时，`services/fifo.py` 执行：

1. 对同一商品且 `remaining_quantity > 0`（剩余数量大于 0）的批次执行 MySQL `SELECT ... FOR UPDATE`，等待正在修改这些批次的事务结束后读取最新数量；
2. 按 `product_id`、`arrived_at`（入库时间）、批次 ID 升序排列；
3. 计算总可用库存；
4. 库存不足时返回 HTTP 400，不创建销售记录；
5. 从最早批次开始扣减，必要时跨多个批次；
6. 创建 `sales`（销售记录）；
7. 创建 `sale_cost_details`（成本明细）；
8. 在同一个数据库事务中提交库存扣减、销售和成本明细；提交或回滚前不释放批次行锁。

销售文件导入会在分配各行可用库存前，按相同顺序锁定涉及商品的库存批次；因此“库存不足则待确认”的预检和后续 FIFO 扣减基于同一份受锁保护的库存。批次编辑、删除也先锁定目标批次，再判断是否已经被销售消耗。并发销售争用最后一批库存时，后到的请求会等待前一个事务结束，并按最新库存决定成功或返回库存不足。此逻辑依赖 MySQL/InnoDB 的事务和行锁；新增库存写入路径也必须遵守相同约束。

重要约束：

- `remaining_quantity`（剩余库存）和成本明细必须保持一致；
- 不能直接修改已经被销售消耗的批次；
- 不能删除已有销售成本明细的批次；
- 修改历史批次成本会影响后续库存价值和利润报表。

## 8. 接口和权限

业务接口使用可撤销 Cookie 与写请求 CSRF；local/hybrid 下无 Cookie 的传统 Bearer 保持兼容，eims模式服务端禁用本地认证。SSO用独立MySQL事务，不延长到FIFO锁。

### 8.1 认证接口

完整16个端点、模式和CSRF要求见[认证API](docs/modules/auth/api.md)。账号/角色/店铺权限仍从本地读取；EIMS注册和实际联调见[接入说明](docs/modules/auth/eims-sso-setup.md)。以下为本地管理入口节选：

```text
POST /auth/login              登录
POST /auth/users              管理员创建用户
GET  /auth/me                 当前用户
GET  /auth/users              管理员查看用户
PUT  /auth/users/{id}/role    管理员修改角色
PUT  /auth/users/{id}/password 管理员重置密码
PUT  /auth/me/password        修改自己的密码
DELETE /auth/users/{id}       管理员删除用户
```

### 8.2 商品接口

```text
POST   /products              新建商品和图片
GET    /products              商品列表及库存汇总
GET    /products/import-template 商品导入模板
POST   /products/import/preview 商品 Excel 只读校验预览
POST   /products/import       根据有效预览整批新增商品与当日规则
POST   /products/import/result 校验/导入清单 Excel
PUT    /products/{id}         修改商品和图片
DELETE /products/{id}         删除商品
```

商品批量新增复用 openpyxl，不新增表或依赖，已有 SKU 跳过且不更新。模板、预览和事务细节以[商品模块流程](docs/modules/products/workflow.md#商品-excel-批量新增)及[商品 API](docs/modules/products/api.md)为准；原有商品/批次列表契约和 FIFO 不改变。

### 8.3 入库接口

```text
POST   /batches               新建入库批次
GET    /batches               查询批次
PUT    /batches/{id}          修改历史批次，已售成本通过差额追溯
DELETE /batches/{id}          删除未被消耗的批次
```

批次列表/导出对全部已登录角色开放；viewer只读。operator/admin可跨创建人编辑，已售数/日期/版本由行锁内复查，数量和金额按成本重述规则执行，单号等资料不重置库存。operator删除仍限自己创建且未消耗、无补差批次，admin删除也受状态限制。日志仅admin/operator共享只读。

### 8.4 销售接口

```text
POST /sales                   FIFO 出库并生成销售成本明细
GET  /sales                   查询销售
GET  /sales/{id}              查询单笔销售和成本来源
```

### 8.5 报表接口

```text
GET /reports/monthly?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
```

返回按月份和商品汇总的销售数量，并附当前库存数量和库存价值。

## 9. 当前已知风险

以下风险在后续改造前必须明确处理，不得忽略：

1. **数据库迁移风险**：`create_all` 不是完整迁移方案，字段变更必须有可回滚迁移脚本。
2. **FIFO 并发约束**：销售、销售文件导入和批次编辑/删除已使用批次行锁；以后新增直接修改 `remaining_quantity` 的路径必须在同一事务内加锁并复查数量。
3. **批次编号并发边界**：按到货日前缀的最大有效序号加一，唯一约束冲突后最多尝试三次，仍冲突返回409；不是全局编号锁。依据：[batches.py](routers/batches.py) 的 _next_batch_no、create_batch；目标环境并发入库仍待验证。
4. **删除风险**：商品、用户、批次删除没有完整的业务级级联策略，删除前必须核对外键和历史记录。
5. **权限边界风险**：当前 `viewer` 可以修改商品和平台 SKU 映射，与旧版“只读”描述不一致，需要按实际业务要求复核。
6. **认证配置风险**：源码仍有安全默认配置；[Compose](docker-compose.yml) 已显式要求 JWT_SECRET 并注入 SSO 变量，实际生产配置仍需核实。传统 Bearer 的吊销限制见[认证边界](docs/knowledge/authentication.md)。
7. **默认账号风险**：默认账号和密码只适用于首次本地初始化，生产部署后必须修改。
8. **上传边界**：商品图片已限制类型/大小/像素并重新编码，静态路径隔离HTML/SVG及活动内容；销售文件等其他上传仍需分别核查，详见[商品流程](docs/modules/products/workflow.md#创建与图片)。
9. **报表权限风险**：运营用户的销售记录会过滤，但库存汇总没有完全按用户过滤。
10. **时区风险**：部分时间使用 Python 本地时间或无时区 `datetime`，跨服务器部署时必须统一时区策略。
11. **输入兼容风险**：`test_main.http` 仍使用旧字段 `duty_cost`（关税费用），当前模型使用 `last_mile_cost`（尾程费用）。
12. **测试范围**：已有 `tests/` 自动化测试；FIFO 并发回归测试使用锁语义模拟，部署前仍应在目标 MySQL/InnoDB 环境验证。
13. **国际化缺口**：模板、API 错误及 Excel 文案尚无统一翻译机制；OA 中西文组件名参与业务匹配。影响界面/导出及外部取数，建议按实际多语言需求渐进集中资源，保持来源契约兼容；现状已静态核对，语言范围待确认，见[国际化规范](docs/knowledge/internationalization.md)。
14. **容量边界**：销售导入整体读取文件，报表加载历史事实并在内存生成 Excel；商品导入已有上限，不能推广为全部上传入口已受限。后续以实际数据量验证文件额度、查询内存、锁等待及超时，再决定分批/限额；本次未压测。代码及缓存复用见[架构容量边界](docs/knowledge/architecture.md#复用能力与容量边界)。
15. **审计与历史扩展**：已有成本明细、来源JSON和导入撤销标记，但没有统一不可变操作台账；商品导入结果不持久化，撤销会删除销售明细。建议审计需求先明确保留对象及期限，不把动态报表当冻结账；外部OA更新与当前店铺归属影响见[集成边界](docs/knowledge/integrations.md#一致性审计及变更核对)。

## 10. 后续改造规则

后续要改造本项目时，必须按以下顺序执行：

1. 先阅读本文档和 `PROJECT_RUNBOOK.md`；
2. 明确是本地数据库还是服务器数据库；
3. 任何写数据库操作前先做只读核查和备份；
4. 数据库结构变更必须提供迁移脚本和回滚方案；
5. FIFO、库存剩余数量和销售成本明细必须增加回归测试；
6. 先在本地测试，再执行服务器部署；
7. 不能把上传文件目录当成数据库备份；
8. 不能直接删除 Docker 容器、存储卷或数据库表来解决异常；
9. 不得把密码、JWT 密钥或数据库连接字符串写入日志和说明文档；
10. 未经明确要求，不上传 GitHub，不部署服务器；
11. 每次改动后必须更新本文档、`PROJECT_RUNBOOK.md` 和变更记录；
12. 运行 Python 语法检查、接口验证和关键业务测试后，才能进入部署评估。

## 11. 建议的改造优先级

### 第一阶段：安全和可回滚

- 移除默认 JWT 密钥和默认账号密码的生产依赖；
- 复核 `viewer` 对商品和平台 SKU 映射的写入权限；
- 增加数据库备份和迁移流程；
- 商品图片校验已实现；其他文件入口按业务补充类型、大小与内容校验；
- 增加自动化测试基础。

### 第二阶段：库存一致性

- 持续验证 FIFO 事务行锁和并发导入；
- 为批次编号增加数据库唯一约束下的重试机制；
- 为销售创建增加幂等键，防止重复订单重复扣库存；
- 增加库存、批次和成本明细一致性检查。

### 第三阶段：报表和权限

- 明确运营用户可查看的库存范围；
- 统一销售、库存、成本和利润的统计口径；
- 增加报表导出和审计日志前先确认权限边界；
- 统一时间字段和报表时区。

## 12. 只读排障命令

查看容器和日志：

```bash
docker compose ps
docker compose logs --tail=200 web
docker inspect fifo-inventory --format '{{json .Mounts}}'
```

确认数据库网络连通性：

```bash
nc -vz -w 5 172.19.49.226 3306
```

连接后只读核对：

```sql
SELECT DATABASE();
SHOW TABLES;
SELECT COUNT(*) FROM products;
SELECT COUNT(*) FROM inventory_batches;
SELECT COUNT(*) FROM sales;
SELECT COUNT(*) FROM sale_cost_details;
```

任何 `DELETE`（删除）、`UPDATE`（更新）、`ALTER`（修改表结构）或清理 Docker 存储卷的操作，都必须在明确授权、完成备份并确认影响范围后执行。

## 13. 当前状态

- 2026-10-15追加：07、09、10、12采用持有期间增提及销售转销，新服务不写业务数据/快照；新增三项报表变动及损益转销抵减成本，异常/跨店归属未知时留空。当前实现和本地验证见[实施记录](docs/development/2026-10-15-holding-period-impairment.md)，服务器未发布。

- 2026-10-15：07存货报表按用户更正恢复期末准备余额，账面为现存成本减准备；保留成本追溯及SKU汇总，另列现存库存本月增提。无数据库结构变更，本地8010与MySQL备份已核验，生产未更新；实现及验证边界见[报表模块](docs/modules/reports/overview.md)和[开发决策](docs/development/2026-10-15-inventory-balance-impairment.md)。

- 2026-10-08：FIFO 扣库、销售导入库存预检、批次编辑/删除使用事务行锁；增加并发回归测试，无数据库结构变更。
- 2026-10-08：本机 `mysql57` 容器创建独立 `inventory_fifo` 测试库，初始化 17 张表，并在 MySQL 5.7.43/InnoDB 上通过真实并发扣库验证；测试销售和商品已清理。
- 2026-10-12：为兼容服务器备份的 MySQL 8.0.46 与 `utf8mb4_0900_ai_ci`，新建本机 `fifo-mysql8-test` 容器及专用数据卷，恢复到 `127.0.0.1:3309` 的 `inventory_fifo_test`；17 张表、5,631 条记录逐表核对一致，项目 `.env` 已切换，原 MySQL 5.7 测试库保留。随后成功启动宿主机 FastAPI（`127.0.0.1:8010`），正常生命周期完成，首页和接口定义均返回 HTTP 200；关键表数量与恢复时一致，财务报表尚未执行业务验收。

以下为 2026-08-19 的历史审阅记录，不代表当前环境状态：

- 已完成项目结构和核心逻辑审阅；
- 本次已将本地开发 MySQL 连接切换为 `localhost:3306`、`root` 用户；
- 已确认本机 MySQL 版本为 `8.0.34`，但尚未发现 `inventory_fifo` 数据库；
- 已确认 `fifo-inventory` 不包含 MySQL 数据库容器；
- 已确认 `uploads/` 只是图片文件持久化目录；
- 已确认本地项目没有 Git 仓库；
- 本次只新增本说明文档，没有修改业务代码、数据库、Docker 容器或服务器；
- 后续改造前必须先更新本文档中的“当前状态”和“改造范围”。

## 2026-10-19财务增强增量

当前源码为27表305字段、8组路由86个API（另有首页和脚本静态入口）；8张财务表默认关闭，真实库状态需单独核验。独立调整层保留原FIFO，晚到成本追溯销售月；台账显式替换人工费用，平台差额分类不再次计费，回款独立核对；月度版本保存冻结结果和来源清单。

规则、权限、数据字典和接口统一见[财务增强模块](docs/modules/financial-control/overview.md)，运行/加表及已使用后不能关闭开关的约束见[运行步骤](PROJECT_RUNBOOK.md#财务增强结构与启用)。本轮没有连接业务库迁移或部署，历史章节中的数量/环境记录按当时日期理解。

2026-10-19浏览器回归发现MySQL默认时间列在审计序列化时触发隐式异步IO，阻断六类财务保存。现将公共audit改为异步，在原事务中flush并显式加载未加载列；各写入入口统一await，凭证和审计仍统一提交/回滚。无需结构、API或前端变更；维护规则与验证入口见[财务流程](docs/modules/financial-control/workflow.md#事务与审计默认字段)。
