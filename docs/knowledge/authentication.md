# 登录、权限和店铺隔离

## 登录、会话与 EIMS

[auth.py](../../auth.py) 使用 bcrypt 哈希；浏览器改为服务端可撤销的 24 小时不透明 Cookie 会话，页面不持久化 JWT，`/auth/me` 恢复当前身份并提供独立 CSRF。Cookie 写请求校验 CSRF 和来源，生产使用 HttpOnly/Secure/SameSite=Lax、Path=/、无 Domain 的 `__Host-` Cookie。每次鉴权读取当前启用 User 与本地角色/店铺。

`FIFO_AUTH_MODE` 支持 local/hybrid/eims，缺省 hybrid 保留本地登录；eims 在服务端禁用本地密码及传统 Bearer。local/hybrid 下无 Cookie 的既有客户端仍可用 HS256 JWT（sub、role、exp，24 小时），角色载荷不替代数据库角色；有 Cookie 时拒绝失败后回退另一 Bearer 身份。建号和账号管理仍依赖 RequireAdmin，无公开注册。改密/停用撤销 Cookie 会话；传统 JWT 无吊销版本，改密/主动 Cookie 退出不吊销已签发 JWT。

EIMS 采用授权码 + PKCE S256，RS256 ID Token 验签并校验 issuer/audience/nonce/时间；UserInfo sub 必须一致，app_user_id 严格对应本地 users.id，不推定远端角色、不自动开户。提供方 Token 加密保存后端，读复核最多缓存 60 秒、写即时复核，行锁串行刷新。主动退出撤销本地会话再走提供方退出；API 401 只清页面状态。源码：[eims_sso.py](../../services/eims_sso.py)、[auth_sessions.py](../../services/auth_sessions.py)、[eims_auth.py](../../routers/eims_auth.py)。

2026-10-14 并发修复：登录签发会话与密码/启停更新统一 User → AuthSession 锁序，并复查校验过的密码；已收到的轮换 Token 在临时故障时保留待复核状态，不恢复成旧 Refresh Token，也不跳过验签/绑定；延迟退出回调只清流程状态，不删除新登录 Cookie。原角色/店铺和传统 JWT 兼容边界保留，详见[认证流程](../modules/auth/workflow.md)。

首页、静态图片和默认接口文档仍公开；缺失/无效认证由 get_current_user 返回 401，CSRF 拒绝为 403，提供方暂不可达为 503。EIMS 账号停用、门户撤权/解绑的实际提供方生效行为待真实联调。配置与验收见[接入说明](../modules/auth/eims-sso-setup.md)。

## 实际权限矩阵

“当前店铺”指非管理员 users.store_id；未绑定时敏感销售/损益操作会拒绝。下表以实际依赖和业务判断为准，不能把 viewer 默认理解为所有功能只读。

| 功能 | admin | operator | viewer |
| --- | --- | --- | --- |
| 用户管理、启停、角色、店铺绑定 | 可 | 不可 | 不可 |
| 修改自己的密码 | 可 | 可 | 可 |
| 商品创建/修改/删除/导出 | 可 | 可 | 可 |
| 平台 SKU 映射创建/修改/删除 | 可 | 可 | 可 |
| 店铺、产品线、店铺 SKU 写入 | 可 | 不可 | 不可 |
| 财务主数据列表 | 共享读取 | 共享读取 | 共享读取 |
| 批次创建 | 可 | 可 | 不可 |
| 批次修改/删除 | 任意未消耗批次 | 自己创建的未消耗批次 | 不可 |
| 批次列表和导出、商品库存汇总 | 共享读取 | 共享读取 | 共享读取 |
| 手工销售 | 指定有效店铺 | 绑定的有效店铺 | 不可 |
| 文件导入 | 可，源码未要求绑定店铺 | 绑定的有效店铺 | 不可 |
| 销售列表/详情、待确认、导入历史 | 全部 | 当前店铺 | 当前店铺 |
| 导入撤销 | 全部已完成导入 | 当前店铺的已完成导入 | 不可 |
| 店铺损益查询/保存 | 任意店铺，可保存 | 当前店铺，可保存 | 当前店铺，只读 |
| 产品线毛利 | 可汇总启用店铺 | 当前店铺 | 当前店铺 |
| 独立存货跌价查询/导出 | 全局 | 全局 | 全局 |
| 财务附件 | 五张，含跨店损益与存货SKU汇总 | 当前店铺四张，无跨店损益汇总 | 当前店铺四张，无跨店损益汇总 |

来源：[认证路由](../../routers/auth.py)、[商品](../../routers/products.py)、[批次](../../routers/batches.py)、[销售](../../routers/sales.py)、[主数据](../../routers/finance_masters.py)、[报表](../../routers/reports.py)。共享商品写权限和按店铺敏感数据过滤还由 [权限测试](../../tests/test_product_feature_permissions.py)、[店铺隔离测试](../../tests/test_store_data_isolation.py)记录。

## 店铺与租户机制

不存在统一 Tenant 模型、tenant_id、按租户建库或统一查询拦截。当前实现是“共享商品/库存 + 具体接口按店铺限制敏感数据”。

管理员不能绑定店铺；非管理员可以绑定一个有效店铺，也能由管理员解绑。提升为 admin 会清空绑定。新手工销售保存所选/绑定店铺，运营导入同时固化 sales、settlement_entries、sales_import_batches 的 store_id；随后改绑不改变这些新记录的店铺。

旧销售 store_id 为空时，权限查询会通过销售创建人当前 store_id 兼容判断，展示还可能通过有效店铺 SKU 映射推断；创建人改绑可能影响旧记录。批次没有固定店铺字段，库存报表使用批次创建人当前绑定推导；FIFO 不按店铺锁定库存。平台映射也不包含 store_id。依据：[销售范围辅助函数](../../routers/sales.py) 的 _sale_store_scope、_sale_import_context，[快照服务](../../services/accounting_periods.py)。

## 必须明确的安全边界

- viewer 的商品和平台映射写权限是当前实现，并有测试支持；是否符合未来角色设计待业务确认。
- 独立存货接口未应用店铺过滤；月度报表销售受店铺过滤，但附带的是全局当前库存；不能宣称全部报表隔离。
- 管理员文件导入的 store_id 可为空；手工销售则必须选择店铺，两条路径不能混为同一规则。
- 导入撤销按当前店铺授权，operator 可撤销同店其他人的文件；旧文档“只能撤销自己的文件”已滞后。
- JWT 和数据库配置仍存在代码默认值、空用户表自动初始化账号、密码最低长度仅三位。文档不记录默认值；生产必须确认强配置和账号策略。Compose 已要求 JWT_SECRET 并显式传认证配置。
- 文件类型/大小校验不完整、公开图片风险仍需按模块核实。前端已删除旧登录缓存、不再持久化 JWT；Cookie 写请求新增 CSRF 不替代输入/上传校验。
- 新认证表有写入和过期清理，启动会补 users.is_active；生产需先备份并核实结构。提供方退出/回调 URL 不得进入代理、CDN 或 APM 的完整查询日志。

2026-10-13 增加统一登录、启停和会话保护，原业务角色/店铺矩阵保留。模块维护入口见[认证模块](../modules/auth/overview.md)，生产身份接入仍待验收。
