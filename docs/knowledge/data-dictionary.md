# 核心数据字典导航

本页维护跨模块字段的阅读入口和易混淆语义；完整类型、可空性、默认值、外键和索引只在模块 `database.md` 维护。依据：[models.py](../../models.py)、[schemas.py](../../schemas.py)。ORM 声明不代表历史实库已完成迁移；总体关系见[数据库设计](database.md)。

## 字段字典归属

| 模块 | 表与主要对象 | 唯一字段字典 |
| --- | --- | --- |
| 认证 | users、auth_login_transactions、auth_sessions：本地身份、临时流程、会话 | [认证数据库](../modules/auth/database.md) |
| 商品 | products、product_impairment_rules：商品与规则生效历史 | [商品数据库](../modules/products/database.md) |
| 库存 | inventory_batches：到货数量、剩余数量、批次成本及创建人 | [库存数据库](../modules/inventory/database.md) |
| 销售 | sales、sale_cost_details、sales_import_batches、settlement_entries、settlement_entry_allocations：出库、来源及分摊 | [销售数据库](../modules/sales/database.md) |
| 财务主数据 | product_lines、stores、store_products、platform_sku_mappings、platform_sku_components：两套用途不同的映射 | [主数据数据库](../modules/finance-masters/database.md) |
| 报表 | accounting_periods、inventory_period_snapshots、store_profit_loss_reports：快照标记、冻结结果及人工项 | [报表数据库](../modules/reports/database.md) |
| 财务增强 | inventory_cost_adjustments/lines、financial_expenses/sources/receipts、settlement_classifications、financial_report_versions、financial_audit_events | [财务增强数据库](../modules/financial-control/database.md) |

## 跨模块字段的关键区别

| 字段/类型 | 含义与依赖 | 维护入口 |
| --- | --- | --- |
| inventory_batches.quantity / remaining_quantity：Integer | 原始入库量 / 当前余额；历史余额由销售成本明细重建。直接改余额会破坏 FIFO 与报表勾稽 | [库存约束](../modules/inventory/database.md#数据约束和归属) |
| total_amount、purchase_price、unit_cost：DECIMAL(12,4) | 分别为整批采购金额、采购单价、含整批费用分摊的单件成本；请求含总金额时优先据此计算采购价 | [库存流程](../modules/inventory/workflow.md#新建批次) |
| SaleCostDetail.quantity / unit_cost：Integer / DECIMAL(12,4) | 已保存扣库数量和成本；是撤销及财务成本追溯依据，不用当前商品价格替换 | [销售流程](../modules/sales/workflow.md) |
| sales.order_no / order_claim：String(64) | 前者允许组合多明细；后者只由新订单第一条占位且全局唯一，历史和后续明细为 NULL | [销售幂等](../modules/sales/database.md#店铺幂等和审计) |
| settlement_entries.source_key：String(128) | 结算事件唯一键，与订单占位不同；同订单可有多个结算事件 | [销售导入](../modules/sales/workflow.md#文件导入) |
| currency / exchange_rate_to_cny：String(8) / DECIMAL(18,8) | 原币及固化人民币汇率；不能把 sales.profit 直接当人民币利润。缺失有效汇率统一传播至相关收入/费用和利润 | [销售人民币口径](../modules/sales/workflow.md#组合金额与人民币) |
| users.store_id 与业务 store_id：可空 Integer 外键 | 当前账号绑定不等于历史业务归属；批次无 store_id，库存归属从创建人当前绑定推导 | [隔离机制](authentication.md#店铺与租户机制) |
| product_type / safe_stock_quantity / effective_date | String(16) 类型、Integer 安全库存及 Date 生效日；历史规则参与跌价，不能只读商品当前值 | [商品规则](../modules/products/database.md#规则历史与引用) |
| manual_values / remarks / report_metadata：JSON | 人工损益项、备注和元数据；report_metadata 对应实际列名 metadata。current 保存范围只替换本月人工值 | [报表字典](../modules/reports/database.md#store_profit_loss_reports) |

存货 API 的 `monthly_total_impairment_amount`、`monthly_writeoff_amount` 等是动态计算字段，没有同名 ORM 列；类型、公式、空值传播和 Excel 映射统一见[报表字段来源](../modules/reports/inventory-impairment-fields.md)。

## 状态编码与展示边界

以下为现有值，详细校验位置见对应模块；这些字符串大多不是数据库 ENUM，不能推定任意写库路径均受相同限制。

| 对象 | 内部值 | 业务含义/注意 |
| --- | --- | --- |
| User.role | admin / operator / viewer | 管理员/运营/查看者；以[权限矩阵](authentication.md#实际权限矩阵)为准 |
| Product.product_type | new / stable | 新品/稳健；新品安全库存为 0 |
| SalesImportBatch.status | completed / rolled_back | 导入完成/已撤销；完成仍可有待确认行 |
| SettlementEntry.mapping_status | confirmed / pending_confirmation | 映射确认/待确认，与平台原始 status、transaction_type 分开 |
| AuthSession.source、AuthLoginTransaction.flow | local / eims；login / logout / logout_redirect | 会话来源与一次性流程类型，不能作为角色 |
| AccountingPeriod.status / snapshot_version | auto_closed；0 / 1 | 内部自动快照标记，无公开人工关账流程 |
| ProductImportRow.status（响应） | new / skipped / error / created | 预览新增/跳过/错误/已创建，未持久化为导入表 |
| StoreProfitLossUpdateRequest.update_scope（请求） | all / current | 默认完整替换；页面使用仅本月替换 |

依据：[模型](../../models.py)、[契约](../../schemas.py)、[认证会话](../../services/auth_sessions.py)、[快照](../../services/accounting_periods.py)。布尔 is_active 控制启停，不等于软删除或不可变历史版本。

## 扩展约定

- 修改字段先核查表字典 → 请求/响应 Schema → 页面/导出 → 下游公式及历史兼容；枚举新增应同时检查校验、展示和导入解析。
- 界面状态标签可以翻译，内部值、SKU、订单号、外部表单识别名称不能随界面语言改变；业务名称仍为单字段存储，见[国际化规范](internationalization.md)。
- 本次只核对源码和字典；真实表引擎、排序规则、历史索引/外键及数据完整性均待目标环境验证。

## 财务增强字典

新增8表88字段由[财务增强数据库](../modules/financial-control/database.md)维护：实际成本调整/批次分配、费用/来源、回款、结算分类、不可变版本及审计。原始成本与追溯响应分开；成本状态及单据状态、版本状态以该字典为准。
