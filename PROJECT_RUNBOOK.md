# FastAPIProject 运行与维护说明

> 强制约定：以后每次运行、部署、排障或修改本项目之前，先完整阅读本文件。代码、配置或部署方式变化后，同步更新本文件。

> 财务报表改造：涉及店铺、TikTok Shop 同步、订单/结算/费用、库存成本、月末关账或四张财务 Excel 导出时，还必须阅读 `FINANCIAL_REPORTING_TARGET.md`。该文件定义目标模板、字段映射、待确认财务口径和实施顺序。

## 项目概览

- FastAPI + SQLAlchemy 异步 ORM + MySQL 的 FIFO 库存成本核算系统。
- 入口为 `main.py`；根路径 `/` 返回 `templates/index.html`，接口文档为 `/docs`。
- Compose 服务名 `web`，容器名 `fifo-inventory`，端口映射为宿主机 `8006` 到容器 `8000`。
- 商品图片在项目根目录 `uploads/`，挂载到容器 `/app/uploads`，访问路径 `/uploads/<文件名>`。
- `test_main.http` 是手工请求示例，不是自动化测试；项目当前没有测试套件。

## 每次运行前检查

1. 阅读本文件和项目根目录的 `.env`，不要把密码复制到日志、截图或文档。
2. 检查 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。当前本地开发配置为 `localhost:3306`、用户 `root`、库名 `inventory_fifo`；该库仅作为本机测试库，已于 2026-08-20 初始化。服务器或 Docker 部署不能直接使用 `localhost`，必须配置容器可访问的 MySQL 地址。
3. 确认运行服务器能连接 MySQL，且数据库账号有建表和改表权限。应用启动会创建缺失表。
4. 确认 `uploads/` 可写、磁盘空间足够、宿主机端口 `8006` 未被占用。
5. 生产环境必须更换数据库密码、默认账号密码和 JWT 密钥，并使用 HTTPS/反向代理。
6. 启动后检查日志，确认数据库连接、建表和默认用户初始化没有异常。

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
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 启动逻辑与配置

`main.py` 启动时先执行 `database.init_db()`，用 SQLAlchemy `create_all` 创建缺失表；再执行 `migrate()` 添加缺失的兼容字段；最后执行 `auth.seed_users()`，仅在 `users` 表为空时创建默认用户。这不是完整的版本化迁移。

`database.py` 从项目根目录 `.env` 读取 MySQL 配置，使用 `mysql+aiomysql` 异步连接。当前 `.env` 适合直接在本机运行 FastAPI；Docker 容器内的 `localhost` 指向容器自身，因此 Docker 或服务器部署必须单独配置数据库地址。JWT 使用 HS256、有效期 24 小时。`JWT_SECRET` 可用环境变量覆盖，但当前 Compose 未传入该变量，生产部署必须补充强随机值。

## 文件职责

```text
main.py                 应用入口、生命周期、首页、静态 uploads
database.py             .env、异步 MySQL 引擎、会话、建表
auth.py                 JWT、密码哈希、认证依赖、默认用户
models.py               五张数据表的 ORM 模型
schemas.py              Pydantic 请求和响应模型
migrate.py              手工兼容迁移，不会自动运行
routers/auth.py         登录、注册、用户角色和密码管理
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

数据库 `inventory_fifo` 有五张表：

- `users`：用户名唯一、密码哈希、角色 `admin`、`operator`、`viewer`。
- `products`：SKU 唯一、名称、图片路径。
- `inventory_batches`：批次、入库和剩余数量、采购价、钉钉单号、总金额、头程/尾程/其他成本及其可选单号、单件成本、发货和入库时间、创建者。总金额由用户填写，后端固定计算采购单价为 `总金额 ÷ 数量`，不接受前端覆盖；旧接口未传总金额时仍兼容采购单价输入。
- `sales`：订单、数量、销售单价、FIFO 总成本、平台费、利润、创建者。
- `sale_cost_details`：一笔销售对应的批次扣减明细。

单件成本：

```text
purchase_price + (shipping_cost + last_mile_cost + other_cost) / quantity
```

利润：

```text
selling_price * quantity - total_cost - platform_fee
```

FIFO 依据批次 `arrived_at` 升序，从 `remaining_quantity > 0` 的批次扣减。库存不足时返回 HTTP 400，销售不会创建。

## API 与角色

除 `/auth/login` 和 `/auth/register` 外，请求均要求 `Authorization: Bearer <JWT>`。

- `admin`：全部功能和用户管理。
- `operator`：创建、编辑、删除商品和批次，记录销售；可查看和导出全部批次，但只能编辑、删除自己创建且未被销售消耗的批次；只能看到自己创建的销售。
- `viewer`：只读商品、批次、销售和报表。

主要接口：`/auth/*` 登录和用户管理；`/products` 商品和图片；`/batches` 入库批次；`/batches/export?batch_ids=1&batch_ids=2` 导出一个或多个批次为带图片 Excel；`/sales` 销售和成本明细；`/reports/monthly?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` 月度报表。

## 前端行为

前端有商品、入库、图册、销售、报表和管理员用户管理。入库表单在头程运费、尾程派送费和其他成本后各提供一个可选的钉钉单号字段：头程运费钉钉单号、尾程派送费钉钉单号、其他成本钉钉单号；批次列表、“显示字段”面板和 Excel 导出表头同步使用这些名称。批次操作栏可单条导出，列表工具栏可批量导出当前列表，导出 Excel 会嵌入商品图片。图册图片右下角提供下载按钮。批次字段勾选只影响页面列显示，不筛掉批次、修改数据库或影响 FIFO 成本。销售页面按天录入，会自动把 `selling_price` 与 `platform_fee` 传为 `0`，订单号为 `日期-SKU`。CSV 格式为 `SKU,YYYY-MM-DD,数量`，逐行调用销售接口。需要准确利润时，必须通过 API 传入真实售价和平台费，不能把当前页面录入的利润当真实利润。

界面样式以桌面端为主：统一使用浅灰画布、白色内容卡片、深色标题栏和红色主操作色；批次列表使用固定最小宽度的横向滚动宽表，避免字段标题被压缩成竖排；商品、销售、报表和用户列表也使用统一的表格容器、表头、边框和按钮样式。该次视觉调整只修改前端 HTML/CSS，不改变 API、数据库字段、导出逻辑或权限行为。`templates/index.html` 是运行时首页，根目录 `index.html` 与其保持同步。

批次与商品列表默认每页显示 20 条，可切换为每页 10、20、50 或 100 条并通过页码翻页。复选框选择会跨页保留，表头全选仅选择当前页；选中一条或多条后点击“批量导出”只导出所选记录，没有任何勾选时导出当前完整列表。批次列表提供“搜索商品”输入框，可按商品名称、SKU 或批次号模糊搜索；搜索结果同样可分页、勾选和导出。批次宽表使用固定高度的独立滚动区域：仅保留表格底部原生横向滚动条，数据较多时横向滚动条固定在该区域底部、表头固定在区域顶部，无需滚到整个列表底部即可左右查看字段。编辑商品和编辑批次均在页面中央固定窗口中打开，不再调用 `scrollIntoView` 改变页面滚动位置。

商品列表同样提供左侧复选框、表头全选、单条导出和批量导出；勾选商品后批量导出只导出所选商品，未勾选时导出当前商品列表全部商品。`GET /products/export?product_ids=1&product_ids=2` 返回带商品图片的 Excel，权限与商品查询一致。

## 默认账号与敏感信息

仅在 `users` 表为空时初始化：`admin/admin123`、`operator/op123`、`viewer/view123`。首次部署后必须立即改密码。`.env` 含数据库密码，`mysql-init/init.sql` 含 root 初始密码，`auth.py` 有 JWT 默认密钥，均不得用于长期生产配置。

## 已知问题与维护风险

1. Compose 未传 `JWT_SECRET`，生产部署必须加入。
2. `/auth/register` 对外开放，任何人可注册 operator/viewer；内网系统以外的部署应关闭或增加审批。
3. 月度报表会过滤 operator 的销售，但库存汇总未按用户过滤，可能泄露全局库存。
4. 删除商品或用户没有显式级联策略；有批次或销售关联时，删除前必须备份并确认外键影响。
5. FIFO 查询没有显式行锁；并发销售同一商品可能超卖。
6. 批次号按当天已有数量加一生成；并发入库可能触发唯一约束冲突。
7. `test_main.http` 使用过时字段 `duty_cost` 且缺少认证头；当前字段为 `last_mile_cost`。
8. `migrate.py` 需手工运行，且静默忽略部分异常；执行前必须备份数据库。新增批次留档字段（含三个成本单号）使用独立的 `migrations/20260820_add_batch_dingtalk_fields.py`，脚本会先检查列是否存在，可重复执行。
9. 上传仅使用原文件扩展名，未限制大小和类型；公网部署前应限制类型、大小和反向代理上传额度。

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

新增钉钉单号、自动计算的总金额和成本单号字段前，先备份目标库，再执行：

```bash
python migrations/20260820_add_batch_dingtalk_fields.py
```

将黄丽瑶名下入库批次的操作归属转给 `Navi` 前，先备份目标库；然后在已更新的 `web` 容器中执行一次：

```bash
docker compose exec web python migrations/20260909_transfer_huang_liyao_batches_to_navi.py
```

该脚本只更新 `inventory_batches.user_id`，不会修改批次成本、库存数量、销售记录或用户账号；脚本会按用户名精确核对“黄丽瑶”和“Navi”，且 `Navi` 必须为 `operator`。已被销售消耗的批次仍受现有规则限制，不能编辑或删除。

容器无法启动时，依次检查 `.env` 是否完整、MySQL 是否可达、用户是否允许该来源和具备权限、端口 `8006` 是否被占用。不要为了重置问题直接删除容器、数据卷或数据库。

## GitHub 代码发布与服务器更新

当前 GitHub 公有仓库为 `https://github.com/yueweiit/FastAPIProject`，默认分支为 `main`。服务器通过 `git pull --ff-only` 获取新版本，公有仓库无需在服务器配置 GitHub Token 或 Deploy Key。`.gitignore` 排除了 `.env`、`uploads/`、`output/`、`.deploy/`、虚拟环境和压缩包；仓库只保存代码、Docker 配置和文档。首次发布可参考 `DEPLOYMENT_HANDOFF.md` 第 13 节。服务器更新前必须先确认 `.env` 和 `uploads/` 仍在项目目录，并备份数据库；更新后执行 `docker compose build web` 和 `docker compose up -d --force-recreate web`，再检查容器、日志和 `curl` 页面状态。禁止将个人 Token、数据库密码或服务器密码提交到 GitHub。

## 审阅来源

本文件基于 `FastAPIProject_mefrn.tar.gz` 于 2026-08-19 解压后审阅的源码、部署文件、配置、前端和手工示例整理。`.venv/`、`.idea/`、`__pycache__/` 为第三方或本地生成内容，未作为业务逻辑审阅。
