# FastAPIProject 项目架构与改造基线

> 本文档是本项目后续开发、排障和部署前的必读说明。任何代码、数据库结构、配置或部署方式发生变化后，必须同步更新本文档和 `PROJECT_RUNBOOK.md`。

## 1. 项目定位

`FastAPIProject` 是一个基于 FastAPI 的跨境电商库存与 FIFO 成本核算系统，当前主要负责：

- 商品和 SKU 管理；
- 商品图片上传与访问；
- 入库批次管理；
- 按批次计算单件成本；
- 按 FIFO（先进先出）规则处理销售出库；
- 记录销售成本明细和利润；
- 提供月度销售与库存报表；
- 提供用户登录、角色权限和密码管理。

本项目与钉钉审批、`dingtalk-oa`、`dingtalk-expense-sync`、`dingtalk-budget` 没有代码级依赖。当前数据库是本项目独立使用的 MySQL 库 `inventory_fifo`。

## 2. 当前运行拓扑

```text
浏览器
  -> fifo-inventory Web 容器
     -> FastAPI / Uvicorn（应用端口 8000）
     -> MySQL localhost:3306（本机开发数据库服务）
     -> 项目 uploads/ 目录（宿主机持久化文件）
```

Docker Compose 对外提供：

```text
宿主机 8006 -> 容器 8000
```

当前 Docker 容器：

```text
容器名：fifo-inventory
容器地址：172.26.0.2（Docker 内网地址）
服务器历史数据库地址：172.19.49.226:3306
数据库名：inventory_fifo
```

服务器历史配置中的 `172.19.49.226` 不属于 `fifo-inventory` 当前 Docker 网络，也不是当前容器内的数据库。它是独立的 MySQL 服务或另一台内网主机。本地开发配置已经切换为 `localhost`，两者不能混用。

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

当前本地开发连接为：

```text
主机：localhost
端口：3306
用户：root
数据库：inventory_fifo（本机测试库，已于 2026-08-20 初始化）
```

该数据库名是应用配置中的目标库。本机 `inventory_fifo` 仅供测试，不能与服务器数据库混用。应用启动会创建缺失表，但不会为既有表自动补齐新增字段；字段变更必须执行对应的迁移脚本。

注意：Docker 容器内的 `localhost` 指向容器自身，不指向宿主机。当前 `.env` 适合直接在本机运行 FastAPI；如果使用 Docker，不能直接把此配置用于容器，必须改回可从容器访问的数据库地址，例如宿主机网关或独立 MySQL 主机。

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
5. 应用开始提供 HTTP 接口。

当前不是完整的版本化数据库迁移系统。`create_all` 不会安全地自动补齐已有表的字段变更，`migrate.py` 仍然需要人工执行，而且其中部分异常会被静默忽略。批次钉钉单号、总金额和三个成本单号字段使用 `migrations/20260820_add_batch_dingtalk_fields.py`，可重复执行且只添加缺失列。

## 5. 目录与模块职责

```text
main.py                 FastAPI 应用入口、生命周期、首页和 uploads 静态目录
database.py             .env 加载、异步 MySQL 引擎、数据库会话、建表
models.py               SQLAlchemy ORM（对象关系映射）模型
schemas.py              Pydantic 请求和响应模型
auth.py                 JWT（JSON Web Token，JSON 网络令牌）、密码哈希、认证依赖
migrate.py              手工兼容迁移脚本
routers/auth.py         登录、注册、用户和角色管理
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
test_main.http          手工请求示例，不是自动化测试
```

批次列表的“显示字段”是纯前端列可见性控制，默认显示全部字段；它不调用后端接口，也不改变批次查询、数据库或 FIFO 计算。

前端视觉基线：页面面向桌面端使用，采用统一的深色顶部栏、浅色导航和白色卡片布局；批次宽表设置最小列宽和固定高度的独立滚动区域，保留底部原生横向滚动条。数据较多时表头固定在区域顶部、横向滚动条固定在区域底部，避免用户滚到整个列表底部才可横向查看长字段和成本单号。商品和批次列表均使用前端分页（默认每页 20 条，可选 10、50、100 条）；已勾选的导出记录跨页保留，表头全选仅影响当前页。此次界面美化仅涉及 `templates/index.html` 和同步的根目录 `index.html`，不涉及后端接口、数据库结构和业务计算。

批次导出交互：批次表最左侧提供逐行复选框和表头全选框；`selectedBatchIds` 仅保存当前页面的选择状态，批量导出优先导出已选 ID，选择为空时导出当前列表全部 ID。编辑窗口通过固定定位居中显示，避免编辑操作导致页面跳转。

商品导出交互：商品表最左侧提供逐行复选框和表头全选框，工具栏提供批量导出，操作列提供单条导出；`selectedProductIds` 仅保存当前页面选择，`GET /products/export` 按传入商品 ID 导出 SKU、名称、库存、库存价值、最近入库、创建时间和嵌入图片。

## 6. 数据库表和业务关系

当前主要有五张表：

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

1. 查询同一商品且 `remaining_quantity > 0`（剩余数量大于 0）的批次；
2. 按 `arrived_at`（入库时间）升序排列；
3. 计算总可用库存；
4. 库存不足时返回 HTTP 400，不创建销售记录；
5. 从最早批次开始扣减，必要时跨多个批次；
6. 创建 `sales`（销售记录）；
7. 创建 `sale_cost_details`（成本明细）；
8. 在同一个数据库会话中提交事务。

重要约束：

- `remaining_quantity`（剩余库存）和成本明细必须保持一致；
- 不能直接修改已经被销售消耗的批次；
- 不能删除已有销售成本明细的批次；
- 修改历史批次成本会影响后续库存价值和利润报表。

## 8. 接口和权限

除登录和注册外，接口通过 `Authorization: Bearer <JWT>`（Bearer 令牌认证）保护。

### 8.1 认证接口

```text
POST /auth/login              登录
POST /auth/register           注册
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
PUT    /products/{id}         修改商品和图片
DELETE /products/{id}         删除商品
```

### 8.3 入库接口

```text
POST   /batches               新建入库批次
GET    /batches               查询批次
PUT    /batches/{id}          修改未被消耗的批次
DELETE /batches/{id}          删除未被消耗的批次
```

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
2. **FIFO 并发风险**：当前查询和扣减没有显式行锁，并发销售同一 SKU 可能超卖。
3. **批次编号并发风险**：批次号通过查询当天数量再加一生成，并发入库可能生成冲突编号。
4. **删除风险**：商品、用户、批次删除没有完整的业务级级联策略，删除前必须核对外键和历史记录。
5. **权限风险**：注册接口公开，外部访问时任何人都可能注册普通账号。
6. **JWT 安全风险**：`JWT_SECRET`（JWT 密钥）有代码默认值，Compose 当前没有显式传入，生产环境必须覆盖。
7. **默认账号风险**：默认账号和密码只适用于首次本地初始化，生产部署后必须修改。
8. **上传风险**：当前主要按原始扩展名保存文件，未完整限制文件类型、大小和内容。
9. **报表权限风险**：运营用户的销售记录会过滤，但库存汇总没有完全按用户过滤。
10. **时区风险**：部分时间使用 Python 本地时间或无时区 `datetime`，跨服务器部署时必须统一时区策略。
11. **输入兼容风险**：`test_main.http` 仍使用旧字段 `duty_cost`（关税费用），当前模型使用 `last_mile_cost`（尾程费用）。
12. **测试不足**：当前没有自动化测试套件，`test_main.http` 只是手工请求示例。

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
- 限制注册接口；
- 增加数据库备份和迁移流程；
- 增加上传文件类型、大小和文件名校验；
- 增加自动化测试基础。

### 第二阶段：库存一致性

- 为 FIFO 扣减增加事务锁；
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

截至 2026-08-19：

- 已完成项目结构和核心逻辑审阅；
- 本次已将本地开发 MySQL 连接切换为 `localhost:3306`、`root` 用户；
- 已确认本机 MySQL 版本为 `8.0.34`，但尚未发现 `inventory_fifo` 数据库；
- 已确认 `fifo-inventory` 不包含 MySQL 数据库容器；
- 已确认 `uploads/` 只是图片文件持久化目录；
- 已确认本地项目没有 Git 仓库；
- 本次只新增本说明文档，没有修改业务代码、数据库、Docker 容器或服务器；
- 后续改造前必须先更新本文档中的“当前状态”和“改造范围”。
