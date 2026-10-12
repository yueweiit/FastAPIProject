# 报表 API

前缀 /reports，共8个端点。读取允许三种角色，但范围不同；保存损益只允许 admin/operator。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `GET /reports/store-profit-loss` | [get_store_profit_loss](../../../routers/reports.py) | admin/operator/viewer | report_month、store_id | StoreProfitLossResponse |
| `PUT /reports/store-profit-loss` | [update_store_profit_loss](../../../routers/reports.py) | admin/operator | StoreProfitLossUpdateRequest | StoreProfitLossResponse |
| `GET /reports/inventory-impairment` | [inventory_impairment_report](../../../routers/reports.py) | admin/operator/viewer | report_date | 存货明细数组 |
| `GET /reports/inventory-impairment/summary` | [inventory_impairment_summary](../../../routers/reports.py) | admin/operator/viewer | report_date | 全部截止日批次的 SKU 汇总数组 |
| `GET /reports/product-line-profit` | [product_line_profit_report](../../../routers/reports.py) | admin/operator/viewer | report_month、report_date、store_id | 产品线汇总数组 |
| `GET /reports/inventory-impairment/export` | [export_inventory_impairment_report](../../../routers/reports.py) | admin/operator/viewer | report_date、view=batch/sku（默认batch） | 批次或 SKU Excel 文件 |
| `GET /reports/financial-attachments/export` | [export_financial_attachments](../../../routers/reports.py) | admin/operator/viewer | report_date、report_month、store_id | Excel 文件 |
| `GET /reports/monthly` | [monthly_report](../../../routers/reports.py) | admin/operator/viewer | start_date、end_date | list[MonthlyReportItem] |

## 日期和店铺

- monthly：start_date、end_date，含结束日；非管理员销售按绑定店铺筛选，库存字段为当前全局库存，不是月末库存。
- inventory-impairment、summary 及独立 export：report_date 必填、不得晚于 date.today，当前没有 store_id/店铺过滤；export 未传 view 保持原有批次导出，非法 view 框架校验422。
- store-profit-loss：report_month 必须当月第一天；store_id 可空，管理员默认首个有效店铺，非管理员固定绑定店铺且不能请求他店。
- product-line-profit：report_month 必须第一天，report_date 省略则该月末；admin 无 store_id 汇总当前有效店铺，其余通过绑定/选定店铺范围。该入口未采用存货接口同样的未来截止日校验。
- financial-attachments/export：report_date 必填不得晚于今天；report_month 可省略，默认截止日所在月份第一天；store_id 通过损益范围解析。

日期参数来自 Query/Pydantic，实际业务规则见 [reports.py](../../../routers/reports.py)。没有独立 public accounting-period 路由。

## 存货明细金额契约

`GET /reports/inventory-impairment` 保留已有字段，新增四个入库/出库追溯字段；算法依据见[流程](workflow.md)，完整 21 个响应字段（20 个显示列及 comparison_note）的血缘见[逐字段说明](inventory-impairment-fields.md#页面-20-列完整对照)：

| 字段 | 口径 |
| --- | --- |
| `original_quantity` | 批次保存的原始入库数量；不是本月期初数量 |
| `original_inventory_amount` | 原始入库数量×批次单件成本（含物流/其他成本） |
| `sold_quantity` | 该批次从入库至截止日已确认销售出库数量 |
| `sold_cost_amount` | 同期间销售成本明细数量×明细保存的单价之和 |
| `impairment_amount` | 各日销售后库存按各日累计比例计算的金额逐日相加，保留售完前金额，不按成本封顶 |
| `monthly_impairment_amount` | 本月1日至截止日每天金额之和，包含当月已售商品售前金额 |
| `book_value` | 原始入库成本金额减逐日累计跌价，财务自定义口径，保留负数 |
| `book_value_difference` | 当前自定义账面价值减按相同每日口径重算的上月值；本月新增批次上月为0，不依赖准备余额快照 |
| `comparison_note` | 说明本月新增批次上月自定义账面为0，正常历史批次为空字符串 |
| `status` | 售完批次为“已售完”；其他按安全库存/比例状态显示 |

2026-10-12最终确认改变上述金额字段语义，JSON字段名保留；页面/Excel明确标注逐日累计及财务自定义账面。明细、汇总和导出均包含全部已到货批次，已售完批次金额不会清零。正常上月值从现有流水重算；意外缺失比较值仍传播null。店铺损益继续使用原准备余额差，见[流程](workflow.md)。

`GET /reports/inventory-impairment/summary` 返回 16 字段：store_name、sku、product_name、batch_count，上述四个追溯字段，以及 quantity、provision_quantity、inventory_amount、impairment_amount、monthly_impairment_amount、book_value、book_value_difference、comparison_note。按 product_id 汇总，不合计单价/库龄/比例。包含已售完旧批次，和明细的范围一致；任一比较值未知则该SKU差额仍为null。完整范围和例子见[SKU 汇总说明](inventory-impairment-fields.md#sku-汇总与成本核对)。

## 损益保存契约

StoreProfitLossUpdateRequest：store_id、report_month、manual_values、remarks。金额单元 StoreProfitLossCell 有 current/previous/ytd，可空；只保存允许的人工科目，自动/公式键忽略。备注只接受已定义科目、去空白后最多500字符。保存替换原 JSON，应提交完整要保留的人工值。

响应 StoreProfitLossResponse 含店铺、月份、can_edit、updated_at，以及每行 key/label/kind/is_auto/is_formula/source/value/remark。没有单独的审批/结账状态，公式由后端生成。UI 当前仅展示/编辑本月金额，API 和单店 Excel 仍提供三个期间。

## 导出与副作用

独立存货导出是全局；财务附件保留已有工作表顺序，最后追加 07-存货SKU汇总表，admin 五张、非管理员四张且无跨店损益汇总。admin 单店损益为所选店铺、两种存货视图为全局、损益汇总和产品线为当前启用店铺范围；非管理员两种存货视图均按绑定店铺过滤。

07存货查询/导出不再调用ensure_period_snapshot，仅读取并计算；应用启动与后台仍有迁移/快照写入，不能用启动代替只读验证。OA 完整配置的读取失败抛异常，汇率历史缺失在不同报表中处理不同，细节见[流程](workflow.md)。

输入日期不合规400，越店/无绑定403，无店铺404，框架校验422；并未定义统一报表外部数据错误格式。Excel 为 StreamingResponse，工作簿公式缓存已有处理。回归包含逐日公式、模拟会话、内存工作簿和权限；本地备份核验与生产部署应分别记录，生产接口和原始历史完整性仍待验证。损益字段契约见 [schemas.py](../../../schemas.py)。
