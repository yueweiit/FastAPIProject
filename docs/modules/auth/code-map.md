# 认证模块代码映射

| 能力 | 页面入口 | 后端入口/依赖 | 数据 |
| --- | --- | --- | --- |
| 登录配置及恢复 | initializeAuth、loadCurrentUser | auth_config、me、get_current_user | users、auth_sessions |
| 本地登录 | doLogin | login、verify_password、SessionStore.create_session | users、auth_sessions |
| EIMS 登录 | startEimsLogin | eims_start、eims_callback；EimsClient、SessionStore | 两张认证表、users |
| 请求认证/CSRF | api、authHeaders、直接 fetch | get_current_user、check_csrf、check_origin | auth_sessions、users |
| 过期/退出 | handleSessionExpired、doLogout | logout、eims_logout_start、eims_logout_callback | 两张认证表 |
| 用户列表/新建 | loadUsers、adminCreateUser | list_users、create_user；RequireAdmin | users、stores |
| 启停账号 | setUserActive | update_user_status、revoke_user_sessions | users、auth_sessions |
| 角色/店铺绑定 | changeRole、bindUserStore | update_user_role、update_user_store | users、stores |
| 重置密码 | resetPwd | reset_user_password | users、auth_sessions |
| 自助改密 | 当前模板没有入口，API 存在 | change_my_password | users、auth_sessions |
| 删除 | deleteUser | delete_user | users 及历史业务关联 |

页面函数以 [templates/index.html](../../../templates/index.html) 为准，根目录 [index.html](../../../index.html) 的认证功能同步，保留原平台 SKU 差异。

| 文件 | 职责 |
| --- | --- |
| [auth.py](../../../auth.py) | 密码、传统 JWT、Cookie/Bearer 鉴权、最新角色依赖、初始账号 |
| [routers/auth.py](../../../routers/auth.py) | 本地登录及用户/权限管理、me；局部请求类 |
| [routers/eims_auth.py](../../../routers/eims_auth.py) | 登录能力、授权回调、主动退出和安全错误响应 |
| [services/eims_sso.py](../../../services/eims_sso.py) | 模式/来源配置、PKCE、Fernet、Discovery/JWKS、RS256、Token/UserInfo、退出 URL |
| [services/auth_sessions.py](../../../services/auth_sessions.py) | Cookie、CSRF、哈希状态、独立事务/行锁、刷新、撤销、过期清理 |
| [services/auth_logging.py](../../../services/auth_logging.py) | Uvicorn 认证路径查询参数脱敏；容器同时禁用访问日志 |
| [models.py](../../../models.py)、[schemas.py](../../../schemas.py)、[migrate.py](../../../migrate.py) | 三张认证模型、用户响应、旧 users 启用字段兼容迁移 |
| [main.py](../../../main.py) | 第七组路由、SSO 异常处理、每小时认证清理、HTTP 客户端关闭 |

离线协议/API 测试为 [test_eims_sso.py](../../../tests/test_eims_sso.py)，页面辅助函数为 [auth_page_checks.cjs](../../../tests/auth_page_checks.cjs)；真实本地 InnoDB 并发为 [mysql_auth_sessions.py](../../../tests/mysql_auth_sessions.py)，有写入及加法型结构变更，不可默认执行。原 [管理权限](../../../tests/test_user_creation_permissions.py)、[商品权限](../../../tests/test_product_feature_permissions.py)、[店铺隔离](../../../tests/test_store_data_isolation.py) 继续回归。

绑定变化影响旧销售兼容查询和库存归属；改权限时联读[销售代码映射](../sales/code-map.md)及[报表流程](../reports/workflow.md)，不将 EIMS 角色替代本地角色。
