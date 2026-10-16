# 销售、导入及待确认 API

前缀 /sales，共 9 个端点；非管理员敏感读取按当前绑定店铺。operator 撤销当前店铺文件，admin 可撤销全部。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /sales/import` | [import_sales_file](../../../routers/sales.py) | admin/operator | file | SalesImportResponse |
| `GET /sales/imports` | [list_sales_imports](../../../routers/sales.py) | admin/operator/viewer | limit | list[SalesImportBatchResponse] |
| `POST /sales/imports/{import_batch_id}/rollback` | [rollback_sales_import](../../../routers/sales.py) | admin/operator | import_batch_id | SalesImportRollbackResponse |
| `POST /sales` | [create_sale](../../../routers/sales.py) | admin/operator | SaleCreate | SaleResponse |
| `GET /sales/pending-confirmations` | [list_pending_confirmations](../../../routers/sales.py) | admin/operator/viewer | page、page_size | PendingConfirmationPageResponse |
| `GET /sales/pending-confirmations/export` | [export_pending_confirmations](../../../routers/sales.py) | admin/operator/viewer | 无 | Excel 文件 |
| `GET /sales/monthly-summary` | [monthly_sales_summary](../../../routers/sales.py) | admin/operator/viewer | product_id、keyword、month | list[MonthlySalesSummaryResponse] |
| `GET /sales` | [list_sales](../../../routers/sales.py) | admin/operator/viewer | product_id、keyword、month、page、page_size | list[SaleResponse] \| SalePageResponse |
| `GET /sales/{sale_id}` | [get_sale](../../../routers/sales.py) | admin/operator/viewer | sale_id | SaleResponse |

## 手工销售与查询

SaleCreate：product_id、order_no、quantity>0；store_id 可在模型层为空，但路由要求有效店铺，非管理员使用绑定店铺。selling_price/platform_fee 默认0且≥0，sold_at 可省略。订单号最大64、strip 后不能为空，前置查询已存在订单号返回400，并发占位/数据完整性冲突返回409且回滚；截至销售日可用库存不足返回400。到货日按自然日比较，不能使用销售日之后的批次。order_claim仅为数据库内部字段，不增加请求或响应字段。

GET 列表及 monthly-summary 支持 product_id、keyword≤255、month=YYYY-MM；列表还支持 page/page_size。monthly-summary 按全部筛选结果汇总，不只是当前页，返回数量、人民币收入/成本/费用/毛利与 fx_missing_count。

SaleResponse 包含原始金额、*_cny 字段、汇率依据、店铺和 cost_details。分页列表未指定 product_id 时还可能混入赔付费用行：record_type=other_expense、负 id、quantity=0；它不是普通 sales 主键，不能直接调用 GET /sales/{sale_id}。不分页列表和月度汇总不完全具有同样的赔付行合并行为。

## 文件导入

multipart/form-data 的 file 支持 .csv、.xlsx、.xlsm。Excel 必须含“订单详情”，必要列为：结算日期、结算单 ID、付款 ID、状态、货币、交易类型、订单ID/调整单ID、SKU ID、数量，允许规定表头空白归一化。

旧 CSV 无表头，格式 SKU,YYYY-MM-DD,数量，金额为0。订单详情另读取净商品销售额、结算总金额、订单创建日期、费用及退款等来源列；必要表头存在不等于各行内容完整。

SalesImportResponse 返回 success、行统计、sales_created、skipped_duplicates、pending_confirmation、import_batch_id、errors、warnings。格式/行校验错误400，汇率异常502，数据库冲突409；成功仍可能有待确认行。没有设置完整上传大小限制。

预分配按来源行销售日期核对批次层，销售日可用量不足时整行进入待确认，不扣部分组件；数据库占位冲突仍按整批回滚处理。组合订单允许多条销售明细，共用order_no但仅第一条占位。详细规则见[销售流程](workflow.md)。

## 待确认、历史与撤销

待确认列表固定分页；导出不接收分页参数，按授权范围导出全部并按 SKU ID/原因归组，金额为原币汇总，不能直接作财务记账结果。赔付类型不进入待确认列表。

imports 的 limit 默认20、1–100；can_rollback 为 UI 提示，后端重新校验角色、店铺和 completed 状态。rollback 无请求体；已撤销409，越店403，库存恢复异常409；返回恢复件数、删除销售和结算行数。

请求/响应定义见 [schemas.py](../../../schemas.py)，实际异常和兼容行为见 [sales.py](../../../routers/sales.py)。不存在销售编辑/普通删除、单行确认、完整退款恢复或平台同步接口。
