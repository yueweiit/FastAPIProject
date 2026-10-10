# 财务主数据模块

## 功能与角色

管理产品线、平台店铺、店铺 SKU 生效映射，以及平台 SKU 对本地商品的组件映射。店铺/产品线/店铺 SKU 读取开放三种角色，写入仅 admin；平台 SKU 映射读写均开放三种角色。依据：[routers/finance_masters.py](../../../routers/finance_masters.py)。

页面在用户管理区创建店铺和绑定账号，在商品区维护平台 SKU 组件；产品线/店铺 SKU 有 API，但当前模板未发现独立维护界面。不存在平台授权凭据或同步任务管理页面。

## 必须区分两套映射

| 对象 | 业务作用 | 关键区别 |
| --- | --- | --- |
| StoreProduct | 店铺 SKU/平台商品 → 本地商品及产品线 | 有 store_id、产品线和生效日期，参与报表归类/旧数据推断 |
| PlatformSkuMapping + Component | 平台 SKU → 一个或多个本地商品及数量倍数 | 没有 store_id 或生效历史，订单详情导入严格依赖该映射 |

创建 StoreProduct 不能替代订单详情的 PlatformSkuMapping；商品导入成功也不意味着已有产品线归属。启停映射不自动更改历史销售/结算记录。映射变更却可能影响重新导入、旧记录店铺推断和产品线报表归类，需联读[销售流程](../sales/workflow.md)、[报表流程](../reports/workflow.md)。

主数据有物理删除接口，并非一律逻辑删除；产品线和店铺删除有部分引用检查，历史其他外键仍可能限制。推荐维护任务按实际业务确定停用/新版本策略，不将“推荐”写成已实现机制。

## 文档入口

- [业务流程](workflow.md)
- [代码映射](code-map.md)
- [数据库字段](database.md)
- [模块 API](api.md)
- [知识库总索引](../../knowledge/README.md)
