# 销售、导入及待确认 API

前缀 /sales，共 14 个端点；非管理员敏感读取按当前绑定店铺。operator 撤销当前店铺文件，admin 可撤销全部。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /sales/import` | [import_sales_file](../../../routers/sales.py) | admin/operator | file、store_id | SalesImportResponse |
| `POST /sales/import-stream` | import_sales_stream → _import_sales_file | admin/operator | 同上；不接收导入月份 | NDJSON阶段进度与最终结果 |
| `GET /sales/imports` | [list_sales_imports](../../../routers/sales.py) | admin/operator/viewer | limit | list[SalesImportBatchResponse] |
| `POST /sales/imports/{import_batch_id}/rollback` | [rollback_sales_import](../../../routers/sales.py) | admin/operator | import_batch_id | SalesImportRollbackResponse |
| `POST /sales/manual-settlements` | create_manual_settlement | admin/operator | 店铺、来源字段、人民币金额、request_key | 不扣库存的结算补录SaleResponse |
| `POST /sales` | [create_sale](../../../routers/sales.py) | admin/operator | SaleCreate | SaleResponse |
| `GET /sales/pending-confirmations` | [list_pending_confirmations](../../../routers/sales.py) | admin/operator/viewer | page、page_size、reason、keyword | PendingConfirmationPageResponse |
| `GET /sales/pending-confirmations/export` | [export_pending_confirmations](../../../routers/sales.py) | admin/operator/viewer | reason、keyword | 筛选后的Excel文件 |
| `POST /sales/pending-confirmations/retry` | [retry_pending_confirmations](../../../routers/sales.py) | admin/operator | PendingRetryRequest | PendingRetryResponse |
| `GET /sales/monthly-summary` | [monthly_sales_summary](../../../routers/sales.py) | admin/operator/viewer | product_id、keyword、month | list[MonthlySalesSummaryResponse] |
| `GET /sales` | [list_sales](../../../routers/sales.py) | admin/operator/viewer | product_id、keyword、month、page、page_size | list[SaleResponse] \| SalePageResponse |
| `GET /sales/export` | [export_sales_records](../../../routers/sales.py) | admin/operator/viewer | product_id、keyword、month | 全部匹配台账的Excel文件 |
| `GET /sales/{sale_id}/cost-breakdown` | [sale_cost_breakdown](../../../routers/sales.py) | admin/operator/viewer | sale_id | SaleCostExplanationResponse：本行代入、FIFO批次及完整订单汇总 |
| `GET /sales/{sale_id}` | [get_sale](../../../routers/sales.py) | admin/operator/viewer | sale_id | SaleResponse |

## 订单号展示兼容

`SaleResponse`及`SalesOrderCostSummary`追加可空只读`display_order_no`，页面与Excel优先使用该字段，缺少时回退原`order_no`。导入来源明确保留`raw_data["订单ID/调整单ID"]`时返回原编号；手工/旧CSV或缺少来源时返回保存编号，不按连字符拆分。列表、单条、计算明细（含order_lines/order_summary）与导出一致。原`order_no`继续返回内部标识，读写接口、订单+SKU防重、店铺权限和汇总范围不变，不迁移/改写历史数据；不同来源SKU显示同一原订单号时仍是独立分组。

销售只读响应追加可空Decimal字段`source_row_net_product_sales`、`source_row_settlement_total`，直接读取关联原始结算行的原币净商品销售额与结算总金额。组合各组件重复展示完整来源行金额，不得跨组件累加；人民币金额仍按原有组件份额计算。无原始结算行时为null；缺失汇率不影响原币展示。独立退款、赔付及手工结算保留实际来源值；币种、保存汇率、实际汇率日期及来源复用既有字段。GET /sales（数组/分页）、GET /sales/{id}、计算明细及导出一致；无写接口、数据库、权限或汇率获取策略变更。

## 待确认筛选与原记录处理

GET及导出新增可选`reason`稳定码：stock_shortage/product_missing/product_ambiguous/expression_invalid/mapping_invalid/order_conflict/source_invalid/other；未知参数422。keyword最长255，匹配订单、SKU ID、产品名、SKU名称、文件名及原因文本。原page/page_size和店铺范围保持不变。列表追加reason_counts（权限/关键字范围的各分类数量），每项追加reason_code、confirmed_components（此前人工确认的product_id/multiplier）。旧字段仍保留；分类为动态字段，无DDL或历史回填。

POST请求可以传`entry_ids`（正ID、去重、1–200项），或省略ID并传reason/keyword处理当前可见筛选全部记录；后一种超过200条422 `pending_retry_too_large`，不会只处理前200条。显式ID不叠加筛选，任一不存在/无权访问404 `pending_not_found`，不部分越权执行。可选`components: [{product_id>0,multiplier:1..2147483647}]`最多50项，只允许恰好一个entry_id，表示确认该行本地组件；非法或已删除商品422。重复组件按商品ID合并，件数超过Integer范围仍待确认。

响应含confirmed_rows/pending_rows/skipped_rows/sales_created及逐行results（id、status=confirmed/pending/skipped、reason_code、message）。HTTP200不等于全部完成，需检查pending_rows；未解决业务原因保留原行并更新原因，已确认行重复请求只跳过。已撤销来源409 `pending_import_rolled_back`，来源并发变化409 `pending_source_changed`，唯一键/完整性竞态409 `pending_retry_conflict`且整次回滚。人工确认仅影响该来源行，不更改原source_key和金额/日期/汇率。原导入统计随成功结果更新，原文件撤销仍能恢复此次生成的销售库存；已有财务引用时按原保护拒绝物理撤销。事务、审计及不可变版本边界见[流程](workflow.md#待确认与文件撤销)。

新增静态`/sales-pending.js`供两份首页共用，先于后续销售加载使用；源脚本未加载时保留旧只读待确认展示。需要加载新增后端与脚本后才能使用筛选/处理功能，不通过启动服务执行预检。

## 导入进度流

页面调用`POST /sales/import-stream`，原`POST /sales/import`的JSON契约不变，两者共用`_import_sales_file`，无新增业务表。Cookie/CSRF及admin/operator权限沿用原依赖；管理员必须指定有效店铺，运营只能导入绑定店铺。认证/请求校验失败可直接返回HTTP错误；开始流后HTTP状态为200，必须读取最后一行`type=result`的`status/data`判断业务结果，不能凭HTTP 200判断导入成功。

响应为`application/x-ndjson`，每行一个JSON：`type=progress`含稳定阶段码`parsing/validating/exchange_rates/inventory/writing/committing`及可空`processed/total`；行数表示本阶段真实处理行数，不是预计耗时或已提交数量。`keepalive`仅保活。最终`result`包含业务HTTP状态和原SalesImportResponse或错误体；仅事务提交成功后产生成功结果。失败不发送成功结果，校验/权限/冲突/汇率失败沿用400/403/409/502。系统异常返回500 `sales_import_failed`，不返回异常内部数据。

上传字节在请求结束前读完；独立任务自持数据库会话，沿用原行锁顺序、幂等及整批事务。慢客户端的旧进度合并，不阻塞库存事务。浏览器断线/退出只失去实时进度，已受理任务可继续；无后台任务列表/断点续传接口，须在导入历史核对结果后决定是否重试，前端不会自动重传。进度不是持久化作业队列，服务重启后以实际数据库结果及导入历史为准。

文件按原日期自动归月：库存销售以订单创建日期、缺少时结算日期；独立结算以结算日期、缺少时订单创建日期、均缺少时导入时间。页面month只用于查询/导出，不覆盖文件日期。同一文件可跨月，不能根据当前查询月份过滤上传行。

## 手工销售与查询口径

统一搜索框“商品名称 / SKU / 订单号”继续使用原`keyword≤255`，支持完整或部分原始订单号，不新增参数。商品销售同时匹配本地名称/SKU、来源SKU/产品名及来源订单号（order_id和raw_data明确保存的编号），并保留内部Sale.order_no/手工旧编号查询；独立退款、调整、赔付及手工结算也按原订单号匹配。列表（数组/分页）、月度统计、Excel记录及导出计数复用筛选，month/product_id和服务端店铺范围继续共同生效；不因搜索订单号放宽权限或改变内部成本分组。

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
| `impairment_cny` | Decimal / null，完整计算精度 | 出库商品按订单库龄公式计算的跌价参考金额，独立于成本及毛利 |
| `impairment_review_reason` | string / null | 跌价待核对或历史规则推定说明，已知参考金额也可附说明 |

非库存事件上述金额为0；有出库但未知的分项/跌价为null。字段按实际本地组件数量计算，不对组合订单重复返回整单总额；不得把 `impairment_cny` 直接作为销售店铺跌价费用或准备转销。服务端先校验现有店铺权限，只读取获准销售引用的批次及商品类型历史；公式及缺历史兼容见[出库成本流程](workflow.md#出库成本拆分与跌价参考)。

跌价日期另读取获准销售的分摊/直接履约来源：订单详情的来源创建日缺失或冲突时，`impairment_cny`及明细的`order_date/age_days/product_type/daily_rate`为null，通过`impairment_review_reason`说明；已知成本继续展示。手工、无履约来源的历史销售和legacy_csv使用显式保存日期。`SaleResponse.sold_at`仍为原销售归属日，不因本次读取重写或重分核算月份；正常订单可在计算明细看到真实来源日期。

`GET /sales/export`沿用列表的`month=YYYY-MM`、`keyword≤255`和可选`product_id>0`筛选；月份为空导出全部月份，keyword首尾空白忽略，商品ID与关键字按AND组合。没有page/page_size限制，不按当前页截断。admin读取全部店铺，operator/viewer固定当前绑定店铺，无绑定403；不接受以store_id切换范围。静态路径先于`/{sale_id}`注册，不改变已有详情接口。

接口复用`list_sales`的不分页响应，包含相同的独立退款/调整/赔付事件及0数量、固化汇率、成本追溯和待核对空值；商品名称/SKU批量读取，无逐记录查询。先用相同权限/筛选SQL统计销售及独立事件，超过20,000条返回422 `sales_export_too_large`（含max_rows），不截断；空结果404 `sales_export_empty`。非法月份沿用400/422校验，商品ID或关键字长度错误422。

文件包含“销售记录”和“说明”：原23列保留顺序，现追加4列单件分项及交易类型、来源数量共29列；前18列保留原位置（第9列展示本行订单总成本），追加采购、头程、尾程、其他入库成本及出库商品跌价参考。数量/收入/成本/费用/毛利、四项成本及跌价提供全部导出记录合计，未知金额及对应合计为空；单价和汇率不合计。所有用户文本按Excel文本写入，保留前导零并防止公式注入。“说明”记录月份、关键字、可选商品ID、权限范围、分项舍入及跌价独立口径。附件名`sales_YYYYMM.xlsx`，无月份为`sales_all.xlsx`，MIME为application/vnd.openxmlformats-officedocument.spreadsheetml.sheet。

只SELECT，不扣库、改成本、补快照或写导出记录。取数完成后释放只读事务，在线程生成文件。新接口须加载更新后的后端；前端未加载接口时会明确提示，不把错误网页下载为Excel。页面行为见[流程](workflow.md#销售台账导出)。

## 单件成本与计算明细

SaleResponse增加`transaction_type`、`source_sku_id`、`source_product_name`、`source_quantity`及`source_type`，从保存的结算来源读取，原product_id仍为本地成本组件。display_sku保留来源SKU ID；display_name优先取raw_data的中文“产品名”表达式，历史缺列时取已经关联的本地商品名称，否则保留保存名称。手工结算补录显示填写值；没有来源的旧手工库存销售仍兼容本地商品展示。source_product_name保留原保存名称，不覆盖历史平台标题。SKU ID按文本保留前导零及长编号；keyword同时匹配来源SKU ID、保存名称、原始“产品名”、交易类型和本地商品，列表/月度/导出采用同一筛选。计算明细展示本地组件，并附来源表达式与数量。旧版只有直接sale_id的来源也可读取，不补写关联，不重新扣库或计算历史成本。

`POST /sales/manual-settlements`为人民币独立结算补录：接收store_id、request_key（16至64位字母/数字/下划线/连字符）、occurred_on日期、order_no≤128、sku_id≤128、product_name≤500、transaction_type≤32、整数quantity、net_product_sales及settlement_total（16位/4位小数，允许负数）。管理员须指定有效店铺，运营只能写绑定店铺，viewer禁止。响应quantity固定0、source_quantity为录入值，成本/跌价为0且单件成本不适用。普通补录record_type=manual_settlement，赔付为compensation_income；两者source_type=manual_settlement。

补录只写现有SalesImportBatch与SettlementEntry，file_type=manual_settlement、product_id/sale_id为空，不创建Sale、成本或组件分摊。request_key按店铺唯一，同内容重试返回原记录，冲突内容409 manual_settlement_conflict；唯一键竞态回滚新尝试后重读原记录。补录可通过原imports历史与撤销入口查看/撤销，撤销不改库存。旧POST /sales保持库存销售兼容，当前手工表单不再调用。

SaleResponse追加可空只读字段`unit_purchase_cost_cny`、`unit_head_shipping_cost_cny`、`unit_last_mile_cost_cny`、`unit_other_cost_cny`、`unit_total_cost_cny`，均为Decimal，展示4位。四项由未舍入的有效成本分项除本行实际出库数量后ROUND_HALF_UP；不能用已舍入的两位金额反推。总单价由有效FIFO总成本除数量；分项未知但总成本已知时，总单价可保留。作废成本的总单价及无出库事件的全部单价为null，页面分别显示待核对/不适用。原有成本、利润和月度精度不变。

`GET /sales/{sale_id}/cost-breakdown`仅返回当前权限下的库存销售；没有可见来源（含独立财务事件负ID/其他店铺）404 `sale_cost_not_found`，非管理员未绑定仍403。`SaleCostExplanationResponse`含sale、sku、product_name、quantity_basis固定`inventory_units`、cost_batches、impairment_batches、order_summary及order_lines。批次成本含batch_id/no、quantity、有效unit_cost、未舍入四项和total_cost_cny。跌价批次追加可空只读字段`arrival_date/order_date`（自然日日期）、`age_days`（整数）、`product_type`（订单日类型）、`daily_rate`（Decimal）和`total_cost_cny`（本批出库有效成本）；`impairment_cny=age_days×daily_rate×total_cost_cny`。旧`quantity/unit_cost/impairment_units`保留兼容，impairment_units现为`quantity×age_days×daily_rate`的折算量，不表示准备转销数量；异常时金额及折算量为空。无新增持久化字段，旧客户端可忽略追加字段。

整单按`coalesce(sales.store_id,创建人当前store_id)+order_no`读取完整授权出库行，不受页面、月份或商品筛选限制；不会合并不同店铺同号订单。历史店铺无法确定时只返回被选中行，order_summary.complete=false、两项整单金额null并附review_reason，不猜测范围。已知店铺complete=true仅指来源范围完整，金额是否可信仍以null/核对说明为准。成本为完整明细各行分位总额之和，任一作废成本则整单成本待核对；跌价任一未知则整单跌价未知。明细接口上限1000出库行，超限422 `sales_order_too_large`，不返回截断汇总。

整单名称按上述分组店铺ID批量读取Store.name，计算明细响应中的sale/order_lines及order_summary使用同一名称，不借用商品映射推断的店铺；未知店铺的名称保持null。Excel“订单汇总”使用相同名称，不将有明确兼容绑定的历史整单误标成其他店铺或“未关联店铺”。

导出原23列位置保留，另追加4列单件采购/头程/尾程/其他入库成本及交易类型、来源数量，第7列为相同单件总成本口径，单位价格4位、不合计，现共29列。增加“字段口径说明”和“订单汇总”，后者对本次筛选涉及的订单读取完整明细并去重，不与筛选行重复累加；全量关联明细上限20000，超限同样拒绝导出。原筛选权限、20,000匹配行上限、未知金额留空及Excel文本安全规则不变。

页面脚本`/sales-ledger.js`和公开静态资源`/sales-fields.json`由main提供，后者仅包含字段标签/分组/公式/来源/口径，无业务数据、无写入。资源来自[局部zh-CN文件](../../../static/sales-fields.zh-CN.json)，Excel通过[sales_fields.py](../../../services/sales_fields.py)读取相同文件。主业务API仍在登录/店铺权限下读取；静态资源不授予业务访问权限。

## 文件导入

纯运费调整、全额退款冲账即使来源交易类型为“订单”且数量大于0，也可按金额证据仅保存结算，不创建销售/扣库存；不改原交易类型和来源数量。台账响应为既有settlement_adjustment，quantity=0、成本为0，费用仍为净商品销售额减结算总金额。按结算日期优先归月，列表/月度/计数/导出和店铺损益共用公共财务查询；详细识别条件见[文件导入](workflow.md#文件导入)，未增加公开字段。正常货品销售的订单/SKU在同一文件重复时，返回400及errors中的具体行号，尚未写入业务数据；已有数据库订单及并发冲突保护不变。未根据零收入或金额正负推断退货入库。

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
