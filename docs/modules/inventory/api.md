# 入库批次 API

前缀 /batches，共6个端点。admin/operator可编辑任意创建人的历史批次（含已售），强制留痕；删除仍受原创建人、出库及成本来源约束。读取全局共享，修改日志仅admin/operator可读。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /batches` | [create_batch](../../../routers/batches.py) | admin/operator | BatchCreate | BatchResponse |
| `PUT /batches/{batch_id}` | [update_batch](../../../routers/batches.py) | admin/operator | batch_id、BatchUpdate（含edit_version） | BatchResponse |
| `GET /batches/{batch_id}/logs` | [list_batch_change_logs](../../../routers/batches.py) | admin/operator | page≥1、page_size 1–100（默认20） | BatchChangeLogPageResponse |
| `DELETE /batches/{batch_id}` | [delete_batch](../../../routers/batches.py) | admin/operator | batch_id | ok |
| `GET /batches` | [list_batches](../../../routers/batches.py) | admin/operator/viewer | product_id、keyword、page、page_size | list[BatchResponse] \| BatchPageResponse |
| `GET /batches/export` | [export_batches](../../../routers/batches.py) | admin/operator/viewer | batch_ids、product_id、keyword | Excel 文件 |

## BatchCreate 请求

必填 product_id、quantity，quantity>0。采购信息优先 total_amount（≥0）；没有该字段时兼容 purchase_price（≥0，默认0）。shipping_cost、last_mile_cost、other_cost 是整批费用，默认0；钉钉单号和三个成本单号可空，最大长度64。shipping_date、arrived_at 可省略。

PUT使用BatchUpdate，要求edit_version；受保护批次改数量/金额还须cost_edit_mode="restate"，否则428（batch_restatement_mode_required），防止旧客户端无意覆盖已确认成本。quantity必填；费用省略仍用默认0，不是PATCH。product_id不会写回，可选单号省略保留/null清空。POST保持原契约。客户端应从edit_values回填有效金额；没有该对象时沿用原字段。

PUT的quantity范围为1–2147483647，对齐数据库INT；还须满足已售数量下限。

整批采购/各费用上限99999999.9999，对齐DECIMAL(12,4)；直接重算时基准单价同样受该上限约束。金额或派生差额越界均返回422，不依赖数据库溢出报500。

批次业务时间按无时区 DateTime 保存；页面请求示例为 `2026-09-01T00:15`，不转换为 UTC 的 `Z` 字符串。日期响应仍是既有 ISO 无时区字符串，未改变日期类型。日期未改动的编辑保留响应中的秒数/小数秒；历史数据不自动回填，见[批次日期规则](workflow.md#批次业务日期)。

## 编辑并发保护

BatchResponse包含edit_version（64位小写十六进制不透明值）。InventoryBatch持久化字段形成基础摘要，有调整层时再纳入不可变明细ID/数量/金额；包括零额来源。不是递增序号或数据库列，客户端必须保留打开时版本，不得自行计算或失败后自动替换。

PUT先锁批次和当前调整层、比对版本，再核对已售数量。缺少版本428（batch_edit_version_required）、格式无效422、过期409（batch_edit_conflict）。失败不写批次或成功日志，冲突后保留输入并重新核对。新剩余数=新quantity−原已售数，不能通过请求直接传remaining_quantity。编辑时不因入库日晚于已有销售日而拒绝；既有销售日期和FIFO分配不随此修改重算。

已售/已有补差/cost_basis批次改数量或成本，追加差额凭证并保留原FIFO单价；财务开关关闭返回503（batch_cost_controls_required）。数量小于已售返回409（batch_quantity_below_sold，附sold_quantity），余量与明细不符返回409（batch_quantity_inconsistent）。派生差额超出财务金额范围返回422（batch_cost_out_of_range）。纯资料更正不重算库存或追加成本。

## 查询和导出

GET 列表支持 product_id、keyword（名称/SKU/批次号，≤255）、page、page_size；分页约定见[接口总览](../../knowledge/api-overview.md)。

GET 导出支持重复 batch_ids，或无 ID 时使用 product_id/keyword；有 ID 时以 ID 为准。示例：/batches/export?batch_ids=1&batch_ids=2。无可导出记录为404。

Excel保留原21列及顺序，在末尾追加“店铺（创建人当前绑定）”。通过批次user_id关联User.store_id/Store.name一次性读取，创建人缺失或未绑定时显示“未关联店铺”；名称按文本写入，不执行为Excel公式。该列不是固化批次归属，不改变共享读取/FIFO扣库范围，也不新增数据库字段。

商品或待修改批次不存在404，viewer写入/读日志及operator删除他人批次403。删除已出库批次400、已有成本来源409；编辑错误码见上方。响应契约见[schemas.py](../../../schemas.py)，实现见[batches.py](../../../routers/batches.py)。

## 修改日志

日志查询固定返回 `{items,total,page,page_size}`，按日志ID倒序，超过末页调整页码。items包含id、batch_id、batch_no、action（update）、actor_id、actor_username、actor_role、before_values、after_values、created_at（UTC偏移明确的ISO时间）；快照金额为精确字符串。没有记录时返回空页，批次删除后仍能按原ID读取保留日志；viewer返回403。没有日志更新/删除API。

## 成本响应扩展

财务开关启用后，unit_cost含追溯调整，base_unit_cost为FIFO基准，cost_adjustment_per_unit为每件补差，cost_status为actual/partial/unverified。edit_values为可空金额对象（total_amount、purchase_price、shipping_cost、last_mile_cost、other_cost）；非空时用于显示和编辑，保留原顶层字段以兼容旧读取。重述批次的顶层采购/费用保存最近编辑目标，不应再与调整层直接相加。服务端cost_basis不返回、不接受客户端覆盖。来源与版本规则见[财务增强](../financial-control/overview.md)。
