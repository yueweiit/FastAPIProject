# 商品模块

## 功能

维护共享本地 SKU、名称、图片、商品类型和安全库存；提供搜索、服务端分页、下拉选项、图册和带图片 Excel 导出。依据：[routers/products.py](../../../routers/products.py)、[模板商品和图册区](../../../templates/index.html)。

三种角色均有创建/修改/删除权限，服务端使用 RequireAnyRole。商品/库存查询不按创建人或店铺隔离；该行为由 [test_product_feature_permissions.py](../../../tests/test_product_feature_permissions.py)记录。

## 业务规则

SKU 唯一；类型只接受 stable（稳健）和 new（新品）；安全库存不能负数，新品强制为 0。创建或修改商品规则时，写入 product_impairment_rules 的生效历史，用于跌价计算，不只维护产品当前字段。依据：_impairment_settings、create_product、update_product。

当前库存/库存价值为所有批次 remaining_quantity 及其 unit_cost 汇总；不是商品表的独立库存字段。update_product 的即时返回通过 _enrich_product 填 0 库存，重新列表后才得到汇总，不应依赖写入返回值计算真实库存。

删除会阻止仍被平台组件引用的商品；其他批次/销售/结算/店铺映射等关联仍须核对。图片磁盘变更与数据库提交不处于同一事务，失败可能造成孤立文件或失效路径。

图册是商品图片的另一种展示，没有独立表或独立业务服务。运行页面为 templates/index.html，根目录副本当前存在差异，不能以副本推断实际页面。

## 文档入口

- [业务流程](workflow.md)
- [代码映射](code-map.md)
- [数据库字段](database.md)
- [模块 API](api.md)
- [知识库总索引](../../knowledge/README.md)
