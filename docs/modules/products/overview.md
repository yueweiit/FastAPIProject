# 商品模块

## 功能

维护共享本地 SKU、名称、图片、商品类型和安全库存；提供搜索、服务端分页、下拉选项、图册、带图片 Excel 导出及商品 Excel 批量新增。依据：[routers/products.py](../../../routers/products.py)、[模板商品和图册区](../../../templates/index.html)、[product_import.py](../../../services/product_import.py)。

三种角色均有创建/修改/删除权限，服务端使用 RequireAnyRole。商品/库存查询不按创建人或店铺隔离；该行为由 [test_product_feature_permissions.py](../../../tests/test_product_feature_permissions.py)记录。

## 业务规则

SKU唯一；商品类型仅三选一：`stable`（稳健商品，每日1%，先扣除独立的安全库存数量）、`new`（新品，全部库存每日0.5%）、`no_impairment`（不跌价商品，不新增计提）。安全库存为0–2147483647整数，仅稳健商品可设置，新品/不跌价商品保存为0；安全库存大于库存时新增计提数量为0。各数量层累计上限90%，已有准备不因保护或停止计提清零，仍随销售转销。历史`safe_stock`及旧Excel“安全库存商品”兼容为稳健商品并保留安全库存；新写入用stable，不批量改写旧商品或历史规则。依据：[inventory_impairment.py](../../../services/inventory_impairment.py)的`effective_product_type/protected_stock_quantity`及路由`_impairment_settings`。

批量新增采用“下载模板 → 上传校验预览 → 确认导入 → 下载清单”。只支持 .xlsx，每次最多 2,000 个非空数据行、5 MB；SKU 必须按文本填写，保留前导零。已有 SKU 跳过且不更新，文件内重复 SKU 和字段错误阻止整批提交。确认时重新校验文件及数据库，新增商品与当日规则一起提交；不导入图片、库存、成本或历史日期。细节见[批量导入流程](workflow.md#商品-excel-批量新增)。

当前库存/库存价值为所有批次 remaining_quantity 及其 unit_cost 汇总；不是商品表的独立库存字段。update_product 的即时返回通过 _enrich_product 填 0 库存，重新列表后才得到汇总，不应依赖写入返回值计算真实库存。

删除会阻止仍被平台组件引用的商品；其他批次/销售/结算/店铺映射等关联仍须核对。图片磁盘变更与数据库提交不处于同一事务，失败可能造成孤立文件或失效路径。

图册是商品图片的另一种展示，支持名称/SKU搜索、联想候选和分页；沿用商品查询接口，没有独立表或独立业务服务。运行页面为 templates/index.html，根目录副本当前存在差异，不能以副本推断实际页面。

## 文档入口

- [业务流程](workflow.md)
- [代码映射](code-map.md)
- [数据库字段](database.md)
- [模块 API](api.md)
- [知识库总索引](../../knowledge/README.md)


业务变更的可选事务留痕已接入BusinessAuditRoute；捕获白名单、同次操作编号、角色/店铺权限、失败回滚及默认关闭的结构准备，统一见[操作日志](../operation-logs/overview.md#新增业务留痕)，不在本页重复维护。读取/导出不新增该类日志，原业务权限和历史接口契约保持。
