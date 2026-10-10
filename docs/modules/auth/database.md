# 认证模块数据表

字段字典依据 ORM 静态声明；默认值需区分 Python 默认和数据库默认，真实数据库结构待验证。全局关系见[数据库总览](../../knowledge/database.md)。

## users

来源：[models.py](../../../models.py) 的 `User`（源码第 22 行，行号可能随版本变化）。

| 字段 | ORM 类型 | 可空 | 含义 | 约束与默认 |
| --- | --- | --- | --- | --- |
| `id` | `Integer` | 否 | 自增主键 | 主键 |
| `username` | `String(64)` | 否 | 登录名 | 唯一；索引 |
| `password_hash` | `String(255)` | 否 | 密码哈希；不保存明文 | — |
| `role` | `String(16)` | 否 | 当前角色 | Python 默认：'operator' |
| `store_id` | `Integer` | 是 | 店铺关联；具体归属见模块规则 | 索引；ForeignKey('stores.id') |
| `created_at` | `DateTime` | 否 | 创建时间 | 数据库默认：func.now() |

## 关联与限制

users.store_id 为可空 stores 外键，只表示账号当前店铺。users.id 被批次、销售、文件导入及撤销人、期间关闭人和损益更新人引用；User.batches、User.sales 也有 ORM relationship，不存在统一历史记录转移或安全级联删除策略。

username 唯一并建索引；role 是普通 String，没有数据库枚举约束，合法值由认证路由检查。密码只保存在 password_hash，不在文档或日志复制哈希内容。依据：[models.py](../../../models.py)、[routers/auth.py](../../../routers/auth.py)。
