# 认证与用户模块

## 功能与职责

提供本地密码登录、EIMS 统一登录、可撤销的浏览器会话、当前用户、主动退出、自助改密，以及管理员建号、角色/店铺绑定、启停、重置密码和删除用户。入口：[routers/auth.py](../../../routers/auth.py)、[routers/eims_auth.py](../../../routers/eims_auth.py)；协议与会话实现：[eims_sso.py](../../../services/eims_sso.py)、[auth_sessions.py](../../../services/auth_sessions.py)。

EIMS 负责认证和门户访问策略，FIFO 使用 UserInfo 的 `app_user_id` 查本地 `users.id`，继续按当前 `role`、`store_id` 授权。没有绑定、账号不存在或停用均拒绝，不按用户名、邮箱或 EIMS 角色兜底，不自动创建账号或提升权限。

## 模式与业务规则

| FIFO_AUTH_MODE | 本地密码/传统 Bearer | EIMS 会话 |
| --- | --- | --- |
| local | 开放 | 拒绝；关闭 SSO 入口 |
| hybrid（缺省） | 开放 | 配置完整时开放 |
| eims | 服务端拒绝 | 仅统一登录；配置不全时保持不可用 |

浏览器登录后使用 HttpOnly Cookie；页面不保存 JWT，刷新通过 `/auth/me` 恢复。Cookie 写请求需要 CSRF 和来源校验。local/hybrid 下无 Cookie 的既有客户端继续使用 24 小时 HS256 Bearer；Cookie 无效时不会回退到请求中的其他 Bearer 身份。依据：[auth.py](../../../auth.py)、[模板](../../../templates/index.html)。

admin 管理账号，operator/viewer 无用户管理权限。新增用户默认 operator、启用；用户名去空白且唯一，密码最低三位；合法角色为 admin/operator/viewer。空用户表才初始化种子账号，文档不保存初始密码。管理员不得修改自己的角色、删除或停用自己，不能绑定店铺；提升为 admin 清空店铺。非管理员可绑定一个启用店铺或解绑。

每次鉴权重新读取本地账号及角色，停用拒绝所有认证方式并撤销 Cookie 会话；改密/重置密码撤销该用户 Cookie 会话。保留的传统 JWT 没有黑名单或版本机制，改密和 `/auth/logout` 不吊销其已签发令牌，最多持续到原到期；重新启用账号也不会使旧 Cookie 恢复。删除用户仍可能受历史业务外键限制，优先使用停用。权限和库存共享例外见[权限矩阵](../../knowledge/authentication.md)。

2026-10-14 修复改密与会话创建竞态、轮换 Token 后的临时故障恢复，以及延迟退出回调对新登录的影响。使用既有 User/AuthSession 锁和加密 payload，不增加表/字段或改变 API；具体锁序、待复核状态和退出边界见[流程](workflow.md)。

## 维护入口

- [workflow.md](workflow.md)：登录、刷新、退出和用户状态。
- [code-map.md](code-map.md)：页面、路由、服务和测试映射。
- [database.md](database.md)：用户及两张认证表。
- [api.md](api.md)：16 个认证端点和兼容契约。
- [eims-sso-setup.md](eims-sso-setup.md)：配置、绑定、发布与验收步骤。
- [接入方案与实施记录](../../plans/2026-10-13-eims-sso-integration.md)：责任边界、设计决策和待联调事项。
- [知识库总索引](../../knowledge/README.md)。

2026-10-13 已完成本地代码及模拟协议、真实本地 MySQL 验证；正式 EIMS 客户端和账号绑定未配置，真实授权/刷新/退出、生产发布待验证。
