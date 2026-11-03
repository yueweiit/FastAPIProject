# 扩展业务操作审计表

模型为[models.py](../../../models.py)的BusinessOperationLog，对应business_operation_logs，InnoDB/utf8mb4，无业务外键、无删除级联。不回填启用前历史。查询中心其余来源沿用原表，不复制历史。

| 字段 | 类型 / 规则 |
| --- | --- |
| id | INT自增主键 |
| module / action | VARCHAR(32)，稳定模块/操作码 |
| entity_type / entity_id | VARCHAR(64)表编码 / INT对象ID；删除后保留 |
| entity_label | VARCHAR(512)，当时的对象标识摘要；完整值仍保留在前后快照 |
| product_id / store_id | INT可空，关联线索/权限范围；不用外键，店铺删除不抹除历史 |
| actor_id / actor_username / actor_role | INT / VARCHAR(64) / VARCHAR(16)，操作人快照，不记录认证凭据 |
| group_id | VARCHAR(32)，一次请求上下文的UUID十六进制；批量对象及同请求多次提交共享，用于关联查询 |
| before_values / after_values | JSON，模型业务字段白名单；Decimal保存精确字符串，日期ISO；创建前/删除后为{} |
| unknown_before_fields | JSON数组，未加载、无法确认的旧字段，不误写为已知NULL |
| recorded_at | DATETIME，应用UTC时间，不含时区偏移 |

索引为(recorded_at,id)、(module,store_id,recorded_at,id)、(entity_type,entity_id,id)、group_id。无日志编辑/删除API；这是应用追加式留痕，数据库管理账号仍能修改，不宣称防篡改账本或永久存储制度。

## 结构准备及开关

1. 离线生成：`python migrations/add_business_operation_logs.py --write-sql output/business_operation_logs.sql`。只生成这一张表及索引，不连接数据库。
2. 核实目标库、现有表结构及DDL权限，按授权审核执行。MySQL DDL不依赖事务回滚；不要用启动main代替预检（原启动另有迁移、修复和快照写入）。
3. 配置`FIFO_OPERATION_AUDIT_ENABLED=1`并重启相应本地服务后才捕获新增操作。默认0；关闭时写入口保持原行为，若表存在则继续提供只读历史。不要删除表或旧日志作为回退。
4. 开启前同一环境全部业务写进程需更新，旧进程或关闭开关的时段不会留痕。不能把更新代码或生成SQL描述为真实库已建表/已生效。

2026-11-02已按用户授权仅在本机127.0.0.1:3309 / inventory_fifo_test建表并核验：16列、4个二级索引、0外键，InnoDB/utf8mb4，0条初始记录；本地.env开关已设为1。未回填或改变既有业务记录，未操作生产。8010未重启，须由用户手动重启后生效；真实浏览器写入链路及其他环境状态仍待验证。完整运行边界见[运行步骤](../../../PROJECT_RUNBOOK.md#扩展业务操作留痕结构与启用)。
