# 认证与用户 API

前缀 `/auth`，共 **16** 个端点，由 [routers/auth.py](../../../routers/auth.py)（10 个）及 [routers/eims_auth.py](../../../routers/eims_auth.py)（6 个）注册。Cookie 写请求要求 `X-CSRF-Token` 和来源校验；角色/店铺/自操作限制继续在服务端执行。无公开注册及前端 Token 刷新 API。

| 方法与路径 | 处理函数 | 权限/模式 | 输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /auth/login` | login | 公开，local/hybrid | LoginRequest | LoginResponse + 新 Cookie |
| `GET /auth/config` | auth_config | 公开 | 无 | mode、local_enabled、sso_available |
| `GET /auth/eims/start` | eims_start | hybrid/eims 且配置完整 | 无 | 303 到 EIMS，临时 Cookie |
| `GET /auth/eims/callback` | eims_callback | 有效浏览器事务 | code、state 或授权 error | 303 首页 + 新 Cookie；失败为安全错误码 |
| `POST /auth/logout` | logout | 已有 Cookie 需 CSRF | 无 | ok、同源 redirect_url；清 Cookie |
| `GET /auth/eims/logout/start` | eims_logout_start | 有效一次性票据 | ticket + 临时 Cookie | 后端 303 到 EIMS |
| `GET /auth/eims/logout/callback` | eims_logout_callback | 有效退出事务 | state + 临时 Cookie | 303 首页，清 Cookie |
| `POST /auth/users` | create_user | admin | CreateUserRequest | UserResponse |
| `GET /auth/users` | list_users | admin | 无 | list[UserResponse] |
| `PUT /auth/users/{user_id}/store` | update_user_store | admin | UpdateUserStoreRequest | ok、store_id、store_name |
| `PUT /auth/users/{user_id}/status` | update_user_status | admin，不可自停用 | is_active | ok、is_active |
| `PUT /auth/users/{user_id}/role` | update_user_role | admin，不可改自己 | UpdateRoleRequest | ok |
| `PUT /auth/users/{user_id}/password` | reset_user_password | admin | PasswordRequest | ok；撤销目标 Cookie 会话 |
| `PUT /auth/me/password` | change_my_password | 当前用户 | PasswordRequest | ok；撤销本人 Cookie 会话 |
| `DELETE /auth/users/{user_id}` | delete_user | admin，不可自删 | user_id | ok |
| `GET /auth/me` | me | 当前用户 | 无 | 当前用户和会话信息 |

## 请求与兼容契约

- LoginRequest：username、password；JSON 仍返回 token、user_id、username、role，保留传统客户端。新页面不保存或使用这个 JWT。模式 eims 即使界面被绕过也返回 403；账号停用、密码错误或密码在会话创建前变更返回 401。
- CreateUserRequest：username、password、role（默认 operator）；用户名不能为空且唯一，密码至少三位。UserResponse 增加 is_active，保留 id、username、role、store_id、store_name、created_at。
- UpdateUserStoreRequest：store_id，可空解绑；非管理员仅绑定启用店铺。
- UpdateRoleRequest：role 为 admin/operator/viewer；提升 admin 清空绑定。
- UpdateUserStatusRequest：is_active 布尔；停用在同一事务撤销该用户所有 Cookie 会话，不删除业务记录。
- PasswordRequest：new_password、可选 old_password；自助修改要求正确旧密码，管理员重置不使用它。传统 JWT 没有统一吊销机制。
- me 保留 id、username、role、store_id，新增 is_active、auth_source（local/eims/bearer）、csrf_token。Bearer 请求的 csrf_token 为 null；CSRF 不是身份令牌，不可替代 Cookie。

请求类与响应在 [schemas.py](../../../schemas.py) 和认证路由内。页面采用同源相对地址，Cookie 写请求统一通过 authHeaders 附加 CSRF，文件上传同样覆盖。

## 安全响应

认证信息响应设置 `Cache-Control: no-store`；授权跳转另设 `Referrer-Policy: no-referrer`。config 的 sso_available 表示配置完整，不代表 EIMS 实时健康或客户端已注册成功。回调不回显提供方错误、code/state/Token，失败跳首页固定 auth_error 分类。

401 表示认证/绑定失效，403 为角色/模式/CSRF 拒绝，503 为提供方、配置或网络暂不可用；其他账号校验常见 400/404、参数校验 422。503 时已收到的轮换结果保留为待复核状态，恢复后复核成功才放行。前端 API 401 仅清本地状态，不自动全局退出 EIMS。退出回调不删除后来建立的登录 Cookie，错误/重放回调也不清除新的浏览器流程。登录/退出回调会写认证事务，不能视为只读 GET。流程见 [workflow.md](workflow.md)。
