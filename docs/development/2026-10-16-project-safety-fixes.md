# 2026-10-16 项目安全与财务一致性修复

本次按用户要求修复五项已复现问题，并修复收尾核查发现的人工比较值保存问题，共六项，范围覆盖商品图片、销售事务、损益页面和成本取数。没有增加依赖，没有操作服务器或发布代码。任务开始前已有的`docs/modules/reports/inventory-impairment-fields.md`修改保留，本次未编辑该文件；两份页面原有七处差异也保留。

## 问题与最终行为

| 问题 | 修复后行为 | 主要代码与维护入口 |
| --- | --- | --- |
| 商品上传任意扩展名，HTML可在同源公开路径运行 | 限制大小/像素、真实解码并重新编码为JPEG/PNG；历史HTML/SVG返回404，图片强制MIME、nosniff和sandbox CSP | [product_images.py](../../services/product_images.py)、[商品路由](../../routers/products.py)、[main.py](../../main.py)；[图片流程](../modules/products/workflow.md#创建与图片) |
| 手工订单先查后写，并发可重复扣库 | 新订单首条Sale占用order_claim唯一值，重复事务冲突后回滚；组合后续明细保持NULL | [Sale](../../models.py)、[兼容迁移](../../migrate.py)、[FIFO](../../services/fifo.py)、[销售路由](../../routers/sales.py)；[销售流程](../modules/sales/workflow.md) |
| 损益页面旧月数据可保存到新月，慢响应覆盖新结果 | 加载禁用保存，以请求序号和店铺/月校验响应；保存绑定已加载报表条件，防重复提交，等待保存后再查询；会话失效使旧请求无效 | [运行模板](../../templates/index.html)、[页面副本](../../index.html)；[损益流程](../modules/reports/workflow.md#店铺损益及期间) |
| 页面仅编辑本月，却整包替换人工值，清空已有上月/年累计 | 页面新增update_scope=current，在报表行锁内保留数据库人工比较值；默认all保持原完整替换契约，不固化OA默认金额 | [保存契约](../modules/reports/api.md#损益保存契约)、[schemas.py](../../schemas.py)、[报表路由](../../routers/reports.py) |
| 历史销售使用未来入库批次 | FIFO和导入预分配均限制到货自然日≤销售日；同日可用，历史不足手工400、导入整行待确认 | [fifo_sell](../../services/fifo.py)、[_allocate_available_stock](../../routers/sales.py)；[销售API](../modules/sales/api.md) |
| 损益和产品线漏计批次other_cost，成本偏低 | 新增自动“入库其他成本”，按FIFO明细数量/批次原始数量分摊；09/10/12同步计入营业成本 | [报表路由](../../routers/reports.py)；[全部公式](../modules/reports/calculation-formulas.md)、[报表流程](../modules/reports/workflow.md) |

## 数据与接口边界

- 表数量仍为19。新增`sales.order_claim VARCHAR(64) NULL`及`uq_sales_order_claim`唯一索引；order_no保留普通索引，未新增外部API输入/输出字段。历史记录保持NULL，由原order_no查询拦截已存在订单；新订单通过数据库唯一约束保护。组合首条占位与其他组件、扣库、成本明细在同一事务，失败回滚及撤销删除销售会释放占位。
- 保留批次锁序product_id、arrived_at、id，不因销售日期过滤而改变锁序。仅约束新录入的销售/文件，不修正已经存在的未来批次扣减、库存剩余量或历史成本。按文件行顺序预分配，共同商品需求先聚合，不能部分履行组合行。
- 图片原文件最多5 MiB、像素最多2000万、重新编码结果最多10 MiB；动图保留首帧。只清理本次失败新文件，更新保留旧图片，本次不删除历史uploads。公开图片权限未改成私有；HTML/SVG等历史非图片地址不再可用。
- 新“入库其他成本”独立于销售侧“其他直接成本”，自动分摊后不再进入人工分摊池。不改写sales.total_cost、历史凭证或人工报表JSON。已有人工费用若与批次other_cost重复，需财务审核，不能自动冲销。
- 损益PUT增加可选update_scope，默认all仍完整替换manual_values；current替换本月人工列，保留已有previous/ytd，忽略传入的比较值，未提交的本月科目清空。只保留原始人工值，不从响应复制OA默认值；已有报表保存加行锁。remarks仍完整替换。页面仅编辑本月，人工比较期间仍不跨月自动推算；已被旧版本清空的值无法凭空还原。

## 实际验证

2026-10-16在现有本地环境完成：

| 检查 | 实际结果与边界 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m unittest discover -s tests -q` | 220项通过；主要为模拟会话、临时目录及独立SQLite/ASGI验证，不启动main生命周期 |
| `node tests/profit_loss_page_checks.cjs` | 68项通过，覆盖两份页面的加载/保存顺序、条件切换、旧响应/错误隔离、会话失效及current保存范围 |
| `node tests/inventory_page_checks.cjs` | 34项通过，两份页面脚本与DOM模拟 |
| `node tests/auth_page_checks.cjs` | 48项通过，两份页面认证辅助函数与语法检查 |
| `.\.venv\Scripts\python.exe -m tests.mysql_sales_integrity --apply-local-order-schema` | 用户明确授权后仅在127.0.0.1:3309 / inventory_fifo_test / fifo_local_app补字段/索引；真实InnoDB不同商品同号并发只成功一次、无重复扣库、组合多明细、失败回滚重试及未来批次排除全部通过 |
| 同脚本不带参数 | 默认只读，复核column_exists=true、unique_index=true |
| 静态及范围检查 | 56个Python文件AST解析、383个本地文件链接、19张ORM表和36个损益科目对应检查通过；git diff --check通过。原有字段说明/requirements摘要及两份页面七处既有差异保持不变 |

本地MySQL脚本仅创建并清理随机前缀的自建商品、批次、销售和成本明细；对八张既有业务表的行数/内容摘要前后核对一致。新增结构保留，自增序列正常推进，不回填历史、不执行完整migrate、不初始化账号或快照。凭据仅从现有本地配置读取，输出不含密码或业务原始数据。脚本目标限制、写入开关见[mysql_sales_integrity.py](../../tests/mysql_sales_integrity.py)。

新增回归为[test_product_images.py](../../tests/test_product_images.py)、[test_sales_integrity.py](../../tests/test_sales_integrity.py)、[profit_loss_page_checks.cjs](../../tests/profit_loss_page_checks.cjs)；已有损益/产品线/FIFO用例同步补充。全量运行仍出现环境既有的passlib/bcrypt版本提示与依赖弃用警告，未影响通过，本次未调整依赖。

## 正式更新及待验证

1. 服务器尚未迁移或部署，运行中本地应用也未在本次重启。正式更新前备份数据库，核实sales字段/索引、DDL权限、索引创建锁等待及回滚安排。启动仍包含其他既存建表、兼容迁移、历史修复与初始化副作用。
2. 将模型、迁移、路由、服务（包含新增product_images.py）和运行模板一起更新；统一更新全部写入进程，旧版本不写order_claim，混跑无法保证完整防重。应用回退可以保留新增可空列和索引，无需删除业务数据；回退后不具备新防重能力。
3. 在实际反向代理路径核对/uploads确由受保护的应用挂载提供，且缓存不会继续提供旧HTML/SVG；检查正常图片展示、非法文件拒绝及响应头。本地ASGI/DOM模拟不代表浏览器、代理或生产验收。
4. 财务重新查询/导出09/10/12，核对入库其他成本和人工费用有无重复。实际历史异常流水、跨店同号策略、库存店铺归属及外部OA/汇率完整性仍按模块待验证事项维护，不自动重写数据。

运行及更新入口：[PROJECT_RUNBOOK.md](../../PROJECT_RUNBOOK.md)、[PROJECT_ARCHITECTURE.md](../../PROJECT_ARCHITECTURE.md)。

## 复审后的报表修复

后续代码审查补出三项问题，已在本地修复：

1. 09报表响应将最终计算值`value`与实际保存的人工值`manual_value`分开。页面只把人工值放入输入框；未人工填写的OA薪资作为占位提示，保存其他科目时仍提交薪资`null`，避免把OA默认金额意外固定。旧版本已经误存的薪资无法与用户有意覆盖区分，需按实际报表逐项复核，不自动清理。
2. 切换店铺或月份期间保存失败，仍显示原报表月份及错误信息；新选择的查询继续执行，不用旧报表响应覆盖新页面。
3. 09/10的采购、头尾程、入库其他三项自动成本，以`SaleCostDetail.quantity × SaleCostDetail.unit_cost`为每笔总额，按批次原始成本构成比例拆分，并让最后一个非零科目承接尾差；12产品线直接汇总相同的已保存明细成本。三项之和与FIFO成本一致，不改写历史销售。原始成本构成全为0但保存成本非0的异常历史暂归采购科目，需财务核对。

本次仅调整报表响应、页面、计算、测试和相关文档；没有新增表/字段、执行数据库迁移、启动服务或部署。全量本地`unittest`共223项通过；两份页面的损益请求/渲染模拟80项、存货DOM模拟34项、认证页面检查48项通过；`git diff --check`通过。测试没有连接真实OA或生产数据库，正式数据中的既有薪资覆盖值、成本构成异常与跨表勾稽仍待核验。
