# 报表 API

2026-11-26：当前`calculation_policy=order_created_fx_mxn8_net_impairment_v4`。店铺损益采用内部管理净列报：`operating_cost`不再抵减转销，`asset_impairment_loss=inventory_impairment-inventory_writeoff`。两个自动源字段仍分别是期间总计提、正数转销，转销行移到跌价区；行键、类型、人工保存和权限不变。本月/上月/本年累计均适用，任一跌价源未知则净影响及营业/净利润为null，已知成本/毛利可保留。新旧策略及行标签随响应保存，旧固定版本不重算或改名；09/10实时导出与页面同源，详见[跌价公式](calculation-formulas.md#损益的存货跌价准备)。

2026-11-24：该阶段`calculation_policy=order_created_fx_mxn8_income_only_v3`；`fulfillment_order_counts`改按交易类型为“订单”的原始订单号在同店同创建月去重，包含指定赠品/未出库订单。字段类型、权限和三个期间保持，旧固定版本不重算。指定赠品正常扣库并计实际销量，财务免商品成本和销售参考跌价；独立结算按订单创建日优先归月。以上订单/费用规则在当前策略继续沿用，完整规则见[流程](workflow.md)。

2026-11-19：当前损益新增只读自动行`compensation_revenue`“其他收入（赔付）”；`product_sales_revenue`排除同笔赔付，`operating_revenue`加回该自动行。已有`other_revenue`继续为人工/台账科目，不改保存接口或旧值。三个期间、金额类型、权限和旧固定版本保持；该阶段响应`calculation_policy=month_end_fx_mxn8_compensation_v2`，当前策略见上文。2026-11-25起自动获取的`exchange_rates[].source`为`Frankfurter/ECB`（人民币为`CNY`），手动报表覆盖为`manual`，日期/汇率仍通过原字段返回；旧固定版本保留原来源，参见[流程](workflow.md)。

2026-11-02：损益响应新增calculation_policy（month_end_fx_mxn8_v1）、exchange_rates及fulfillment_order_counts；每行新增is_reserved。旧字段和三个期间保留，新增字段均有默认值，旧月度版本可读取。查询/保存及Excel使用[现行月度规则](workflow.md)；无需DDL或历史回填。


前缀 /reports，共10个端点。读取允许三种角色，但范围不同；保存损益只允许 admin/operator。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `GET /reports/store-profit-loss` | [get_store_profit_loss](../../../routers/reports.py) | admin/operator/viewer | report_month、store_id | StoreProfitLossResponse |
| `PUT /reports/store-profit-loss` | [update_store_profit_loss](../../../routers/reports.py) | admin/operator | StoreProfitLossUpdateRequest | StoreProfitLossResponse |
| `GET /reports/store-exchange-rate/reference` | [store_exchange_rate_reference](../../../routers/reports.py) | admin/operator/viewer | store_id、report_month、currency | 自动参考汇率 |
| `PUT /reports/store-exchange-rate` | [update_store_exchange_rate](../../../routers/reports.py) | admin/operator | StoreExchangeRateUpdateRequest | store_id/report_month/setting |
| `GET /reports/inventory-impairment` | [inventory_impairment_report](../../../routers/reports.py) | admin/operator/viewer | report_date | 存货明细数组 |
| `GET /reports/inventory-impairment/summary` | [inventory_impairment_summary](../../../routers/reports.py) | admin/operator/viewer | report_date | 全部截止日批次的 SKU 汇总数组 |
| `GET /reports/product-line-profit` | [product_line_profit_report](../../../routers/reports.py) | admin/operator/viewer | report_month、report_date、store_id | 名称保留，按本地商品汇总数组 |
| `GET /reports/inventory-impairment/export` | [export_inventory_impairment_report](../../../routers/reports.py) | admin/operator/viewer | report_date、view=batch/sku（默认batch） | 批次或 SKU Excel 文件 |
| `GET /reports/financial-attachments/export` | [export_financial_attachments](../../../routers/reports.py) | admin/operator/viewer | report_date、report_month、store_id | Excel 文件 |
| `GET /reports/monthly` | [monthly_report](../../../routers/reports.py) | admin/operator/viewer | start_date、end_date | list[MonthlyReportItem] |

## 日期和店铺

- monthly：start_date、end_date，含结束日；非管理员销售按绑定店铺筛选，库存字段为当前全局库存，不是月末库存。
- inventory-impairment、summary 及独立 export：report_date 必填、不得晚于 date.today，当前没有 store_id/店铺过滤；export 未传 view 保持原有批次导出，非法 view 框架校验422。动态结果排除已作废批次；已保存的旧快照和月度版本不覆盖，关联销售利润待核对。
- store-profit-loss：report_month 必须当月第一天；store_id 可空，管理员默认首个有效店铺，非管理员固定绑定店铺且不能请求他店。
- product-line-profit：report_month 必须第一天，report_date 省略则该月末；admin 无 store_id 汇总当前有效店铺，其余通过绑定/选定店铺范围。该入口未采用存货接口同样的未来截止日校验。

product-line-profit已改按本地商品ID分组，不再读取产品线映射决定归类；report_date参数兼容保留，核算期间仍按report_month。保留原name、sku_count及所有金额/比率字段，新增group_by=product、product_id、sku、product_name、row_type和current_quantity。row_type为product/unallocated_settlement/unallocated_fulfillment；后两类商品字段为空，quantity为0。name为“本地SKU · 中文名称”或未归属事项标签。current_quantity为本月实际出库组件件数，不含退款/赔付来源数量；sku_count保留原商品计数含义，不能作为销量使用。金额未知继续null。页面对无group_by=product的旧后端响应提示更新，避免将旧合并行误当逐商品结果。12工作表名称/列数/金额列位置保留，第一、二列分别改为商品编码名称及本月出库数量；汇总数量为出库数合计，公式口径见[流程](workflow.md#产品线跨店汇总与-excel)。无新增表或历史数据改写。
- financial-attachments/export：report_date 必填不得晚于今天；report_month 可省略，默认截止日所在月份第一天；store_id 通过损益范围解析。

日期参数来自 Query/Pydantic，实际业务规则见 [reports.py](../../../routers/reports.py)。没有独立 public accounting-period 路由。

损益中的已作废批次待核对范围包括原销售期间及原归属店铺的历史持有期间。即使当月没有销售，持有期间的`inventory_impairment`和衍生利润仍返回null，并在warnings中说明，不能按0确认费用；本月、上月、年累计分别判断，区间与时区边界见[报表流程](workflow.md)。接口字段及旧版本payload保持兼容，本次不回填历史金额。

## 存货明细金额契约

`GET /reports/inventory-impairment` 保留已有字段及四个入库/出库追溯字段；算法依据见[流程](workflow.md)，完整26个响应字段（25个显示列及comparison_note）的血缘见[逐字段说明](inventory-impairment-fields.md#页面25列完整对照)：

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
| `monthly_transfer_in_amount` | 本月归属变更转入的剩余准备，不作为计提费用 |
| `monthly_transfer_out_amount` | 本月归属变更转出的剩余准备，不作为销售转销或费用 |
| `status` | 异常优先为历史流水待核对；其他依次为已售完、安全库存内、比例上限/接近上限、正常 |

2026-10-15追加持有期间计提/转销：销售当天先计提，售完期末余额0，但本月总计提和转销可非0。旧字段保留语义并新增三项变动。历史时序/数量异常相关金额为null，不填0；上月按同一回放计算，金额/比较值未知按列传播至SKU及合计。

`GET /reports/inventory-impairment/summary` 返回21字段：store_name、sku、product_name、batch_count，上述四个追溯字段，以及 quantity、provision_quantity、inventory_amount、impairment_amount、monthly_impairment_amount、book_value、book_value_difference、comparison_note及上述三项变动和准备转入/转出。按 product_id 汇总，不合计单价/库龄/比例。包含已售完旧批次，和明细的范围一致；任一准备或比较值未知则该SKU对应合计为null。完整范围和例子见[SKU 汇总说明](inventory-impairment-fields.md#sku汇总与成本核对)。

## 损益保存契约

店铺损益查询和保存响应中的 `salary_oa_breakdown` 提供 `china_salary`、`individual_income_tax`、`employer_contributions` 和 `total` 的 `current/previous/ytd` 人民币金额。它用于核对 OA 原值，最终薪资仍取 `rows` 中 `salary.value`。保存请求只接收人工科目和备注。旧月度版本可缺少此字段。

`shared_admin`（平摊人事＋财务管理费用）与`salary`一样是允许保存的人工科目，响应`kind="manual"`、`is_auto=false`。`manual_values.shared_admin.current`为本店本月覆盖总额，0有效，null恢复OA默认；`admin_expenses`仍是公式科目，不能直接保存。两项的`value`是最终金额，`manual_value`只含实际保存输入；前端把未填写的OA默认显示为占位值并提交null。上月/年累计及Excel逐月采用人工值或OA默认，接口字段和金额类型不变，已有客户端应根据kind渲染。

StoreProfitLossUpdateRequest：store_id、report_month、manual_values、remarks，以及可选update_scope（all/current，默认all）。金额单元StoreProfitLossCell有current/previous/ytd，可空；只保存允许的人工科目，自动/公式键忽略。默认all仍整体替换人工JSON，须提交完整要保留的值；current只替换本月人工列，保留原previous/ytd存储并忽略请求比较输入，未提交的本月人工科目清空为null；metadata标记monthly后，响应比较列根据各月current及OA月默认计算，上月取实际月记录、年累计自动汇总。all模式明确输入的比较值保留兼容，缺少时才推导。当前页面显式提交current，避免只编辑本月却清空比较值；OA薪资默认金额仅显示为输入框占位值，未人工填写时提交null，不会把本月/比较期间OA默认金额固化为人工值。已有报表保存时加行锁保护读取及合并。

remarks在两种scope下均为完整替换，只接受已定义科目、去空白后最多500字符。权限、店铺、月份校验不变；非法update_scope返回422。字段定义见[schemas.py](../../../schemas.py)，保存实现为[update_store_profit_loss](../../../routers/reports.py)。

响应StoreProfitLossResponse含warnings数组，说明受影响期间的历史/归属异常；相关自动金额及衍生利润为null。inventory_writeoff自动科目抵减营业成本，inventory_impairment取期间总计提，不用净余额差。其他响应含店铺、月份、can_edit、updated_at，以及每行 key/label/kind/is_auto/is_formula/source/value/manual_value/remark。value为最终计算金额；人工行的manual_value只含实际保存的人工覆盖，未保存时为null；自动/公式行为null。两者不能混作保存输入。没有单独的审批/结账状态，公式由后端生成。UI 展示本月与只读本年累计，只有本月人工项目可编辑；API和单店Excel仍提供三个期间。每行追加is_reserved，响应追加exchange_rates（币种/月/请求月末日/实际日期/人民币汇率/来源/状态）与fulfillment_order_counts（各期间去重履约订单数）；新字段可缺省，旧版本仍可读取。

## 导出与副作用

独立存货导出是全局；财务附件保留已有工作表顺序，最后追加 07-存货SKU汇总表，admin 五张、非管理员四张且无跨店损益汇总。admin 单店损益为所选店铺、两种存货视图为全局、损益汇总和产品线为当前启用店铺范围；非管理员两种存货视图均按绑定店铺过滤。

07存货查询/导出不再调用ensure_period_snapshot，仅读取并计算；应用启动与后台仍有迁移/快照写入，不能用启动代替只读验证。OA 完整配置的读取失败抛异常，汇率历史缺失统一传播到相应收入/费用、利润和跨店/产品线合计，细节见[流程](workflow.md)。

输入日期不合规400，越店/无绑定403，无店铺404，框架校验422；并未定义统一报表外部数据错误格式。Excel为StreamingResponse，工作簿公式缓存已有处理。回归覆盖库存余额/本月增提、模拟会话、内存工作簿和权限；本地核验与生产部署分别记录，生产接口和原始历史完整性仍待验证。损益字段契约见[schemas.py](../../../schemas.py)。

## 财务来源兼容扩展

损益行增加input_source=manual/ledger（默认manual）；manual_value仍为原人工备选值，value是选定来源的计算结果。保存损益时不会覆盖ledger科目的原人工备选值。实际成本补差通过查询层进入现有存货/损益/产品线/Excel；新凭证、来源选择、版本/比较及冻结导出见[财务增强API](../financial-control/api.md)。

## 手动汇率接口

`GET /reports/store-exchange-rate/reference`接收必填store_id、report_month（月首）、currency（三位大写代码），按报表店铺权限返回自动参考的currency/report_month/requested_date/rate_date/rate_to_cny/source/status。它不读取手动设置；未结束月为month_not_closed，缺率为missing，不写数据库。

`PUT /reports/store-exchange-rate`接收store_id、report_month、currency、mode（auto/manual）、manual_rate、note（可选，最多500字）、expected_revision（首次0）。manual_rate建议传十进制字符串，正数、最多18位有效数字且最多8位小数；manual必须提供，auto必须省略或为null，CNY不可覆盖。成功只返回store_id/report_month/setting，保存与重新查询报表分开，避免外部重算失败被误认为保存失败。

setting含currency/mode/manual_rate/revision/note/updated_at/updated_by/updated_by_user_id；manual_rate以普通十进制字符串保存并去除小数末尾零（例如0.371100000返回0.3711），不舍入有效数字、不返回科学计数法。旧设置读取时采用同一格式，不批量改写历史metadata；updated_at为UTC ISO时间。未保存时mode=auto、revision=0。月首/未来月份不合规返回400与code=report_fx_month_invalid，权限不符403，修订冲突409与code=report_fx_conflict，金额/模式格式不合规422。失败不覆盖原设置。

损益响应新增默认字段supports_manual_fx=true及exchange_rate_settings=[]，设置列表含本月使用币种、店铺结算币种和已配置币种，排除CNY。exchange_rates继续保留原字段，追加mode/store_id/is_provisional和设置依据；手动时source=manual、rate_date=null，不冒充外部报价日期。未知旧字段不影响读取，旧版本仍以保存payload导出。前端旧后端无supports_manual_fx时隐藏写入入口。现有损益保存与汇率保存互相保留对方字段；不改变calculation_policy的原订单/赠品/费用计算规则。
