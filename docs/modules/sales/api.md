# 销售、导入及待确认 API

前缀 /sales，共 12 个端点；非管理员敏感读取按当前绑定店铺。operator 撤销当前店铺文件，admin 可撤销全部。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /sales/import` | [import_sales_file](../../../routers/sales.py) | admin/operator | file、store_id | SalesImportResponse |
| `GET /sales/imports` | [list_sales_imports](../../../routers/sales.py) | admin/operator/viewer | limit | list[SalesImportBatchResponse] |
| `POST /sales/imports/{import_batch_id}/rollback` | [rollback_sales_import](../../../routers/sales.py) | admin/operator | import_batch_id | SalesImportRollbackResponse |
| `POST /sales/manual-settlements` | create_manual_settlement | admin/operator | 店铺、来源字段、人民币金额、request_key | 不扣库存的结算补录SaleResponse |
| `POST /sales` | [create_sale](../../../routers/sales.py) | admin/operator | SaleCreate | SaleResponse |
| `GET /sales/pending-confirmations` | [list_pending_confirmations](../../../routers/sales.py) | admin/operator/viewer | page、page_size | PendingConfirmationPageResponse |
| `GET /sales/pending-confirmations/export` | [export_pending_confirmations](../../../routers/sales.py) | admin/operator/viewer | 无 | Excel 文件 |
| `GET /sales/monthly-summary` | [monthly_sales_summary](../../../routers/sales.py) | admin/operator/viewer | product_id、keyword、month | list[MonthlySalesSummaryResponse] |
| `GET /sales` | [list_sales](../../../routers/sales.py) | admin/operator/viewer | product_id、keyword、month、page、page_size | list[SaleResponse] \| SalePageResponse |
| `GET /sales/export` | [export_sales_records](../../../routers/sales.py) | admin/operator/viewer | product_id、keyword、month | 全部匹配台账的Excel文件 |
| `GET /sales/{sale_id}/cost-breakdown` | [sale_cost_breakdown](../../../routers/sales.py) | admin/operator/viewer | sale_id | SaleCostExplanationResponse：本行代入、FIFO批次及完整订单汇总 |
| `GET /sales/{sale_id}` | [get_sale](../../../routers/sales.py) | admin/operator/viewer | sale_id | SaleResponse |

## 手工销售与查询

已作废批次的原销售仍出现在列表与详情，cost_details和total_cost保留原始追溯金额；SaleResponse.cost_status=`needs_review`、cost_review_reason列出批次号，profit_cny留空。monthly-summary新增cost_review_count，受影响月份保留原销售成本展示但gross_profit_cny留空；不能将显示的历史成本当作已核准金额。原Sale.profit、成本明细和旧月度版本均不改写。依据：[sale_cost_context](../../../services/cost_layers.py)、[sales.py](../../../routers/sales.py)。

SaleCreate：product_id、order_no、quantity>0；store_id 可在模型层为空，但路由要求有效店铺，非管理员使用绑定店铺。selling_price/platform_fee 默认0且≥0，sold_at 可省略。订单号最大64、strip 后不能为空，前置查询已存在订单号返回400，并发占位/数据完整性冲突返回409且回滚；截至销售日可用库存不足返回400。到货日按自然日比较，不能使用销售日之后的批次。order_claim仅为数据库内部字段，不增加请求或响应字段。

GET 列表及 monthly-summary 支持 product_id、keyword≤255、month=YYYY-MM；列表还支持 page/page_size。monthly-summary 按全部筛选结果汇总，不只是当前页，返回数量、人民币收入/成本/费用/毛利、fx_missing_count及新增settlement_event_count。未关联出库的退款/调整/赔付事件纳入金额，不增加销售单数或数量；同事件只计一次。

SaleResponse 包含原始金额、*_cny 字段、汇率依据、店铺和 cost_details。分页/数组列表均包含独立财务事件：赔付record_type=compensation_income，其他退款/调整为settlement_adjustment，负id、quantity=0；不是sales主键，不能调用GET /sales/{sale_id}。物流/平台/物料赔付以结算总金额乘固化汇率计入收入，负数冲减收入、费用为0；缺失汇率仍返回null。先合并完整结果再分页；月度统计采用相同事件取数。商品筛选仅归属明确单一product_id的事件；组合份额未定的事件保留全店汇总，不猜测商品分摊。

新导入的非赔付退款/调整可按产品名保留明确的单一product_id，因而纳入product_id/本地SKU筛选、月度统计及相同条件导出；识别不到仍接受为未归属结算，不强制商品匹配。组件关联不等于库存出入库，返回实际出库数量仍为0，来源数量保留原值。手工结算补录继续不关联商品，见[导入流程](workflow.md#文件导入)。

## 销售台账导出

列表（分页/数组）、`GET /sales/{sale_id}` 及手工销售响应在 `SaleResponse` 增加以下只读字段，旧字段和结构兼容，无数据库迁移；规则见[成本流程](workflow.md#出库成本拆分与跌价参考)。

| 字段 | 类型/精度 | 含义 |
| --- | --- | --- |
| `purchase_cost_cny` | Decimal / null，分 | 本行采购成本 |
| `head_shipping_cost_cny` | Decimal / null，分 | 本行头程成本 |
| `last_mile_cost_cny` | Decimal / null，分 | 本行尾程成本 |
| `other_cost_cny` | Decimal / null，分 | 本行其他入库成本，与平台其他费用分开 |
| `order_total_cost_cny` | Decimal / null，分 | 四项展示金额之和，本行总成本；原 `total_cost` 仍为完整精度 |
| `cost_breakdown_review_reason` | string / null | 原始构成/明细缺失、不一致或作废的拆分待核对说明 |
| `impairment_cny` | Decimal / null，完整计算精度 | 出库商品已提跌价参考金额，独立于成本及毛利 |
| `impairment_review_reason` | string / null | 跌价待核对或历史规则推定说明，已知参考金额也可附说明 |

非库存事件上述金额为0；有出库但未知的分项/跌价为null。字段按实际本地组件数量计算，不对组合订单重复返回整单总额；不得把 `impairment_cny` 直接作为销售店铺跌价费用。服务端先校验现有店铺权限，再为获准响应计算，跨店全局事实仅用于共享安全库存计算。

`GET /sales/export`沿用列表的`month=YYYY-MM`、`keyword≤255`和可选`product_id>0`筛选；月份为空导出全部月份，keyword首尾空白忽略，商品ID与关键字按AND组合。没有page/page_size限制，不按当前页截断。admin读取全部店铺，operator/viewer固定当前绑定店铺，无绑定403；不接受以store_id切换范围。静态路径先于`/{sale_id}`注册，不改变已有详情接口。

接口复用`list_sales`的不分页响应，包含相同的独立退款/调整/赔付事件及0数量、固化汇率、成本追溯和待核对空值；商品名称/SKU批量读取，无逐记录查询。先用相同权限/筛选SQL统计销售及独立事件，超过20,000条返回422 `sales_export_too_large`（含max_rows），不截断；空结果404 `sales_export_empty`。非法月份沿用400/422校验，商品ID或关键字长度错误422。

文件包含“销售记录”和“说明”：原23列保留顺序，现追加4列单件分项及交易类型、来源数量共29列；前18列保留原位置（第9列展示本行订单总成本），追加采购、头程、尾程、其他入库成本及出库商品跌价参考。数量/收入/成本/费用/毛利、四项成本及跌价提供全部导出记录合计，未知金额及对应合计为空；单价和汇率不合计。所有用户文本按Excel文本写入，保留前导零并防止公式注入。“说明”记录月份、关键字、可选商品ID、权限范围、分项舍入及跌价独立口径。附件名`sales_YYYYMM.xlsx`，无月份为`sales_all.xlsx`，MIME为application/vnd.openxmlformats-officedocument.spreadsheetml.sheet。

只SELECT，不扣库、改成本、补快照或写导出记录。取数完成后释放只读事务，在线程生成文件。新接口须加载更新后的后端；前端未加载接口时会明确提示，不把错误网页下载为Excel。页面行为见[流程](workflow.md#销售台账导出)。

## 单件成本与计算明细

SaleResponse增加`transaction_type`、`source_sku_id`、`source_product_name`、`source_quantity`及`source_type`，从保存的结算来源读取，原product_id仍为本地成本组件。列表display_sku/display_name展示来源SKU ID及产品名；没有来源的旧手工库存销售保留本地商品展示兼容。SKU ID按文本保留前导零及长编号；keyword同时匹配来源SKU ID、产品名、交易类型和本地商品，列表/月度/导出采用同一筛选。计算明细展示本地组件，并附来源表达式与数量。旧版只有直接sale_id的来源也可读取，不补写关联。

`POST /sales/manual-settlements`为人民币独立结算补录：接收store_id、request_key（16至64位字母/数字/下划线/连字符）、occurred_on日期、order_no≤128、sku_id≤128、product_name≤500、transaction_type≤32、整数quantity、net_product_sales及settlement_total（16位/4位小数，允许负数）。管理员须指定有效店铺，运营只能写绑定店铺，viewer禁止。响应quantity固定0、source_quantity为录入值，成本/跌价为0且单件成本不适用。普通补录record_type=manual_settlement，赔付为compensation_income；两者source_type=manual_settlement。

补录只写现有SalesImportBatch与SettlementEntry，file_type=manual_settlement、product_id/sale_id为空，不创建Sale、成本或组件分摊。request_key按店铺唯一，同内容重试返回原记录，冲突内容409 manual_settlement_conflict；唯一键竞态回滚新尝试后重读原记录。补录可通过原imports历史与撤销入口查看/撤销，撤销不改库存。旧POST /sales保持库存销售兼容，当前手工表单不再调用。

SaleResponse追加可空只读字段`unit_purchase_cost_cny`、`unit_head_shipping_cost_cny`、`unit_last_mile_cost_cny`、`unit_other_cost_cny`、`unit_total_cost_cny`，均为Decimal，展示4位。四项由未舍入的有效成本分项除本行实际出库数量后ROUND_HALF_UP；不能用已舍入的两位金额反推。总单价由有效FIFO总成本除数量；分项未知但总成本已知时，总单价可保留。作废成本的总单价及无出库事件的全部单价为null，页面分别显示待核对/不适用。原有成本、利润和月度精度不变。

`GET /sales/{sale_id}/cost-breakdown`仅返回当前权限下的库存销售；没有可见来源（含独立财务事件负ID/其他店铺）404 `sale_cost_not_found`，非管理员未绑定仍403。`SaleCostExplanationResponse`含sale、sku、product_name、quantity_basis固定`inventory_units`、cost_batches、impairment_batches、order_summary及order_lines。批次成本含batch_id/no、quantity、有效unit_cost、未舍入四项和total_cost_cny；跌价批次含quantity、有效unit_cost、impairment_units及impairment_cny，解释折算准备数量乘单位成本的实际转销。

整单按`coalesce(sales.store_id,创建人当前store_id)+order_no`读取完整授权出库行，不受页面、月份或商品筛选限制；不会合并不同店铺同号订单。历史店铺无法确定时只返回被选中行，order_summary.complete=false、两项整单金额null并附review_reason，不猜测范围。已知店铺complete=true仅指来源范围完整，金额是否可信仍以null/核对说明为准。成本为完整明细各行分位总额之和，任一作废成本则整单成本待核对；跌价任一未知则整单跌价未知。明细接口上限1000出库行，超限422 `sales_order_too_large`，不返回截断汇总。

整单名称按上述分组店铺ID批量读取Store.name，计算明细响应中的sale/order_lines及order_summary使用同一名称，不借用商品映射推断的店铺；未知店铺的名称保持null。Excel“订单汇总”使用相同名称，不将有明确兼容绑定的历史整单误标成其他店铺或“未关联店铺”。

导出原23列位置保留，另追加4列单件采购/头程/尾程/其他入库成本及交易类型、来源数量，第7列为相同单件总成本口径，单位价格4位、不合计，现共29列。增加“字段口径说明”和“订单汇总”，后者对本次筛选涉及的订单读取完整明细并去重，不与筛选行重复累加；全量关联明细上限20000，超限同样拒绝导出。原筛选权限、20,000匹配行上限、未知金额留空及Excel文本安全规则不变。

页面脚本`/sales-ledger.js`和公开静态资源`/sales-fields.json`由main提供，后者仅包含字段标签/分组/公式/来源/口径，无业务数据、无写入。资源来自[局部zh-CN文件](../../../static/sales-fields.zh-CN.json)，Excel通过[sales_fields.py](../../../services/sales_fields.py)读取相同文件。主业务API仍在登录/店铺权限下读取；静态资源不授予业务访问权限。

## 文件导入

multipart/form-data 的 file 支持 .csv、.xlsx、.xlsm。Excel 必须含“订单详情”，必要列为：结算日期、结算单 ID、付款 ID、状态、货币、交易类型、订单ID/调整单ID、SKU ID、数量，允许规定表头空白归一化。

同一multipart请求新增store_id表单字段（正整数）：管理员必须明确传入有效、启用的店铺，否则400；运营省略时兼容旧客户端，自动取当前绑定店铺，传入他店为403。非法字段类型/非正数422。导入批次、Sale和所有SettlementEntry统一固化该店铺，包括缺映射/库存不足的待确认行。新增错误码为import_store_required/import_store_forbidden/import_store_unavailable；响应字段和全局事件防重规则不变。历史store_id为空的记录不自动回填。

旧 CSV 无表头，格式 SKU,YYYY-MM-DD,数量，金额为0。订单详情另读取净商品销售额、结算总金额、订单创建日期、费用及退款等来源列；必要表头存在不等于各行内容完整。

SalesImportResponse 返回 success、行统计、sales_created、skipped_duplicates、pending_confirmation、import_batch_id、errors、warnings。格式/行校验错误400，汇率异常502，数据库冲突409；成功仍可能有待确认行。没有设置完整上传大小限制。

预分配按来源行销售日期核对批次层，销售日可用量不足时整行进入待确认，不扣部分组件；数据库占位冲突仍按整批回滚处理。组合订单允许多条销售明细，共用order_no但仅第一条占位。详细规则见[销售流程](workflow.md)。

## 待确认、历史与撤销

待确认列表固定分页；导出不接收分页参数，按授权范围导出全部并按 SKU ID/原因归组，金额为原币汇总，不能直接作财务记账结果。赔付类型不进入待确认列表。

待确认导出的“涉及店铺”保留真实店铺名称；缺失归属明确显示“未关联店铺”，与有归属记录合并时也保留该提示，不猜测店铺。

imports 的 limit 默认20、1–100；can_rollback 为 UI 提示，后端重新校验角色、店铺和 completed 状态。rollback 无请求体；已撤销409，越店403，库存恢复异常409；返回恢复件数、删除销售和结算行数。

请求/响应定义见 [schemas.py](../../../schemas.py)，实际异常和兼容行为见 [sales.py](../../../routers/sales.py)。不存在销售编辑/普通删除、单行确认、完整退款恢复或平台同步接口。

## 成本追溯兼容扩展

开关启用后total_cost、profit_cny及成本明细unit_cost含实际成本层，original_total_cost、cost_adjustment_cny、cost_status及明细base_unit_cost用于解释原值与调整。数据库原Sale.profit/total_cost和SaleCostDetail.unit_cost保持初始值；原币profit沿用旧契约。月度汇总同步成本层，归月不变。新接口见[财务增强API](../financial-control/api.md)。
