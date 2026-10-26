# 入库与库存模块

## 功能

商品每批到货独立建档，保存原始数量、剩余数量、采购金额、单价、整批物流/其他成本、单件总成本、到货/发货时间、钉钉单号及创建人。提供搜索分页、编辑、删除和嵌入图片的 Excel 导出。依据：[routers/batches.py](../../../routers/batches.py)。

钉钉单号是留档字段，本模块没有调用钉钉审批 API。批次不含 store_id；列表、导出和商品库存为全局共享；写入只允许 admin/operator。两种角色均可编辑任意未被消耗且无成本调整来源的批次，每次保存追加修改日志；operator 删除仍仅限自己创建且未被消耗的批次。编辑不转移原创建人或库存归属。

## 成本与一致性

有 total_amount 时按 总金额÷数量 计算采购单价，不采用同请求 purchase_price 覆盖；旧请求仅传 purchase_price 时换算总金额。unit_cost = purchase_price + (shipping_cost + last_mile_cost + other_cost) ÷ quantity。采购金额和物流费用有不同含义，不能重复计入。

批次修改/删除先锁行并验证 remaining_quantity == quantity；库存扣减及撤销由销售模块负责。FIFO 按商品在所有可用批次中扣减，不按销售店铺筛选。库存报表从批次创建人的当前店铺绑定推导店铺，改绑/转移创建人会影响未冻结口径。

字段精度由 DECIMAL(12,4) 保存；算法没有在 _calc_unit_cost 中显式 quantize，展示舍入不能替代持久化规则。依据：[models.py](../../../models.py)、[fifo.py](../../../services/fifo.py)。

## 文档入口

- [业务流程](workflow.md)
- [代码映射](code-map.md)
- [数据库字段](database.md)
- [模块 API](api.md)
- [知识库总索引](../../knowledge/README.md)

实际成本补差由[财务增强模块](../financial-control/overview.md)统一维护，默认关闭；启用后查询成本包含补差，原批次保存成本与FIFO数量不变。已有调整来源的批次不能直接改写或删除。
