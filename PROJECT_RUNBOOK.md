# FastAPIProject 运行与维护说明

> 强制约定：以后每次运行、部署、排障或修改本项目之前，先完整阅读本文件。代码、配置或部署方式变化后，同步更新本文件。

> 财务报表改造：涉及店铺、TikTok Shop 同步、订单/结算/费用、库存成本或四张财务 Excel 导出时，还必须阅读 `FINANCIAL_REPORTING_TARGET.md`。该文件定义目标模板、字段映射、待确认财务口径和实施顺序。

## 项目概览

- FastAPI + SQLAlchemy 异步 ORM + MySQL 的 FIFO 库存成本核算系统。
- 入口为 `main.py`；根路径 `/` 返回 `templates/index.html`，接口文档为 `/docs`。
- Compose 服务名 `web`，容器名 `fifo-inventory`，端口映射为宿主机 `8006` 到容器 `8000`。
- 商品图片在项目根目录 `uploads/`，挂载到容器 `/app/uploads`，访问路径 `/uploads/<文件名>`。
- `test_main.http` 是手工请求示例，不是自动化测试；项目当前没有测试套件。

## 每次运行前检查

1. 阅读本文件和项目根目录的 `.env`，不要把密码复制到日志、截图或文档。
2. 检查 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`。当前本地开发配置为 `localhost:3306`、用户 `root`、库名 `inventory_fifo`；该库仅作为本机测试库，已于 2026-08-20 初始化。服务器或 Docker 部署不能直接使用 `localhost`，必须配置容器可访问的 MySQL 地址。
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
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 启动逻辑与配置

`main.py` 启动时先执行 `database.init_db()`，用 SQLAlchemy `create_all` 创建缺失表；再执行 `migrate()` 添加缺失的兼容字段；最后执行 `auth.seed_users()`，仅在 `users` 表为空时创建默认用户。这不是完整的版本化迁移。

`database.py` 从项目根目录 `.env` 读取 MySQL 配置，使用 `mysql+aiomysql` 异步连接。当前 `.env` 适合直接在本机运行 FastAPI；Docker 容器内的 `localhost` 指向容器自身，因此 Docker 或服务器部署必须单独配置数据库地址。JWT 使用 HS256、有效期 24 小时。`JWT_SECRET` 可用环境变量覆盖，但当前 Compose 未传入该变量，生产部署必须补充强随机值。

`services/oa_office_expenses.py` 使用独立的 `OA_DB_*` 配置以只读方式连接 OA PostgreSQL。它只统计流程 `PROC-E7BC3316-E618-4812-BDCC-7A655A7C694B` 中申请日期落在报表期间、状态为已完成且同意的记录。管理支出为“办公场地总费用”时，仅取 `LatínGo拉丁购`（部门 ID `1089990115`）的租金和电费明细，合计后均分给当前有效店铺；管理支出为“工资中国Salario en China”时，按明细中的部门名称与系统店铺名称精确匹配并带入“人员薪资”，该值仍可人工修改并保存，已保存人工值优先。OA 配置缺失时，本地开发报表对应自动值为 0；配置存在但读取失败时接口必须报错，不能以 0 掩盖生产连接故障。

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

FIFO 依据批次 `arrived_at` 升序，从 `remaining_quantity > 0` 的批次扣减。一次销售跨多个采购成本不同的批次时，`total_cost` 为各批次 `unit_cost × 扣减数量` 之和，页面展示的 FIFO 成本单价为 `total_cost ÷ 销售数量`；这里使用包含采购价和物流费用的单件成本，保证与库存计价口径一致。库存不足时，手工销售返回 HTTP 400；订单详情批量导入按 Excel 行顺序逐行判断，每一行及其所有组合商品只有在库存能够完整满足时才整行扣减，否则整行进入待确认且不扣任何库存，剩余库存可继续匹配后续数量更小的行。

订单详情导入会解析 `A*3`、`A+B*3` 等商品名称。跨商品组合按同一导入文件中、同币种且日期最接近的单独销售价格分摊组合销售额和平台费；单独价格优先取 `商品原价小计 ÷ 数量`，缺失时回退到 `净商品销售额 ÷ 数量`，组成数量会计入权重。任一组成商品没有可用的单独售价时，整条结算行进入待确认区，不创建销售或扣减库存。

## API 与角色

除 `/auth/login` 和 `/auth/register` 外，请求均要求 `Authorization: Bearer <JWT>`。

- `admin`：全部功能和用户管理。
- `operator`：创建、编辑、删除商品和批次，记录销售；可查看和导出全部批次，但只能编辑、删除自己创建且未被销售消耗的批次；只能看到自己创建的销售。
- `viewer`：只读商品、批次、销售和报表。

主要接口：`/auth/*` 登录和用户管理；`/products` 商品和图片；`/products/options` 商品下拉框轻量数据；`/batches` 入库批次；`/batches/export?batch_ids=1&batch_ids=2` 导出一个或多个批次为带图片 Excel；`/sales` 销售和成本明细；`/reports/monthly?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` 月度报表。

商品、批次和销售列表在传入 `page`（从 1 开始）与 `page_size`（1-100）时返回 `{items,total,page,page_size}`，供页面服务端分页使用；超过末页会自动返回最后一页。不传 `page` 时保留原有数组响应，兼容旧调用。商品/批次未勾选时的批量导出仍导出全部匹配数据。应用启动迁移会补齐批次按到货时间和 ID、批次商品加到货时间、销售时间和销售创建人加时间的索引；首次生产部署前应先备份数据库。

存货跌价准备导出中的当前库存按报告截止日前已确认销售计算。上月末账面价值由后台按 `Asia/Shanghai` 自然月自动生成的批次快照固定：应用启动时及之后每小时检查上一完整自然月，缺失快照的历史月份会在首次导出时补建。快照一经生成，不因后续补录销售、修改成本或商品类型而重算；系统不提供人工结账页面、确认按钮或公开会计期间接口，且不能导出未来报告日。

## 前端行为

前端有商品、入库、图册、销售、报表和管理员用户管理。入库表单在头程运费、尾程派送费和其他成本后各提供一个可选的钉钉单号字段：头程运费钉钉单号、尾程派送费钉钉单号、其他成本钉钉单号；批次列表、“显示字段”面板和 Excel 导出表头同步使用这些名称。批次操作栏可单条导出，列表工具栏可批量导出当前列表，导出 Excel 会嵌入商品图片。图册图片右下角提供下载按钮。批次字段勾选只影响页面列显示，不筛掉批次、修改数据库或影响 FIFO 成本。管理员可在用户管理中建立店铺，并将非管理员账号绑定到一个有效店铺；未绑定店铺的运营账号不能执行销售文件导入。导入时会把用户当时绑定的店铺同时固化到销售和结算记录，之后改绑不会改变历史店铺归属。销售页面按天录入，会自动把 `selling_price` 与 `platform_fee` 传为 `0`，订单号为 `日期-SKU`。CSV 格式为 `SKU,YYYY-MM-DD,数量`，逐行调用销售接口。销售列表按“销售单录入模板”展示店铺、日期、SKU、产品名称、销售数量、人民币销售单价、FIFO 成本单价、人民币销售收入、销售成本、人民币平台费和人民币毛利，并在表尾汇总当前页。非 CNY 导入按订单日期从 Frankfurter/ECB 获取历史汇率，非交易日使用该日之前最近的可用交易日；汇率、实际汇率日期和来源在导入时固化到结算行，页面只显示人民币金额并在销售单价悬浮提示中保留原币与汇率依据。汇率缺失会阻止导入，不能静默按零处理。每次成功上传会创建 `sales_import_batches` 记录；导入人可撤销自己的文件，管理员可撤销全部文件，撤销会按 `sale_cost_details` 恢复原 FIFO 批次库存并删除该批次产生的销售、分摊和结算行，导入批次保留为已撤销状态。新导入记录优先展示导入时固化的店铺，旧记录没有固化店铺时才尝试通过有效店铺 SKU 映射推断，否则标记为未关联店铺。需要准确利润时，必须通过 API 传入真实售价和平台费，不能把当前页面录入的利润当真实利润。

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
