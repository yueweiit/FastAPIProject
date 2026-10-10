# 认证模块代码映射

| 页面/能力 | 前端函数 | API/后端函数 | 数据 |
| --- | --- | --- | --- |
| 登录/退出 | doLogin、doLogout、showLogin、api | login；get_current_user | users |
| 管理员建号 | adminCreateUser | create_user | users |
| 用户列表 | loadUsers | list_users；list_stores | users、stores |
| 店铺绑定 | bindUserStore | update_user_store | users.store_id |
| 角色修改 | changeRole | update_user_role | users.role、store_id |
| 重置密码 | resetPwd | reset_user_password | users.password_hash |
| 自助改密 | 当前模板未发现自助入口，API 已存在 | change_my_password | users.password_hash |
| 删除用户 | deleteUser | delete_user | users 及其历史关联 |
| 当前用户 | 登录恢复及销售店铺选择 | me | users |

页面函数见 [templates/index.html](../../../templates/index.html)；后端处理见 [routers/auth.py](../../../routers/auth.py)。认证/角色依赖、密码哈希、令牌和初始化在 [auth.py](../../../auth.py)，请求契约分别在 [schemas.py](../../../schemas.py) 和认证路由内的请求类。

测试定位：[test_user_creation_permissions.py](../../../tests/test_user_creation_permissions.py) 检查管理权限与无公开注册；[test_store_data_isolation.py](../../../tests/test_store_data_isolation.py) 检查敏感读取的店铺范围。用户绑定变化还影响旧销售兼容查询及库存报表，修改时联读[销售代码映射](../sales/code-map.md)和[报表流程](../reports/workflow.md)。
