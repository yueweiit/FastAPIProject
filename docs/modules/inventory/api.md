# 入库批次 API

前缀 /batches，共 5 个端点。operator 编辑/删除还要求自己创建，admin 可操作任意未消耗批次；读取全局共享。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /batches` | [create_batch](../../../routers/batches.py) | admin/operator | BatchCreate | BatchResponse |
| `PUT /batches/{batch_id}` | [update_batch](../../../routers/batches.py) | admin/operator | batch_id、BatchCreate | BatchResponse |
| `DELETE /batches/{batch_id}` | [delete_batch](../../../routers/batches.py) | admin/operator | batch_id | ok |
| `GET /batches` | [list_batches](../../../routers/batches.py) | admin/operator/viewer | product_id、keyword、page、page_size | list[BatchResponse] \| BatchPageResponse |
| `GET /batches/export` | [export_batches](../../../routers/batches.py) | admin/operator/viewer | batch_ids、product_id、keyword | Excel 文件 |

## BatchCreate 请求

必填 product_id、quantity，quantity>0。采购信息优先 total_amount（≥0）；没有该字段时兼容 purchase_price（≥0，默认0）。shipping_cost、last_mile_cost、other_cost 是整批费用，默认0；钉钉单号和三个成本单号可空，最大长度64。shipping_date、arrived_at 可省略。

PUT 同样使用 BatchCreate，quantity 仍必填，费用省略会采用模型默认0；不是任意字段 PATCH。product_id 虽在请求里必填，但不会更换批次商品。可选单号通过 model_fields_set 判断是否保留；总金额/采购单价的复用规则见[流程](workflow.md)。

批次业务时间按无时区 DateTime 保存；页面请求示例为 `2026-09-01T00:15`，不转换为 UTC 的 `Z` 字符串。响应仍是既有 ISO 无时区字符串，未新增字段或改变响应类型。日期未改动的编辑保留响应中的秒数/小数秒；历史数据不自动回填，见[批次日期规则](workflow.md#批次业务日期)。

## 查询和导出

GET 列表支持 product_id、keyword（名称/SKU/批次号，≤255）、page、page_size；分页约定见[接口总览](../../knowledge/api-overview.md)。

GET 导出支持重复 batch_ids，或无 ID 时使用 product_id/keyword；有 ID 时以 ID 为准。示例：/batches/export?batch_ids=1&batch_ids=2。无可导出记录为404。

商品不存在404，编辑他人批次403，已消耗批次400，编号冲突三次后409；数量金额无效422。响应含创建人、数量、剩余数、成本、所有单号及时间，见 [schemas.py](../../../schemas.py)；实际实现见 [batches.py](../../../routers/batches.py)。

## 成本响应扩展

财务开关启用后，批次unit_cost含追溯调整；base_unit_cost为原保存值，cost_adjustment_per_unit为每件补差，cost_status为actual/partial/unverified。原采购/费用字段仍为原始值，不代表已改写。成本调整及不可编辑约束见[财务增强](../financial-control/overview.md)。
