# 项目概览

## 项目定位

项目提供跨境电商商品、批次库存和 FIFO 销售成本核算，并扩展为平台销售文件导入与人民币财务报表工具。核心使用者为管理员、运营和查看者；角色名称不能直接推导权限，查看者也可修改部分共享主数据。依据：[main.py](../../main.py)、[角色机制](authentication.md)。

业务主要链路：商品及映射配置 → 批次入库 → 手工销售或平台文件导入 → FIFO 扣减及成本明细 → 销售查询、月度汇总、财务报表。完整功能见[业务地图](business-map.md)。

## 技术栈

| 层次 | 实际实现和证据 |
| --- | --- |
| 前端 | 原生 HTML/CSS/JavaScript，单文件页面、原生 fetch；[templates/index.html](../../templates/index.html)。未发现独立 Vue/React 项目、package.json 或前端构建流程 |
| Web | FastAPI、Uvicorn、Python；[main.py](../../main.py)、[Dockerfile](../../Dockerfile) |
| 数据访问 | SQLAlchemy 2.x 异步 ORM、aiomysql；[database.py](../../database.py) |
| 校验 | Pydantic 2.x 请求/响应模型和 FastAPI Form/File/Query；[schemas.py](../../schemas.py) |
| 主库 | MySQL，默认库名 inventory_fifo；金额以 DECIMAL、原始结算行及人工报表值以 JSON 保存；[models.py](../../models.py) |
| 登录 | 本地 bcrypt 与可撤销 Cookie；EIMS 授权码/PKCE、RS256、Fernet 加密后端 Token、httpx；保留 local/hybrid 的传统 HS256 Bearer；[认证模块](../modules/auth/overview.md) |
| 文件 | openpyxl、Pillow，Excel 读取/导出和图片嵌入；[商品路由](../../routers/products.py)、[报表路由](../../routers/reports.py) |
| 外部来源 | asyncpg 只读 OA PostgreSQL；urllib 请求 Frankfurter 历史汇率；[OA 服务](../../services/oa_office_expenses.py)、[汇率服务](../../services/exchange_rates.py) |
| 验证 | unittest/IsolatedAsyncioTestCase，模拟数据库、网络和锁；另有写入本地测试库的真实并发脚本；[tests/](../../tests/) |
| 部署 | Docker 单个 web 服务，外置 MySQL、可选 OA PostgreSQL，上传目录挂载；[docker-compose.yml](../../docker-compose.yml) |

版本范围以 [requirements.txt](../../requirements.txt) 为准，大多数依赖只有最低版本约束，bcrypt 固定为 4.0.1；未发现锁文件。Docker 镜像声明 Python 3.13-slim，当前实际已安装版本未验证。

## 入口与目录

```text
main.py                 应用组装、生命周期、首页、静态图片
database.py / models.py  异步会话与 19 张 ORM 表（含两张认证状态表）
schemas.py / auth.py     数据契约、认证和角色依赖
migrate.py              启动兼容迁移和历史记录修复
routers/                七组 HTTP 路由，认证含本地与 EIMS 两组
services/               认证会话、EIMS、商品导入、FIFO、汇率、跌价、快照和 OA
templates/index.html    实际运行首页，内嵌 CSS/JavaScript
index.html              页面副本；前端修改需核对两份文件
tests/                  单元测试和本地 MySQL 并发验证脚本
migrations/             历史字段迁移、批次创建人转移脚本
mysql-init/             历史 MySQL 初始化 SQL；Compose 未挂载
uploads/                运行期商品图片；Git 忽略、需单独持久化
Dockerfile / docker-compose.yml
.env.example            配置变量示例，不能代表实际环境
docs/knowledge/         全局知识库
docs/modules/           六个模块的业务、流程、代码、表和 API
```

源码路径均以仓库根目录为基准。实际请求入口为 Uvicorn `main:app`：`/` 读取模板，`/uploads` 公开提供图片，默认 FastAPI `/docs`、`/redoc`、`/openapi.json` 提供接口说明；路由由 `main.py` 的七次 `include_router` 注册。

当前两份 index.html 在归一化换行后仍有内容差异，以 templates/index.html 为运行依据。2026-10-12 商品导入按功能块同步两份页面，保留平台 SKU 懒加载等原有差异。

## 当前实现边界

系统为进程内单体，未发现独立 Controller、消息队列、Redis、定时任务平台或独立前端服务。月末快照及认证过期清理是 FastAPI 生命周期的 asyncio 任务。店铺隔离通过具体查询实现，没有统一 tenant_id 或数据库行级隔离；EIMS 不改变这条业务边界。

本仓库有 Git，没有根目录 README；知识库与项目 AGENTS.md 已建立。初始化时复用了已有根目录架构、运行维护、交接及财务目标文档，后续从[总索引](README.md)恢复相关上下文。

数据库版本、服务运行状态、真实店铺和库存数据不由源码推断，见[待验证清单](README.md#待验证清单)。
