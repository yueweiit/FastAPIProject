# 财务增强API

路由前缀`/financial-control`，共22个业务方法；依据[financial_control.py](../../../routers/financial_control.py)、[schemas.py](../../../schemas.py)。Cookie写请求继续要求CSRF。除config外需要FIFO_FINANCIAL_CONTROLS_ENABLED=1，否则在任何新增表查询前返回503；结构未准备不能启用。

| 方法/路径（加前缀） | 角色 | 参数/结果 |
| --- | --- | --- |
| GET /config | 所有登录角色 | enabled、can_write、can_post、费用/分类标签 |
| POST /cost-adjustments/preview | admin/operator | CostAdjustmentRequest；批次、分摊及已售/未售预览，不写库 |
| POST /cost-adjustments | admin/operator | 同上；持久化调整头；operator仅自己的批次 |
| GET /cost-adjustments | 所有角色 | 可选batch_id，limit1–200默认50；共享库存记录及allocations |
| POST /cost-adjustments/{row_id}/reverse | admin/operator | FinanceReversalRequest；反向头；原批次权限一致 |
| POST /expenses | admin/operator | ExpenseRequest；该店铺draft凭证 |
| GET /expenses | 所有角色 | store_id、report_month、page、page_size≤100；items/total/sources |
| POST /expenses/{row_id}/post | admin | 确认draft，posted重试复用 |
| POST /expenses/{row_id}/cancel | admin/operator | FinanceReversalRequest；只作废draft |
| POST /expenses/{row_id}/reverse | admin | FinanceReversalRequest；追加相反posted凭证 |
| PUT /expense-source | admin/operator | ExpenseSourceRequest；店铺/月/科目选择manual或ledger |
| GET /reconciliation | 所有角色 | store_id、report_month；entries、原始科目/分类、跨月payouts、当月receipts、warnings |
| PUT /settlements/{entry_id}/classification | admin/operator | SettlementClassificationRequest；breakdown受控科目Decimal字典及note；合计等于原差额 |
| POST /receipts | admin/operator | ReceiptRequest；实际原币到账，明确汇率 |
| POST /receipts/{row_id}/reverse | admin/operator | FinanceReversalRequest；反向回款 |
| GET /overview | 所有角色 | store_id、report_month；利润层次、warnings及摘要 |
| POST /versions | admin/operator | FinancialVersionRequest；已结束月份，返回版本元数据 |
| GET /versions | 所有角色 | store_id、report_month；最近100个版本，不返回payload |
| GET /versions/{version_id} | 所有角色 | 指定版本及冻结payload |
| GET /versions/{version_id}/compare | 所有角色 | changed、利润行changes、当前warnings/层次 |
| GET /versions/{version_id}/export | 所有角色 | 从冻结payload生成xlsx；四张单店表＋说明 |
| GET /audit | admin | store_id；该店铺最近100条审计（共享成本审计store_id为空，来源可从调整单查询） |

非admin店铺操作必须匹配当前绑定，不通过前端隐藏保证权限。费用来源/版本/费用/回款操作统一先锁Store；成本先锁批次，后读/写调整来源。唯一冲突返回409结构化detail.code/message；不输出连接信息。

## 写入契约

request_key为8–64位ASCII字母数字下划线/横线，客户端同一提交重试保留，表单内容变化后更换。来源凭证号必填，建议含供应商/平台前缀，防止不同来源单号碰撞。重复请求内容不同返回409；同时并发首次提交可能由唯一约束返回409，应查询已有记录，不盲目生成新凭证。

成本单重复提交仍验证当前批次权限。quantity模式的分配权重由首次入账数量固化：批次后来更正数量，相同request_key、单据内容及批次集合仍返回原单，不重摊金额、不更新原分配或新增审计。weight/volume/value模式仍比较显式weight；金额、核对状态、来源、其他单据内容或批次集合改变均返回409 idempotency_conflict。数量模式不使用客户端weight作为分摊依据。规则实现见[post_cost_adjustment](../../../services/financial_controls.py)。

工作台优先使用crypto.randomUUID；非本机HTTP等缺少此方法的环境通过crypto.getRandomValues生成UUID v4，失败重试继续复用同一表单标识。两种安全随机接口都不可用时显示集中语言资源中的升级提示，不降级为Math.random。

金额原币Decimal最多16位4小数；汇率18位8小数正数。CNY仅接受1或省略，外币不能省略；人民币结果四位HALF_UP，校验可存储范围。cost的amount是差额，可负或0；expenses与receipts金额均保留原币、固化汇率及amount_cny。日期不晚于当天，月份必须当月1日。

CostAdjustmentRequest另含source_ref、category（purchase/head_shipping/last_mile/other）、allocation_basis（quantity/weight/volume/value）、document_date、rate_date、allocations[{batch_id,weight?}]、is_final、note。ExpenseRequest另含store_id、expense_date、category、source_ref、settlement_entry_id?、note；ReceiptRequest另含store_id、payout_id、receipt_date、source_ref、note。FinanceReversalRequest为request_key、必填note；FinancialVersionRequest为request_key、store_id、report_month、note。

## 既有接口的兼容字段

批次响应unit_cost为追溯后成本，并增加base_unit_cost、cost_adjustment_per_unit、cost_status。销售total_cost/profit_cny/成本明细unit_cost包含调整，增加original_total_cost、cost_adjustment_cny、cost_status及明细base_unit_cost；持久化原字段不变。损益行增加input_source（默认manual），ledger行只读但manual_value仍返回原人工备选值。列表数组/分页形态不改变，旧字段保留。

新财务路由在普通dict编码之前将Decimal转为字符串，保持原币及人民币四位精度；调用端不能假定金额都是JSON number。已有导入若有费用/分类引用，撤销返回409 import_has_financial_references，库存不恢复/删除。
