# FastAPIProject 通用部署手册

> 用途：供后续线程部署任意新版本、验证服务和排查常见问题。
>
> 维护规则：部署方式、端口、容器名或验证步骤发生变化时，先更新本文；本文不记录数据库密码、服务器登录密码或其他敏感凭据。

## 1. 部署前填写项

每次部署前，先确认以下信息。没有确认时不要猜测，也不要直接覆盖生产目录。

| 项目 | 本次部署值 |
| --- | --- |
| 服务器地址 | `<SERVER_HOST>` |
| 项目目录 | `<SERVER_PROJECT_DIR>` |
| Compose 服务 | `<COMPOSE_SERVICE>` |
| 容器名称 | `<CONTAINER_NAME>` |
| 外部端口 | `<PUBLIC_PORT>` |
| 容器端口 | `<CONTAINER_PORT>` |
| 部署包路径 | `<PACKAGE_PATH>` |
| 部署包 SHA256 | `<PACKAGE_SHA256>` |

本项目当前示例值如下，仅用于说明格式；每次部署仍以服务器实际配置为准：

- 项目：`FastAPIProject`
- 服务器：`8.135.70.130`
- 项目目录：`/www/wwwroot/FastAPIProject`
- Compose 服务：`web`
- 容器名称：`fifo-inventory`
- 端口映射：`8006 -> 8000`

## 2. 项目和运行环境

- 应用：FastAPI + SQLAlchemy + MySQL
- 启动入口：`main.py`
- 页面模板：`templates/index.html`
- 数据持久化目录：`uploads/`
- 生产环境配置：项目根目录 `.env`
- 页面地址：`http://<SERVER_HOST>:<PUBLIC_PORT>/`
- API 文档：`http://<SERVER_HOST>:<PUBLIC_PORT>/docs`

Docker 容器中的 `localhost` 指向容器自身，不等于数据库服务器。服务器部署前必须确认 `.env` 中的 `MYSQL_HOST` 是容器可以访问的地址。

## 3. 发布包准备

发布前确认：

- 目标版本已经在本地完成必要测试。
- 发布包来自正确的提交或构建目录。
- 发布包不包含本地 `.env`、密码、私钥或不应上传的测试数据。
- 已记录发布包 SHA256，供服务器上传后核对。
- 已确认是否需要数据库迁移；没有明确要求时不要执行迁移。

本地计算发布包校验值：

```bash
sha256sum <PACKAGE_PATH>
```

Windows PowerShell 可使用：

```powershell
Get-FileHash -Algorithm SHA256 <PACKAGE_PATH>
```

## 4. 部署前检查

登录服务器后进入项目目录：

```bash
cd <SERVER_PROJECT_DIR>
```

检查关键文件和 Compose 服务：

```bash
ls -lh
docker compose ps
```

确认以下内容不会被覆盖：

- 服务器现有 `.env`
- 服务器 `uploads/` 目录
- 正在使用的数据库配置

默认只更新应用代码和前端模板，不重新创建数据库，不执行删除数据操作。若版本包含数据库迁移，必须先备份并确认迁移脚本、目标库和回滚方案。

## 5. 上传和解压部署包

部署包应先上传到服务器项目目录。上传完成后，先确认文件名和大小：

```bash
ls -lh <SERVER_PROJECT_DIR>/<PACKAGE_NAME>
sha256sum <SERVER_PROJECT_DIR>/<PACKAGE_NAME>
```

解压或同步前必须确认包来源正确，并保留服务器现有 `.env` 和 `uploads/`。不要用整个压缩包覆盖这两个生产数据位置。不要使用 `docker compose down` 作为普通部署步骤，避免产生不必要的停机和容器状态变化。

## 6. 构建和重建容器

默认先使用普通缓存构建：

```bash
docker compose build <COMPOSE_SERVICE>
```

只有依赖缓存异常、构建内容不确定或明确要求时才使用无缓存构建：

```bash
docker compose build --no-cache <COMPOSE_SERVICE>
```

构建期间会安装 `requirements.txt` 中的依赖，下载时间可能较长。没有明确报错时不要按 `Ctrl+C`。

构建成功后，重建并后台启动服务：

```bash
docker compose up -d --force-recreate <COMPOSE_SERVICE>
```

检查服务状态：

```bash
docker compose ps
```

预期 `web` 服务和 `fifo-inventory` 容器处于运行状态。

## 7. 部署验证

### 7.1 检查应用状态

```bash
docker compose ps
docker compose logs --tail=200 <COMPOSE_SERVICE>
```

确认服务处于运行状态，且日志没有数据库连接失败、反复重启、端口监听失败或 Python 导入错误。

### 7.2 检查前端模板版本

```bash
sha256sum templates/index.html
```

将结果与本次发布包解压后的预期哈希比较。不要长期使用某个历史版本的固定哈希作为通用判断。

### 7.3 检查页面是否加载

```bash
curl -fsS http://127.0.0.1:<PUBLIC_PORT>/ && echo 'page loaded'
```

### 7.4 浏览器验证

部署完成后在浏览器执行强制刷新：

```text
Ctrl+F5
```

然后检查本次改动涉及的页面、接口、权限、数据列表、导出功能和图片是否正常。

## 8. 数据库查看和迁移

应用通过 `.env` 获取以下配置：

```text
MYSQL_HOST
MYSQL_PORT
MYSQL_USER
MYSQL_PASSWORD
MYSQL_DATABASE
```

服务器上只查看配置名称，不要把密码复制到日志或聊天窗口：

```bash
cd /www/wwwroot/FastAPIProject
grep -E '^(MYSQL_HOST|MYSQL_PORT|MYSQL_USER|MYSQL_DATABASE)=' .env
```

批次表是 `inventory_batches`。在 DataGrip 中只读查询批次数量：

```sql
SELECT COUNT(*) AS batch_count
FROM inventory_batches;
```

查看全部业务表的数量：

```sql
SELECT table_name AS table_name, table_rows AS estimated_rows
FROM information_schema.tables
WHERE table_schema = DATABASE()
ORDER BY table_name;
```

### 8.1 只读检查

优先使用 DataGrip 的只读控制台，先确认数据库和表，再执行查询。示例：

```sql
SELECT DATABASE();
SHOW TABLES;
```

### 8.2 数据库迁移

如版本包含迁移，执行前必须完成：

1. 确认迁移脚本只新增或预期修改字段。
2. 备份目标数据库。
3. 记录迁移前的表结构和数据量。
4. 在本地或测试库验证迁移。
5. 服务器执行后检查表结构和应用启动日志。

没有明确迁移要求时，不要手动改生产数据库结构。

## 9. DataGrip 连接服务器数据库

服务器应用使用的数据库主机可能是内网地址，例如 `172.19.49.226`。本机通常不能直接访问该地址，因此推荐使用 SSH 隧道。

### 常规页

- 主机：填写 `.env` 中的 `MYSQL_HOST`，例如 `172.19.49.226`
- 端口：填写 `.env` 中的 `MYSQL_PORT`，通常是 `3306`
- 用户：填写 `.env` 中的 `MYSQL_USER`
- 密码：填写 `.env` 中的 `MYSQL_PASSWORD`
- 数据库：填写 `.env` 中的 `MYSQL_DATABASE`

### SSH/SSL 页

勾选“使用 SSH 隧道”，新建 SSH 配置：

- SSH 主机：`8.135.70.130`
- SSH 端口：`22`
- SSH 用户：服务器实际登录用户，通常为 `root`
- 认证：服务器密码或 SSH 私钥
- 本地端口：保持动态

数据库主机不要擅自改成 `8.135.70.130`。SSH 隧道只负责转发连接，MySQL 主机仍以服务器 `.env` 为准。

如果 SSH 测试提示“无法连接到远程主机”，先在本机确认服务器 SSH 是否可达，并检查阿里云安全组和服务器防火墙的 `22` 端口；不要直接开放 MySQL `3306` 到公网。

## 10. 常见错误和处理

### 10.1 `pg_dump: command not found`

本项目使用 MySQL，不使用 `pg_dump`。不要用 PostgreSQL 备份命令。应根据服务器实际配置使用 MySQL 备份工具，并先确认目标库和权限。

### 10.2 `tar` 多行粘贴失败

反斜杠续行命令必须一次性完整粘贴。若终端把每一行单独执行，会出现 `--exclude: command not found`。部署时优先使用面板上传和解压，或使用完整单行命令。

### 10.3 `docker compose build` 很久没有输出

构建阶段下载 Python 依赖可能耗时较长。只要没有明确报错，不要按 `Ctrl+C`；等待出现构建成功后再执行 `docker compose up -d --force-recreate`。

### 10.4 页面仍显示旧版本

按以下顺序检查：

1. `sha256sum templates/index.html`
2. `docker compose ps`
3. 使用当前服务端口执行页面检查，例如 `curl -fsS http://127.0.0.1:<PUBLIC_PORT>/`
4. 浏览器执行 `Ctrl+F5`

## 11. 禁止事项

- 未经明确要求，不上传 GitHub。
- 未经确认，不删除数据库数据、Docker 卷或 `uploads/`。
- 不执行 `docker compose down` 作为普通重启手段。
- 不把 `.env` 密码、root 密码或数据库密码写入文档、日志或截图。
- 不把数据库 `3306` 端口直接暴露到公网，优先使用 SSH 隧道。
- 不使用旧部署包覆盖服务器。

## 12. 标准后续操作顺序

```text
确认版本和部署包 -> 计算并核对 SHA256 -> 保留 .env/uploads
-> docker compose build <service>
-> docker compose up -d --force-recreate <service>
-> docker compose ps
-> 校验关键文件和页面
-> curl 页面检查
-> 查看日志
-> 浏览器 Ctrl+F5 验证

## 13. GitHub 部署方式

项目可以使用 GitHub 私有仓库作为代码源。仓库中只提交应用代码、Docker 配置和说明文档，不提交服务器 `.env`、`uploads/`、`output/`、`.deploy/`、虚拟环境或任何压缩发布包。首次准备仓库时执行：

本项目当前私有仓库：`https://github.com/2932543558/FastAPIProject`，默认分支为 `main`。

```bash
git init
git add .
git commit -m "Initial FastAPI inventory application"
gh repo create <GITHUB_OWNER>/FastAPIProject --private --source=. --remote=origin --push
```

服务器首次部署（服务器需已安装 Git，并具有该私有仓库的只读 SSH 或 HTTPS 访问权限）：

```bash
cd /www/wwwroot
git clone git@github.com:<GITHUB_OWNER>/FastAPIProject.git FastAPIProject
cd FastAPIProject
# 将服务器原有 .env 和 uploads/ 放回此目录，不从仓库复制
docker compose build web
docker compose up -d --force-recreate web
```

服务器后续更新：先备份数据库和确认当前 `.env`、`uploads/`，然后执行：

```bash
cd /www/wwwroot/FastAPIProject
git pull --ff-only origin main
docker compose build web
docker compose up -d --force-recreate web
docker compose ps
docker compose logs --tail=200 web
curl -fsS http://127.0.0.1:8006/ && echo 'page loaded'
```

如果仓库默认分支不是 `main`，将命令中的 `main` 换成实际分支。不要在服务器执行 `git clean -fdx`，它可能删除 `.env`、上传图片或其他运行数据；不要用 `docker compose down` 作为普通更新步骤。私有仓库访问建议使用服务器专用 Deploy Key（只读），不要把个人 GitHub Token 写入项目文件或命令历史。
```
