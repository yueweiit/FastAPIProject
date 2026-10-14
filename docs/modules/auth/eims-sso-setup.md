# EIMS 接入配置与验收

适用版本：2026-10-13 的 Cookie 会话与 EIMS 授权码实现。代码已完成、本地验证通过；**正式客户端、账号绑定和真实 EIMS 浏览器联调尚未完成，生产未发布**。协议依据与责任边界见[接入方案](../../plans/2026-10-13-eims-sso-integration.md)，源码见 [eims_sso.py](../../../services/eims_sso.py)、[eims_auth.py](../../../routers/eims_auth.py)。

## 1. 提交给 EIMS 的注册资料

| 项目 | FIFO 的值 |
| --- | --- |
| 系统首页 | `https://fifo.yueweiportal.com/` |
| 由 FIFO 发起 SSO 的入口 | `https://fifo.yueweiportal.com/auth/eims/start` |
| 登录回调 | `https://fifo.yueweiportal.com/auth/eims/callback` |
| 退出回调 | `https://fifo.yueweiportal.com/auth/eims/logout/callback` |
| issuer | `https://eims.yueweiportal.com`，无末尾斜杠 |
| 协议 | Authorization Code，PKCE S256，ID Token RS256 |
| scope | `openid profile`；当前不需要 email |
| 客户端认证 | `client_secret_basic`，仅服务端 |
| 本地匹配字段 | `users.id`，稳定正整数主键；app_user_id 可为整数或规范十进制字符串 |
| 本地权限字段 | `users.role`、`users.store_id`，由 FIFO 管理 |

接入指南约定两条回调均登记在客户端允许回调列表中；精确匹配协议、域名、端口、路径和斜杠。EIMS 侧注册独立 FIFO 客户端，目录选择 oauth2 并关联该客户端，设置用户入口访问策略。门户点击必须最终先进入 FIFO start 生成 state/verifier/nonce，再去 EIMS 授权；门户 oauth2 启动器是否按此方式配合 **待联调**，不能直接把自行生成的 code 回调给 FIFO。

账号绑定在 EIMS 的“系统管理 → OAuth2 账号绑定”维护。先在**正式 FIFO** 用户管理确认 ID，分别绑定普通用户和管理员。需要核对时，在已获授权的正式数据库连接执行只读 SQL：

```sql
SELECT id, username, role, store_id, is_active FROM users ORDER BY id;
```

旧版本尚无 is_active 时先只查询前四列。不要使用测试库 ID 推定生产 ID，不用 EIMS sub/用户名/邮箱代替 users.id。绑定值拒绝空值、负数、前导零、空白、浮点和布尔值；用户须存在且启用。

## 2. 后端配置

在服务器已有 `.env` **补充**以下参数，不覆盖 MySQL/OA 原配置。实际 client_secret 由 EIMS 经安全渠道交付，不能发到聊天或 Git。

```dotenv
FIFO_AUTH_MODE=hybrid
FIFO_PUBLIC_BASE_URL=https://fifo.yueweiportal.com
EIMS_ISSUER=https://eims.yueweiportal.com
EIMS_CLIENT_ID=<EIMS生成的FIFO客户端ID>
EIMS_CLIENT_SECRET=<通过安全渠道配置>
SSO_TOKEN_ENCRYPTION_KEY=<独立Fernet密钥>
JWT_SECRET=<FIFO强随机JWT密钥>
```

SSO_TOKEN_ENCRYPTION_KEY 是 urlsafe base64 编码的 32 字节 Fernet 密钥，不能随意填写普通文本，不复用数据库密码/JWT_SECRET。可在受控部署终端使用已有 cryptography 的 `Fernet.generate_key()` 生成并存入后端密钥配置；本手册不保存生成结果。密钥需要随部署密钥备份，更换后旧 SSO Token/临时事务无法解密，用户须重新登录。

源码 [docker-compose.yml](../../../docker-compose.yml) 已显式传入上述变量，JWT_SECRET 必填；[.dockerignore](../../../.dockerignore) 排除 `.env`，不能仅上传 `.env` 后期待旧容器自动生效。修改环境必须按[部署手册](../../../DEPLOYMENT_HANDOFF.md)重新创建服务；增加 httpx 依赖后需更新镜像。首次发布使用 hybrid，保留现有本地账号恢复入口，联调完成后再决定是否切 eims。

开发与生产分别配置客户端及账号绑定；本地回调若使用 `http://127.0.0.1:8010` 也须由 EIMS 单独注册允许，不能借用生产回调。只测试本地密码登录可保持 SSO 三项密钥为空，public base 留空或填本地地址。**不要在本地复制生产公开域名配置**，否则来源检查和 Secure Cookie 会与 HTTP 本地访问不匹配。公网环境必须 HTTPS；HTTP Cookie/issuer 例外仅用于字面 loopback 开发来源。

## 3. 数据与代理发布前核对

- 备份业务 MySQL 和 uploads，确认恢复方式。新结构是 users.is_active（原用户默认启用）以及 auth_login_transactions、auth_sessions 两张 InnoDB 表；启动会执行 create_all 和兼容迁移，另有原历史修复/快照写入，不能作为只读检查。
- 核实应用建表、ALTER、外键和认证事务所需权限；MySQL DDL 不可假定整体回滚。不要对生产运行 tests/mysql_auth_sessions.py，也不要手工改用户 ID、历史库存/成本。
- 检查 HTTPS 证书、FIFO 域名反向代理到现有 Docker web、可信代理和容器出站访问 EIMS。实际宝塔/Nginx 配置归属待运维核实。
- Docker Uvicorn 已使用 `--no-access-log`，本地默认 access logger 也有 `/auth/eims` 查询脱敏。反向代理、CDN/WAF/APM 仍需防止记录回调 code/state、退出导航票据及 ID Token hint。可在实际认证路径 location 关闭访问日志，或采用不含 query 的日志格式；禁止在排查中粘贴完整认证 URL/头。

上述是后续发布清单，本次没有操作生产配置、客户端注册或生产数据库。

## 4. 验收顺序

1. 发布后读取 `/auth/config`：期望 mode=hybrid、local_enabled=true、sso_available=true；此标志只检查配置完整，不证明提供方健康。先确认本地登录仍可恢复。
2. 使用已绑定普通用户从 FIFO 按钮和 EIMS 门户分别登录；FIFO 显示正确本地用户名/角色，销售及损益继续按绑定店铺限制。再验证已绑定管理员。
3. 未绑定、被停用/删除用户、错 state/nonce、回调重放不得进入账号；EIMS 管理员绑定 FIFO operator 仍保持 operator。
4. 验证 Access Token 到期后的 Refresh Token 轮换与并发请求，UserInfo 绑定变更后拒绝旧会话；在 EIMS 实际停用/撤销入口权/解绑，核实提供方是否拒绝 UserInfo/刷新，以及最终生效时间。
5. 主动退出验证 FIFO Cookie 立即失效、EIMS 会话退出及精确退出回调；API 401 只显示本地登录页。提供方不可达应拒绝当前认证请求，不能自动授予默认身份。
6. 验证写操作 CSRF、文件上传/导入/导出、当前角色及店铺变更，并检查各层认证日志未留敏感查询值。真实浏览器 Cookie/跳转行为需人工联调，离线测试不能替代。

本地已验证：完整 unittest 回归、两份页面辅助函数、真实 MySQL 单次消费/并发刷新/撤销，以及 8010 的商品与存货查询。测试入口和写入限制见[编码约定](../../knowledge/conventions.md)。当前本地未填 EIMS 客户端，按钮显示不可用是预期状态；旧页面登录缓存会清理，需要重新本地登录一次。

2026-10-14 审查修复新增10项回归，并验证真实InnoDB的两种用户锁顺序和轮换后503恢复。发布时包含此次认证修复：改密之后旧校验结果不能创建新Cookie，复核失败不回退旧Refresh Token，延迟退出回调不清新登录；没有增加迁移。当前源码与测试记录见[方案修复记录](../../plans/2026-10-13-eims-sso-integration.md#9-2026-10-14-审查问题修复)。

## 5. 恢复与已知边界

SSO 问题可用新版本 local 模式暂时关闭 EIMS 会话，保留加法型结构和业务数据；不要删除认证表、Docker 卷或清空库来恢复。若回滚旧代码，旧版不理解停用状态/新 Cookie，需核实权限风险。

local/hybrid 兼容的传统 Bearer JWT 仍无吊销版本：改密和 Cookie 退出不能使它立即失效，但当前停用/角色检查仍适用。源码有旧默认密钥/初始账号配置，生产必须显式设置并改密。Discovery 未公布 revocation endpoint，本次实现不宣称 EIMS 退出已撤销所有 Access/Refresh Token；生产的提供方行为及日志保护待验证。
