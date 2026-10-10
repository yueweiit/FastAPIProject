# 认证与用户模块

## 功能与角色

提供账号密码登录、JWT、当前用户、自助改密、管理员建号/改角色/重置密码/删除用户，以及非管理员的单店铺绑定。依据：[routers/auth.py](../../../routers/auth.py)、[auth.py](../../../auth.py)。

admin 管理账号；operator 和 viewer 无用户管理权限。新增用户默认 operator，用户名去空白后检查唯一；请求密码最低三位。角色只接受 admin/operator/viewer。用户表完全为空时才初始化种子账号，文档不保存任何初始密码。

## 核心规则与影响

管理员不能绑定店铺、不能修改自己的角色或删除自己；提升目标用户为 admin 时清空 store_id。绑定目标必须存在且启用，可用 null 解绑。并未实现完整租户系统，权限例外及库存共享见[全局权限文档](../../knowledge/authentication.md)。

认证每次按令牌 sub 查数据库，使用当前角色判断。改密后旧令牌不会因密码哈希改变自动失效；没有服务端退出/刷新流程。删除用户涉及批次、销售、导入、快照和损益审计外键，不可假设只删一行即可成功。

依据：[routers/auth.py](../../../routers/auth.py) 的 create_user、update_user_store、update_user_role、delete_user；[models.py](../../../models.py) 的 User。

## 文档入口

- [业务流程](workflow.md)
- [代码映射](code-map.md)
- [数据库字段](database.md)
- [模块 API](api.md)
- [知识库总索引](../../knowledge/README.md)
