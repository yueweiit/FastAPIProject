# 入库批次与库存查看 API

前缀 /batches，共8个端点（含1个已停用的兼容入口）。admin/operator可编辑或作废任意创建人的历史批次（含已售），强制留痕。页面统一通过“强制删除”执行软作废，未出库批次也保留原记录；旧物理删除接口返回410。活动批次共享读取，修改日志仅admin可读；页面已移除批次日志按钮，统一从操作日志入口查看。

兼容GET /batches/{batch_id}/logs与统一日志接口响应设置Cache-Control: private, no-store；旧弹窗请求显式禁用缓存。普通批次读取、写入与导出缓存策略保持，细节见[日志API](../operation-logs/api.md)。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /batches` | [create_batch](../../../routers/batches.py) | admin/operator | BatchCreate | BatchResponse |
| `PUT /batches/{batch_id}` | [update_batch](../../../routers/batches.py) | admin/operator | batch_id、BatchUpdate（含edit_version） | BatchResponse |
| `GET /batches/{batch_id}/logs` | [list_batch_change_logs](../../../routers/batches.py) | admin | page≥1、page_size 1–100（默认20） | BatchChangeLogPageResponse |
| `DELETE /batches/{batch_id}`（已停用） | [delete_batch](../../../routers/batches.py) | admin/operator | batch_id | 410 batch_delete_retired，不写入数据 |
| `POST /batches/{batch_id}/void` | [void_batch](../../../routers/batches.py) | admin/operator | BatchVoidRequest：edit_version、reason | BatchResponse |
| `GET /batches` | [list_batches](../../../routers/batches.py) | admin/operator/viewer | product_id、batch_id、keyword、page、page_size、voided_only | list[BatchResponse] \| BatchPageResponse |
| `GET /batches/export` | [export_batches](../../../routers/batches.py) | admin/operator/viewer | batch_ids、product_id、keyword、voided_only | Excel 文件 |

## 库存内归属管理API

`PUT /batches/{batch_id}/impairment-stores`仅admin/operator。请求为`impairment_store_ids`（至少1项、最多100）、`edit_version`（64位内容版本）、`impairment_change_mode=from_arrival/from_change_date`（默认from_arrival），禁止额外字段。返回BatchResponse。父批次及关联锁内重验版本，冲突409，作废409，不存在404，无效/空/停用店铺422；不修改数量、剩余、单价、成本或日期，追加update日志。GET /batches?product_id=...&page=...&page_size=20用于分页读取；旧数组响应不变。新管理器按角色隐藏写入口，失败保留选择，保存期间锁表单、关闭/退出/同账号重登使旧请求失效。

## 库存查看 API

### 盘点与跌价扩展

`/inventory`现共8个业务端点，原4个接口保持兼容，另增加以下4个。以下读取用RequireAnyRole，保存用RequireOperator；全部校验当前角色/绑定，未来日期及1900年前日期422，非管理员历史日期403，不接受店铺参数切换范围。

| 方法/路径 | 输入 | 响应/范围 |
| --- | --- | --- |
| `GET /inventory/assessment` | as_of可选（默认上海今天）、keyword≤255、product_id>0、page≥1、page_size1–100默认20 | items/total/page/page_size/as_of/scope/can_write；scope为global或shared_current |
| `GET /inventory/assessment/export` | as_of、keyword、product_id；忽略分页参数 | 全部匹配商品xlsx；`inventory_assessment_YYYYMMDD.xlsx`，上限2000商品/10000批次 |
| `POST /inventory/stock-counts` | InventoryCountCreate JSON | 新增不可覆盖盘点版本；相同幂等键/内容返回原记录 |
| `GET /inventory/stock-counts` | product_id、count_date、page、page_size | 按该商品/日期分页，items/total/page/page_size；版本ID倒序 |

assessment.items含product_id/sku/name/image/as_of、month_inbound/month_outbound、system_quantity、average_unit_cost、inventory_amount、impairment_amount、monthly_total_impairment_amount、book_value、product_type/safe_stock_quantity、warnings、batches、ledger_version、count及count_basis_changed。count为空表示未盘点；0不能当未填写。batches含batch_id/no、quantity、arrived_at、age_days、unit_cost、inventory_amount、impairment_amount、monthly_total_impairment_amount、store_names、store_allocations及note；店铺分摊金额非管理员仅含本人绑定店铺，不返回历史days。保存的count包含id/count_date/revision/system_quantity/actual_quantity/difference/note/created_by/created_at。

month_inbound沿用既有入库可见范围：viewer不包含作废批次，admin/operator保留原范围；页面和Excel一致。内部system_quantity/ledger_version仍按完整共享数量事实计算，不能随读取角色变化。逐批数量核对失败（即使SKU合计相等）时，warnings增加`inventory_assessment_quantity_mismatch`，该商品相关货币字段及批次quantity/金额、店铺分摊为null（单件成本保留已知源值）；其他商品保留已知值，Excel相关不完整合计留空。接口不新增/改名字段，不暴露内部逐批核验映射。

保存必传product_id、count_date、actual_quantity（严格JSON整数0–2147483647）、ledger_version（页面给出的64位小写十六进制）、request_key（16–64位字母数字/横线/下划线），可传previous_id（当前盘点ID，首次null）及note≤1000。客户端不得用当前显示总数自行伪造版本。差异在服务端计算；记录当时账面，不提交直接库存调整。

稳定错误：`inventory_count_schema_required`为503（表未准备，不自动DDL）；`inventory_count_conflict`/`inventory_count_untrusted`为409（版本或数量不可信）；`inventory_count_date_restricted`为403；`inventory_date_invalid`/`inventory_future_date`为422；源/导出上限复用既有inventory_sources_too_large、inventory_export_too_large、inventory_export_sources_too_large，空导出404 inventory_export_empty。保存时可能发生批次锁等待；新表唯一约束冲突整笔回滚，不调整库存。

以下为原有只读接口：

既有[inventory.py](../../../routers/inventory.py)的4个只读GET接口，批次/商品契约和写入权限不变：

| 方法与路径 | 参数 | 范围与响应 |
| --- | --- | --- |
| GET /inventory/overview | keyword≤255、可选product_id>0、page≥1、page_size=20（1–100） | 既有角色共享，可未绑定；items/total/page/page_size、scope=shared、summary |
| GET /inventory/daily | month为过去/当前月首日；可选product_id>0、keyword、page/page_size | admin为global；其他账号须绑定，scope=store_outbound；items/total/page/page_size、month/start/end、scope、warnings |
| GET /inventory/movements | product_id>0、day、kind=inbound/outbound/adjustment（默认outbound）、page/page_size | 出库按销售权限，adjustment仅admin，viewer入库来源排除作废；items/total/page/page_size、product_id/sku/name/day/kind |
| GET /inventory/export | view=overview/daily/matrix（默认overview）、keyword、可选product_id；daily/matrix必传month | 总览共享，日结沿用绑定店铺权限；xlsx附件，不按页面分页截断 |

overview.items沿用商品响应，旧safe_stock和stable正安全库存均显示stable；stock_status=zero/low/normal/pending，低库存预警针对稳健商品的安全库存字段。summary仍仅为本页小计，成本含启用的补差、不扣准备。

overview的可选product_id用于联想候选精确筛选，复用商品有效成本聚合；与keyword按AND组合，无匹配返回空页，页码归一为1。未传时保留原搜索/分页契约。联想继续复用`GET /products?keyword=...&page=1&page_size=20`，不新增候选API或变更商品接口；总览、日结和导出均携带所选ID。

daily.items含product_id/sku/name/image/warnings及days，每日字段为date、opening_quantity、inbound、outbound、adjustment、closing_quantity，含无变动日。非管理员期初/调整/期末为null，不在客户端补0或通过额外店铺参数越权。矛盾日结返回null及warning；更正链不连续导致入库基数不可信时，inbound及对应来源quantity也为null，不能输出按矛盾日志推导的件数。口径见[日结流程](workflow.md#库存查看与每日进出)。

movements.items含source_id、batch_id、batch_no、occurred_at、kind、quantity、reference；kind为inbound/outbound/quantity_correction/void，入库正数、出库负数、更正为差额、未知作废量null。reference为入库单号或有权读取的订单号，不返回其他店铺订单、日志身份、成本、原始JSON。日志/作废显示上海业务时间，不替代审计日志接口。

非法月份、未来日或源超限422，商品不存在404，未绑定/越权403；新增detail={code,message}，稳定码为inventory_month_invalid、inventory_future_date、inventory_sources_too_large、inventory_product_missing、inventory_adjustment_restricted。当前月截止上海当天，超过末页调整页码。GET /inventory-workspace.js是静态脚本。

### 库存导出

view=overview输出“库存总览”“说明”；daily输出“月度汇总”“每日进出”“说明”；matrix输出“月度汇总”“每日横向表”“说明”，每天有入库/出库/调整/期末四列。商品追踪传所选product_id，其他视图导出当前查询全部匹配商品，不限当前页；daily为所选月全期间，不只当前选中日期。

单次最多2,000个商品，每100个分组复用页面查询/日结构建并固定同一期间；10,000条源记录保护仍有效。结果转成普通数据后释放只读事务，再在线程中生成Excel，不创建业务记录或补快照。MIME为application/vnd.openxmlformats-officedocument.spreadsheetml.sheet，附件名inventory_overview_YYYYMMDD.xlsx或inventory_daily/matrix_YYYYMM.xlsx。

非管理员期初/调整/期末在明细、月汇总、横向表及相关合计中均为空白并注明范围；不可信入库基数和不完整合计同样留空。SKU/名称/筛选文本强制按Excel文本写入，保留前导零、防止公式注入。空结果404 inventory_export_empty；超商品上限422 inventory_export_too_large；缺月422 inventory_export_month_required；日结源超限422 inventory_export_sources_too_large，不下载不完整文件。

## 旧删除接口（停用）

旧删除接口保留路径并在OpenAPI中标记deprecated，所有通过角色校验的请求均返回410 `batch_delete_retired`，提示刷新页面后使用强制删除。该入口不读取或修改库存、不追加成功日志，也不自动转为作废；调用方须显式提交`POST /batches/{batch_id}/void`及当前`edit_version`、非空`reason`，成功后同事务留痕。

## BatchCreate 请求

`impairment_store_ids: list[int]`最多100项，正ID、去重排序。POST遗漏/[]允许入库，写显式空归属日规则以区别历史绑定兼容；指定ID仍校验存在及启用状态。原PUT遗漏字段保持归属不变，允许未分配批次编辑其他资料；显式[]仍拒绝。归属迁至库存页独立保存，管理时必须至少一家，旧客户端原PUT合法归属仍兼容。仅admin/operator写入，viewer只读；店铺不影响共享库存/FIFO，历史日规则与旧月度版本保留。

PUT另接受`impairment_change_mode: Literal["from_arrival","from_change_date"]`，遗漏默认`from_arrival`兼容旧客户端；仅在请求含impairment_store_ids时处理。响应新增`impairment_store_history: [{effective_date,store_ids,store_names,is_explicit}]`；无日规则为[]。生效日由服务端决定，不接受任意回填日期；相同店铺的按日保存不追加规则，历史更正可重置已有历史。历史也纳入并发版本和日志，旧版本保存请求409；非法模式422。新页面默认按修改日，新前端发现旧响应缺少历史字段则禁用该模式，避免旧后端忽略字段导致意外追溯。

模式遗漏且店铺选择未变化时保留已有历史，避免旧表单仅改其他资料却意外清空日规则；重置同一选择的历史必须显式传`from_arrival`。

批次Excel在既有列末尾追加“跌价归属店铺（均分）”，显示手工店铺名或“未指定（沿用原归属）”；该列和作废原因均强制按文本写入，原列顺序保持兼容。

必填 product_id、quantity，quantity>0。采购信息优先 total_amount（≥0）；没有该字段时兼容 purchase_price（≥0，默认0）。shipping_cost、last_mile_cost、other_cost 是整批费用，默认0；钉钉单号和三个成本单号可空，最大长度64。shipping_date、arrived_at 可省略。

PUT使用BatchUpdate，要求edit_version；受保护批次改数量/金额还须cost_edit_mode="restate"，否则428（batch_restatement_mode_required），防止旧客户端无意覆盖已确认成本。quantity必填；费用省略仍用默认0，不是PATCH。product_id不会写回，可选单号省略保留/null清空。POST保留原采购价/总金额兼容，归属可遗漏或为空，按上方规则保存为待分配。客户端应从edit_values回填有效金额；没有该对象时沿用原字段。

PUT的quantity范围为1–2147483647，对齐数据库INT；还须满足已售数量下限。

整批采购/各费用上限99999999.9999，对齐DECIMAL(12,4)；直接重算时基准单价同样受该上限约束。金额或派生差额越界均返回422，不依赖数据库溢出报500。

批次业务时间按无时区 DateTime 保存；页面请求示例为 `2026-09-01T00:15`，不转换为 UTC 的 `Z` 字符串。日期响应仍是既有 ISO 无时区字符串，未改变日期类型。日期未改动的编辑保留响应中的秒数/小数秒；历史数据不自动回填，见[批次日期规则](workflow.md#批次业务日期)。

## 编辑并发保护

BatchResponse包含edit_version（64位小写十六进制不透明值）。InventoryBatch持久化字段形成基础摘要，有调整层时再纳入不可变明细ID/数量/金额；包括零额来源。不是递增序号或数据库列，客户端必须保留打开时版本，不得自行计算或失败后自动替换。

PUT先锁批次和当前调整层、比对版本，再核对已售数量。缺少版本428（batch_edit_version_required）、格式无效422、过期409（batch_edit_conflict）。失败不写批次或成功日志，冲突后保留输入并重新核对。新剩余数=新quantity−原已售数，不能通过请求直接传remaining_quantity。编辑时不因入库日晚于已有销售日而拒绝；既有销售日期和FIFO分配不随此修改重算。

已售/已有补差/cost_basis批次改数量或成本，追加差额凭证并保留原FIFO单价；财务开关关闭返回503（batch_cost_controls_required）。数量小于已售返回409（batch_quantity_below_sold，附sold_quantity），余量与明细不符返回409（batch_quantity_inconsistent）。派生差额超出财务金额范围返回422（batch_cost_out_of_range）。纯资料更正不重算库存或追加成本。

## 查询和导出

列表默认只返回活动批次；`voided_only=true`允许admin/operator查看已作废批次，viewer返回403。BatchResponse新增可空`voided_at`、`voided_by`、`void_reason`，旧客户端可忽略。admin/operator的`POST /batches/{batch_id}/void`可跨创建人提交`edit_version`和作废原因（去首尾空白后非空、最多1000字），在批次和成本来源行锁内校验当前版本，过期409（batch_edit_conflict）、重复作废409（batch_voided）；成功同事务追加action=void日志。作废保留原数量、销售成本明细和旧月度版本；已作废批次不能再次编辑或重复作废，不参与新FIFO及当前库存。关联销售的历史成本展示保留，利润待核对。

GET 列表支持 product_id、batch_id（正整数，精确匹配，与其他条件取交集）、keyword（名称/SKU/批次号，≤255）、page、page_size；分页约定见[接口总览](../../knowledge/api-overview.md)。入库列表的“修改归属”用product_id/batch_id读取指定活动批次的最新版本；匹配不到返回空数组/空分页，不返回其他批次。不传batch_id保持原查询及响应兼容。

GET 导出支持重复 batch_ids，或无 ID 时使用 product_id/keyword；有 ID 时以 ID 为准。示例：/batches/export?batch_ids=1&batch_ids=2。无可导出记录为404。

导出默认只包括活动批次。admin/operator可用`voided_only=true`或显式ID导出已作废批次；viewer传该参数返回403，按ID读取已作废批次被过滤。原21列顺序不变，在末尾店铺列后追加“作废时间（UTC）”“作废原因”；店铺名与原因强制按Excel文本写入。已作废行的“剩余数”为历史原值，不是当前可售库存。

Excel保留原21列及顺序，在末尾先追加“店铺（创建人当前绑定）”，再追加作废时间和原因。通过批次user_id关联User.store_id/Store.name一次性读取，创建人缺失或未绑定时显示“未关联店铺”；名称按文本写入，不执行为Excel公式。店铺列不是固化批次归属，不改变共享读取/FIFO扣库范围。

商品或待修改批次不存在404，viewer写入403；运营/查看者读取日志均403。旧DELETE对admin/operator固定返回410，不再按创建人、出库状态或成本来源执行物理删除；编辑错误码见上方。响应契约见[schemas.py](../../../schemas.py)，实现见[batches.py](../../../routers/batches.py)。

## 修改日志

action除update外新增void。void的after_values包含作废时间、操作者ID和原因；日志不随批次状态改变而删除。

日志查询固定返回 `{items,total,page,page_size}`，按日志ID倒序，超过末页调整页码。items包含id、batch_id、batch_no、action（update或void）、actor_id、actor_username、actor_role、before_values、after_values、created_at（UTC偏移明确的ISO时间）；快照金额为精确字符串。没有记录时返回空页，批次删除后仍能按原ID读取保留日志；viewer返回403。没有日志更新/删除API。

## 成本响应扩展

财务开关启用后，unit_cost含追溯调整，base_unit_cost为FIFO基准，cost_adjustment_per_unit为每件补差，cost_status为actual/partial/unverified。edit_values为可空金额对象（total_amount、purchase_price、shipping_cost、last_mile_cost、other_cost）；非空时用于显示和编辑，保留原顶层字段以兼容旧读取。重述批次的顶层采购/费用保存最近编辑目标，不应再与调整层直接相加。服务端cost_basis不返回、不接受客户端覆盖。来源与版本规则见[财务增强](../financial-control/overview.md)。
