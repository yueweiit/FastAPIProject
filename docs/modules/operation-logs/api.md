# 操作日志 API

三个接口仅GET，均使用RequireAdmin；运营和查看者返回403。旧GET /batches/{batch_id}/logs也收紧为RequireAdmin，保留响应兼容但页面已移除按钮。查询不调用迁移、写入、快照或审计追加，业务操作的日志记录机制不变。

以上四个日志读取接口响应设置`Cache-Control: private, no-store`，包括成功与已处理的401/403/404/422/503响应；接口字段、状态码、默认JSON序列化和权限保持不变。前端每次读取日志显式传递`cache: no-store`，不使用浏览器缓存替代管理员鉴权。新策略不会清除升级前已有缓存，发布步骤包含一次浏览器旧缓存清理；详见模块说明。

| 接口 | 参数 | 返回 |
| --- | --- | --- |
| GET /operation-logs/options | 无 | 管理员可见sources及全部可筛选stores；audit_enabled为捕获开关、audit_available为表存在性检查结果 |
| GET /operation-logs | source、store_id、start、end、keyword、actor、action、group_id、page、page_size | items/total/page/page_size |
| GET /operation-logs/{source}/{row_id} | 来源码及正整数ID | 规范化标头、record_kind、业务前后值、changes、reason |

来源码为batch、stock_count、sales_import、sales_rollback、finance，新增products、accounts、masters、batch_create、sales_operations、report_changes。source省略汇总全部当前可见来源。page>=1，page_size为1–100、默认20；页码超范围夹到最后一页。keyword<=255，匹配关联文本，新业务日志也搜索允许保存的前后JSON，便于查旧编码；actor<=64，匹配用户名；LIKE百分号/下划线按字面量处理。group_id为32位小写十六进制编号，匹配同次操作，仍受来源/店铺权限。action<=32为稳定操作码；新日志含create/update/delete/import_create/reset_password/change_password/change_status/change_role/bind_store/process_pending，旧来源保留原码。start/end为原记录日期，含结束日，开始不得晚于结束，日期校验失败422。

列表字段包含source、id、recorded_at、time_basis、actor_id/name、reference、action、product_id、sku、product_name、store_id/name、related_id及结构化summary。唯一事件标识为(source,id)，不能仅按id去重；销售同一次导入及撤销的id相同但来源不同。summary分别包含changed_fields、盘点count_date/revision/difference、导入当前settlement_rows/sales_created/pending_confirmation/status。

详情record_kind为change/snapshot/business_history。仅change提供逐字段changes；盘点和导入来源提供after_values作为保存值/当前业务历史，不伪造before_values。新业务日志额外提供entity_type、group_id、actor_role、unknown_before_fields；changes.before_known=false表示旧值未知。reason为空表示原记录未保存，不推断原因。字段白名单和时间说明见[模块说明](overview.md)。

待确认替换组件新增删除事件action=delete，before_values保存旧倍数/数量等允许字段，after_values为空；新组件及结算来源仍使用process_pending，同次操作共用group_id且分别校验来源店铺权限。来源归属为空时不回退操作者店铺，非管理员不能借group_id读取这类记录。财务字段按真实实体白名单返回：financial_expense_sources.mode、settlement_classifications.breakdown、financial_report_versions.version/calculation_version；摘要与详情使用相同投影。breakdown仅允许既有commission/tax/advertising/logistics/other科目，未知实体或非业务字段不透出。原路由、分页结构、财务开关与权限保持不变。

错误码：非管理员由RequireAdmin返回403；服务层直接调用也以operation_logs_admin_required（403）拒绝。operation_logs_source_forbidden、operation_logs_store_forbidden（403）保留；operation_log_not_found（404）；operation_logs_invalid_dates、operation_log_invalid_id或框架校验（422）；operation_logs_source_unavailable（503）。该中心无导出、编辑或删除接口。开启新增审计的业务写入口可能返回operation_audit_unavailable或operation_audit_storage_failed，均503并回滚本次未完成事务；成功响应契约不变。关闭时不捕获新日志，管理员仍能读取已存在历史。
