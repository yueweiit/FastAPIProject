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

`GET /reports/inventory-impairment` 保留已有字段及四个入库/出库追溯字段；算法依据见[流程](workflow.md)，完整24个响应字段（23个显示列及comparison_note）的血缘见[逐字段说明](inventory-impairment-fields.md#页面23列完整对照)：

| 字段 | 口径 |
| --- | --- |
| `original_quantity` | 批次保存的原始入库数量；不是本月期初数量 |
| `original_inventory_amount` | 原始入库数量×批次单件成本（含物流/其他成本） |
| `sold_quantity` | 该批次从入库至截止日已确认销售出库数量 |
| `sold_cost_amount` | 同期间销售成本明细数量×明细保存的单价之和 |
| `impairment_amount` | 截止日现存计提数量×批次单价×累计比例，即期末准备余额；沿用规则历史、安全库存及90%上限 |
| `monthly_impairment_amount` | 截止日仍在库商品本月新增准备，按规则段的本月比例增量及上限计算，不含已售商品 |
| `book_value` | 现存库存成本减期末累计准备，售完为0 |
| `book_value_difference` | 本期账面减按相同余额规则重建的上月账面，含出入库影响；本月新增批次上月为0，不依赖快照 |
| `comparison_note` | 说明本月新增批次上月账面为0，正常历史批次为空字符串 |
| `opening_impairment_amount` | 上月末准备余额，本月新批为0 |
| `monthly_total_impairment_amount` | 本月实际新增准备，含已售部分售出前增提 |
| `monthly_writeoff_amount` | 本月销售转出对应全部准备，可能含期初准备 |
| `status` | 异常优先为历史流水待核对；其他依次为已售完、安全库存内、比例上限/接近上限、正常 |

2026-10-15追加持有期间计提/转销：销售当天先计提，售完期末余额0，但本月总计提和转销可非0。旧字段保留语义并新增三项变动。历史时序/数量异常相关金额为null，不填0；上月按同一回放计算，金额/比较值未知按列传播至SKU及合计。

`GET /reports/inventory-impairment/summary` 返回19字段：store_name、sku、product_name、batch_count，上述四个追溯字段，以及 quantity、provision_quantity、inventory_amount、impairment_amount、monthly_impairment_amount、book_value、book_value_difference、comparison_note及上述三项新增变动。按 product_id 汇总，不合计单价/库龄/比例。包含已售完旧批次，和明细的范围一致；任一准备或比较值未知则该SKU对应合计为null。完整范围和例子见[SKU 汇总说明](inventory-impairment-fields.md#sku汇总与成本核对)。

## 损益保存契约

StoreProfitLossUpdateRequest：store_id、report_month、manual_values、remarks，以及可选update_scope（all/current，默认all）。金额单元StoreProfitLossCell有current/previous/ytd，可空；只保存允许的人工科目，自动/公式键忽略。默认all仍整体替换人工JSON，须提交完整要保留的值；current只替换本月人工列，保留数据库中已有previous/ytd，忽略请求中的比较期间值，未提交的本月人工科目清空为null。当前页面显式提交current，避免只编辑本月却清空比较值；OA薪资默认金额仅显示为输入框占位值，未人工填写时提交null，不会把本月/比较期间OA默认金额固化为人工值。已有报表保存时加行锁保护读取及合并。

remarks在两种scope下均为完整替换，只接受已定义科目、去空白后最多500字符。权限、店铺、月份校验不变；非法update_scope返回422。字段定义见[schemas.py](../../../schemas.py)，保存实现为[update_store_profit_loss](../../../routers/reports.py)。

响应StoreProfitLossResponse含warnings数组，说明受影响期间的历史/归属异常；相关自动金额及衍生利润为null。inventory_writeoff自动科目抵减营业成本，inventory_impairment取期间总计提，不用净余额差。其他响应含店铺、月份、can_edit、updated_at，以及每行 key/label/kind/is_auto/is_formula/source/value/manual_value/remark。value为最终计算金额；人工行的manual_value只含实际保存的人工覆盖，未保存时为null；自动/公式行为null。两者不能混作保存输入。没有单独的审批/结账状态，公式由后端生成。UI 当前仅展示/编辑本月金额，API 和单店 Excel 仍提供三个期间。

## 导出与副作用

独立存货导出是全局；财务附件保留已有工作表顺序，最后追加 07-存货SKU汇总表，admin 五张、非管理员四张且无跨店损益汇总。admin 单店损益为所选店铺、两种存货视图为全局、损益汇总和产品线为当前启用店铺范围；非管理员两种存货视图均按绑定店铺过滤。

07存货查询/导出不再调用ensure_period_snapshot，仅读取并计算；应用启动与后台仍有迁移/快照写入，不能用启动代替只读验证。OA 完整配置的读取失败抛异常，汇率历史缺失在不同报表中处理不同，细节见[流程](workflow.md)。

输入日期不合规400，越店/无绑定403，无店铺404，框架校验422；并未定义统一报表外部数据错误格式。Excel为StreamingResponse，工作簿公式缓存已有处理。回归覆盖库存余额/本月增提、模拟会话、内存工作簿和权限；本地核验与生产部署分别记录，生产接口和原始历史完整性仍待验证。损益字段契约见[schemas.py](../../../schemas.py)。
