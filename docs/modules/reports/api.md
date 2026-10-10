# 报表 API

前缀 /reports，共7个端点。读取允许三种角色，但范围不同；保存损益只允许 admin/operator。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `GET /reports/store-profit-loss` | [get_store_profit_loss](../../../routers/reports.py) | admin/operator/viewer | report_month、store_id | StoreProfitLossResponse |
| `PUT /reports/store-profit-loss` | [update_store_profit_loss](../../../routers/reports.py) | admin/operator | StoreProfitLossUpdateRequest | StoreProfitLossResponse |
| `GET /reports/inventory-impairment` | [inventory_impairment_report](../../../routers/reports.py) | admin/operator/viewer | report_date | 存货明细数组 |
| `GET /reports/product-line-profit` | [product_line_profit_report](../../../routers/reports.py) | admin/operator/viewer | report_month、report_date、store_id | 产品线汇总数组 |
| `GET /reports/inventory-impairment/export` | [export_inventory_impairment_report](../../../routers/reports.py) | admin/operator/viewer | report_date | Excel 文件 |
| `GET /reports/financial-attachments/export` | [export_financial_attachments](../../../routers/reports.py) | admin/operator/viewer | report_date、report_month、store_id | Excel 文件 |
| `GET /reports/monthly` | [monthly_report](../../../routers/reports.py) | admin/operator/viewer | start_date、end_date | list[MonthlyReportItem] |

## 日期和店铺

- monthly：start_date、end_date，含结束日；非管理员销售按绑定店铺筛选，库存字段为当前全局库存，不是月末库存。
- inventory-impairment 及独立 export：report_date 必填、不得晚于 date.today，当前没有 store_id/店铺过滤。
- store-profit-loss：report_month 必须当月第一天；store_id 可空，管理员默认首个有效店铺，非管理员固定绑定店铺且不能请求他店。
- product-line-profit：report_month 必须第一天，report_date 省略则该月末；admin 无 store_id 汇总当前有效店铺，其余通过绑定/选定店铺范围。该入口未采用存货接口同样的未来截止日校验。
- financial-attachments/export：report_date 必填不得晚于今天；report_month 可省略，默认截止日所在月份第一天；store_id 通过损益范围解析。

日期参数来自 Query/Pydantic，实际业务规则见 [reports.py](../../../routers/reports.py)。没有独立 public accounting-period 路由。

## 存货明细金额契约

`GET /reports/inventory-impairment` 保留已有字段，并增加以下说明/字段；算法依据见[流程](workflow.md)：

| 字段 | 口径 |
| --- | --- |
| `impairment_amount` | 报告日现存库存从入库后至截止日的累计跌价准备 |
| `monthly_impairment_amount` | 新增：同一现存库存本月1日至截止日新增的准备，包含规则变更和90%上限影响 |
| `book_value` | 报告日库存金额减累计准备 |
| `book_value_difference` | 当前账面价值减上月比较账面价值；历史批次无上月快照时为null，本月新增批次按上月0计算 |
| `comparison_note` | 新增：说明本月新增批次或缺少上月快照；比较基准正常时为空字符串 |
| `status` | 售完批次为“已售完”；其他按安全库存/比例状态显示 |

独立存货导出及财务附件07表均增加“本月计提（现存库存）(元)”列。缺少上月快照的历史批次差额和不完整差额合计留空，未知值不再回落为0；本月计提仍可计算。本列为现存库存增提，店铺损益的准备余额差口径见[流程](workflow.md)。

## 损益保存契约

StoreProfitLossUpdateRequest：store_id、report_month、manual_values、remarks。金额单元 StoreProfitLossCell 有 current/previous/ytd，可空；只保存允许的人工科目，自动/公式键忽略。备注只接受已定义科目、去空白后最多500字符。保存替换原 JSON，应提交完整要保留的人工值。

响应 StoreProfitLossResponse 含店铺、月份、can_edit、updated_at，以及每行 key/label/kind/is_auto/is_formula/source/value/remark。没有单独的审批/结账状态，公式由后端生成。UI 当前仅展示/编辑本月金额，API 和单店 Excel 仍提供三个期间。

## 导出与副作用

独立存货导出是全局；财务附件 admin 四张，非管理员三张且无跨店汇总。admin 单店损益为所选店铺、存货为全局、汇总和产品线为当前启用店铺范围。

存货相关 GET 可调用 ensure_period_snapshot 创建并提交历史快照，不能当纯只读请求。OA 完整配置的读取失败抛异常，汇率历史缺失在不同报表中处理不同，细节见[流程](workflow.md)。

输入日期不合规400，越店/无绑定403，无店铺404，框架校验422；并未定义统一报表外部数据错误格式。Excel 为 StreamingResponse，工作簿公式缓存已有处理。当前回归采用模拟会话及内存工作簿，服务器真实接口、数据库和导出对账仍待验证。损益字段契约见 [schemas.py](../../../schemas.py)。
