# 认证模块数据表

依据 [models.py](../../../models.py) 的 User、AuthLoginTransaction、AuthSession。2026-10-13 已在独立本地 MySQL 测试库验证新增列及两张 InnoDB 表，生产结构仍待核实。全局关系见[数据库总览](../../knowledge/database.md)。

## users

| 字段 | ORM 类型 | 可空 | 含义/约束 |
| --- | --- | --- | --- |
| id | Integer | 否 | 自增主键；EIMS app_user_id 必须精确对应 |
| username | String(64) | 否 | 唯一索引；本地登录名 |
| password_hash | String(255) | 否 | bcrypt 哈希，不复制实际内容 |
| role | String(16) | 否 | Python 默认 operator；合法值由路由校验 |
| is_active | Boolean | 否 | Python/数据库默认 true；旧库兼容迁移默认启用 |
| store_id | Integer | 是 | 当前店铺，索引，外键 stores.id |
| created_at | DateTime | 否 | 数据库 func.now() |

停用不删除业务行。users.id 仍被批次、销售、导入/撤销、关账及损益更新人引用；原历史关系不能假设可安全级联。auth_sessions.user_id 新外键为 ON DELETE CASCADE，但不解决其他业务外键限制。

## auth_login_transactions

| 字段 | ORM 类型 | 可空 | 含义/约束 |
| --- | --- | --- | --- |
| state_hash | String(64) | 否 | 主键，SHA256；原 state/ticket 不保存明文 |
| flow | String(16) | 否 | login、logout、logout_redirect |
| browser_hash | String(64) | 否 | 临时 Cookie 的 SHA256，用于浏览器绑定 |
| payload | Text | 是 | Fernet 加密 verifier/nonce 或退出导航数据；消费后置空 |
| expires_at | DateTime | 否 | 索引；创建起 5 分钟 |
| consumed_at | DateTime | 是 | 一次性消费时间 |
| created_at | DateTime | 否 | 创建时间 |

表指定 InnoDB。consume_transaction 在独立事务中用行锁校验类型、有效期、浏览器和消费状态；消费成功只一次，不以进程内字典承担正确性。

## auth_sessions

| 字段 | ORM 类型 | 可空 | 含义/约束 |
| --- | --- | --- | --- |
| session_hash | String(64) | 否 | 主键，随机会话 Cookie 的 SHA256 |
| user_id | Integer | 否 | 索引；外键 users.id，ON DELETE CASCADE |
| source | String(16) | 否 | local / eims |
| csrf_token | String(64) | 否 | 独立随机 CSRF 值；只随 me 发给当前会话，不是认证凭据 |
| issuer | String(255) | 是 | EIMS 来源，会话与配置必须一致 |
| client_id | String(255) | 是 | 创建会话时的 OAuth 客户端标识 |
| subject | String(255) | 是 | ID Token / UserInfo 的一致 sub |
| oidc_nonce | String(128) | 是 | 首次登录 nonce，用于后续 ID Token 复核 |
| token_payload | Text | 是 | Fernet 加密 Token JSON及内部 `_id_token_pending` 待验签标记；撤销时清空 |
| token_expires_at | DateTime | 是 | Access Token 由 expires_in 得到的到期时间 |
| validated_at | DateTime | 是 | 最近成功复核时间；EIMS 会话为空时须重新复核，禁止使用读缓存 |
| expires_at | DateTime | 否 | 索引；会话绝对期限 24 小时 |
| revoked_at | DateTime | 是 | 撤销时间；不恢复已撤销会话 |
| created_at | DateTime | 否 | 创建时间 |

表指定 InnoDB；同一会话刷新用行锁串行替换加密 Token。已收到轮换结果后，JWKS/UserInfo 暂不可用仍保存新 Token 和待复核状态，503 不授权；错误身份永久撤销。local 会话不存提供方身份/Token，不需要 SSO 加密密钥。会话创建与密码/状态写入统一先锁 User 再操作 AuthSession，旧密码哈希不存入会话；详见[流程](workflow.md)。2026-10-14 修复仅使用既有字段，没有新增迁移。

## 时间、迁移与清理

两张认证表的时间由应用显式写入 **UTC 无时区 DateTime**，不改变财务业务日期口径。每小时清理已过期认证事务/会话；本次没有引入完整登录审计流水。

database.init_db/create_all 创建缺失认证表，migrate.MIGRATIONS 给已有 users 补 is_active；MySQL DDL 不能整体回滚，部署需先备份并核实 CREATE/ALTER 权限。不直接修改历史用户 ID，也不把测试库 ID 用于生产绑定。加密密钥只从后端环境读取，不写 Git/文档；更换后旧 SSO 加密数据无法解密，需重新登录。

原子消费、刷新和撤销见 [auth_sessions.py](../../../services/auth_sessions.py)，启停/改密事务撤销见 [routers/auth.py](../../../routers/auth.py)。验证脚本 [mysql_auth_sessions.py](../../../tests/mysql_auth_sessions.py) 会在严格限定本地库添加结构、临时写入并清理自建认证记录，不可用于生产。
