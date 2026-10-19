# 运行环境与部署方式

## 源码定义的部署

[Dockerfile](../../Dockerfile) 使用 python:3.13-slim，工作目录 /app，安装 requirements.txt、复制项目、建立 uploads，然后运行：

```text
uvicorn main:app --host 0.0.0.0 --port 8000 --no-access-log
```

[docker-compose.yml](../../docker-compose.yml) 只定义 web：容器名 fifo-inventory，restart 为 unless-stopped，宿主机 8006 映射容器 8000；./uploads 挂载 /app/uploads，host.docker.internal 映射 host-gateway。没有 MySQL、PostgreSQL、Redis 或代理服务定义。

MySQL 必须单独提供；需要 OA 自动费用时再配置外部 PostgreSQL。容器中的 localhost 指容器自身，不能直接套用宿主机连接地址。2026-10-12 已核实本机的备份测试环境：Docker Desktop `fifo-mysql8-test`（MySQL 8.0.46）通过 `127.0.0.1:3309` 提供 `inventory_fifo_test`，项目本地 `.env` 已切换并通过异步连接读取检查；宿主机 FastAPI 已在 `127.0.0.1:8010` 成功启动，首页检查通过，原 MySQL 5.7 测试库保留。恢复、启动验证、持久化与限制统一维护在 [PROJECT_RUNBOOK.md](../../PROJECT_RUNBOOK.md#服务器备份的本地恢复)。生产运行拓扑、端口占用和代理配置仍待验证，不能将历史记录当作实时状态。

## 配置变量

只记录变量名和用途，不记录任何实际凭据。依据：[.env.example](../../.env.example)、[database.py](../../database.py)、[auth.py](../../auth.py)、[OA 服务](../../services/oa_office_expenses.py)。

| 变量 | 作用 | 注意 |
| --- | --- | --- |
| MYSQL_HOST、MYSQL_PORT | 主库地址和端口 | Compose 要求主机；端口缺省 3306 |
| MYSQL_DATABASE | 业务库 | 默认 inventory_fifo |
| MYSQL_USER、MYSQL_PASSWORD | 主库账号 | Compose 要求账号和密码；启动会建表/迁移 |
| JWT_SECRET | 兼容本地 JWT 签名 | Compose 必填；生产强随机，不采用源码默认值 |
| FIFO_AUTH_MODE | local/hybrid/eims | 缺省 hybrid；eims 服务端禁用本地密码/Bearer |
| FIFO_PUBLIC_BASE_URL | 固定公开来源、回调、Cookie/CSRF | 生产 https://fifo.yueweiportal.com；本地单独配置 |
| EIMS_ISSUER | 固定 OIDC 提供方 | https://eims.yueweiportal.com，无末尾斜杠 |
| EIMS_CLIENT_ID、EIMS_CLIENT_SECRET | FIFO OAuth 客户端 | 注册后通过安全渠道后端配置；不发聊天/Git |
| SSO_TOKEN_ENCRYPTION_KEY | 后端临时数据/Token 的 Fernet 密钥 | 独立密钥；变更使旧 SSO 加密会话失效 |
| OA_DB_HOST、OA_DB_PORT | OA PostgreSQL 地址和端口 | 可选，端口缺省 5432 |
| OA_DB_DATABASE、OA_DB_USER、OA_DB_PASSWORD | OA 查询数据库及账号 | 应用只查表，账号实际只读权限待验证 |

database.py 在加载时读取根目录 .env；Dockerfile COPY . . 配合 [.dockerignore](../../.dockerignore) 排除了 .env，当前 Compose 已显式注入 MySQL/OA/JWT/SSO 变量，环境变更需重新创建容器。OA 必需配置缺失或不完整时服务视为未配置；完整配置但查询失败时抛错。EIMS 配置不全仅关闭 SSO，hybrid 保留本地登录；配置完整不代表真实客户端登录已验收。

## 启动前的数据影响

应用生命周期执行 create_all、migrate、默认用户初始化和月末快照。migrate 不只改结构，还会修复历史结算/导入记录。开发账号若只有业务 DML 权限，可能无法启动；生产启动权限及迁移流程需单独设计。依据：[main.py](../../main.py)、[migrate.py](../../migrate.py)。

2026-10-13 认证新增 users.is_active（既有用户默认启用）及 auth_login_transactions/auth_sessions 两张 InnoDB 表；使用既有启动迁移链，另有每小时过期认证记录清理。已在独立本地 MySQL 验证，未执行生产迁移。发布前核实备份、CREATE/ALTER/外键及实际用户状态；详情与回调登记见[接入说明](../modules/auth/eims-sso-setup.md)。

只做文档核验时不能运行 Uvicorn、迁移脚本或真实 MySQL 测试脚本。下列命令记录既有用法；实际本地启动验证见运行手册的恢复记录，不能据此推断部署命令也已执行：

```powershell
# 环境与数据库已准备并获运行授权时
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8010
```

```bash
# 部署已获授权且备份/配置检查完成时
docker compose build web
docker compose up -d --force-recreate web
docker compose ps
docker compose logs --tail=200 web
```

本地 `8010` 已用于 2026-10-12 的运行验证；每次重新启动仍需检查端口占用。完整部署及备份操作沿用 [DEPLOYMENT_HANDOFF.md](../../DEPLOYMENT_HANDOFF.md) 和 [PROJECT_RUNBOOK.md](../../PROJECT_RUNBOOK.md)，不在此重复服务器地址、凭据或历史 Git 初始化命令。

## 持久化与安全限制

MySQL 数据和 uploads 图片分别备份；上传目录不是数据库备份。当前 Compose 未定义数据库卷；mysql-init/init.sql 也没有被此服务挂载执行。默认不覆盖已有 .env/uploads，不删除数据库、容器卷或上传文件，不用危险 Git 清理处理环境故障。

.gitignore 排除了 .env、uploads、虚拟环境及发布产物；.dockerignore 未排除 uploads，因此构建上下文是否夹带运行数据需发布前核对。源码有默认安全配置和首次种子账号，禁止将默认值作为长期生产配置。HTTPS/反向代理是部署需落实的条件，仓库未提供完整代理或证书配置。

容器禁用 Uvicorn 访问日志，默认 access logger 也有认证查询脱敏；仍须核验实际 Nginx、CDN/WAF/APM 不记录授权/退出参数。生产 Cookie 使用 Secure/HttpOnly、无跨域 Domain，本地 HTTP 例外只允许 loopback；不要将生产公开来源配置复制到 8010。

知识库初始化未执行运行操作；2026-10-13 SSO 实施在后续授权范围内已验证本地库并更新 8010，未安装依赖、操作生产迁移/部署或提交推送。

## 财务增强配置

Compose传递FIFO_FINANCIAL_CONTROLS_ENABLED，默认0。8张新表、开关启用/回滚、启动副作用与隔离库加表流程统一见[运行步骤](../../PROJECT_RUNBOOK.md#财务增强结构与启用)；本次未执行目标库或生产变更。已有财务记录后关闭开关会拒绝启动，不能用开关回退财务口径。
