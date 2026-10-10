# 认证与用户 API

前缀 /auth，共 9 个端点。数据访问由 Depends(get_db) 提供；权限列为入口依赖，店铺和自操作限制另见[流程](workflow.md)。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /auth/login` | [login](../../../routers/auth.py) | 公开 | LoginRequest | LoginResponse |
| `POST /auth/users` | [create_user](../../../routers/auth.py) | admin | CreateUserRequest | UserResponse |
| `GET /auth/users` | [list_users](../../../routers/auth.py) | admin | 无 | list[UserResponse] |
| `PUT /auth/users/{user_id}/store` | [update_user_store](../../../routers/auth.py) | admin | user_id、UpdateUserStoreRequest | ok、store_id、store_name |
| `PUT /auth/users/{user_id}/role` | [update_user_role](../../../routers/auth.py) | admin | user_id、UpdateRoleRequest | ok |
| `PUT /auth/users/{user_id}/password` | [reset_user_password](../../../routers/auth.py) | admin | user_id、PasswordRequest | ok |
| `PUT /auth/me/password` | [change_my_password](../../../routers/auth.py) | 已认证用户 | PasswordRequest | ok |
| `DELETE /auth/users/{user_id}` | [delete_user](../../../routers/auth.py) | admin | user_id | ok |
| `GET /auth/me` | [me](../../../routers/auth.py) | 已认证用户 | 无 | 当前用户字典 |

## 请求契约

- LoginRequest：username、password；成功返回 token、user_id、username、role，不返回刷新令牌。
- CreateUserRequest：username、password、role（默认 operator）；用户名不能为空、不能重复，角色合法，密码至少三位。
- UpdateUserStoreRequest：store_id，可空解绑；仅非管理员绑定有效店铺。
- UpdateRoleRequest：role；禁止改自己，提升 admin 清空店铺。
- PasswordRequest：new_password、可选 old_password；自助改密实际需要正确 old_password，管理员重置不使用它。
- me 返回 id、username、role、store_id；用户列表 UserResponse 还含 store_name、created_at。

以上请求类分别在 [schemas.py](../../../schemas.py) 和 [routers/auth.py](../../../routers/auth.py)。业务错误常见 400、403、404，登录失败 401；缺少字段由框架校验。无 /auth/register、服务端 logout 或 refresh 接口。初始化账号不是公开 API。
