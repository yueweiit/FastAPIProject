# 入库模块代码映射

| 页面/流程 | 前端函数 | 后端函数 | 数据/依赖 |
| --- | --- | --- | --- |
| 入库成本预览/创建 | calcUnitCost、createBatch | create_batch、_resolve_purchase_values、_calc_unit_cost、_next_batch_no | products、inventory_batches |
| SKU 下拉 | loadBatchSkuOptions、loadProductOptions | list_product_options | products |
| 搜索/分页 | loadBatches、searchBatches、renderBatchPage | list_batches | 批次、商品 |
| 字段显示 | toggleBatchColumn、applyBatchColumnSelection | 无后端写入 | 浏览器列状态 |
| 编辑/删除 | batchEditBlockedReason、openEditBatch、saveEditBatch、deleteBatch | _locked_batch、update_batch、delete_batch | 原创建人、库存剩余数、成本调整来源 |
| 选择/导出 | downloadBatchExport、exportBatch、exportAllBatches | export_batches | 批次、商品、uploads 图片 |
| FIFO 消耗 | 销售页面 | lock_fifo_batches、fifo_sell | sales、sale_cost_details |
| 撤销恢复 | rollbackSalesImport | rollback_sales_import | 原批次、原成本明细 |

来源：[templates/index.html](../../../templates/index.html)、[routers/batches.py](../../../routers/batches.py)、[routers/products.py](../../../routers/products.py)、[routers/sales.py](../../../routers/sales.py)、[services/fifo.py](../../../services/fifo.py)。契约为 [schemas.py](../../../schemas.py) 的 BatchCreate/BatchResponse/BatchPageResponse。

相关测试为 [test_sales_rules.py](../../../tests/test_sales_rules.py)、[test_fifo_concurrency.py](../../../tests/test_fifo_concurrency.py)；真实验证脚本 [mysql_fifo_concurrency.py](../../../tests/mysql_fifo_concurrency.py) 会写测试库，需要独立运行授权。历史批次字段/创建人转移脚本见 [migrations/](../../../migrations/)，不得默认执行。

批次编辑回归：`python -m unittest tests.test_batch_edit -v` 使用真实路由和隔离内存 SQLite 验证权限、出库后拦截、四位小数、清空单号及数量/金额校验，不证明 InnoDB 并发锁；`node tests/batch_edit_page_checks.cjs` 执行两份首页的实际渲染/编辑函数，覆盖按钮限制、回填、重复提交、保存错误及刷新失败。日期边界沿用 `node tests/page_interactions_checks.cjs`。新增按钮/限制/保存提示集中于 [page-interactions.js](../../../static/page-interactions.js) 的 zh-CN 字典，未增加全站语言切换。

成本来源并发回归：`test_batch_edit`模拟认证建立的RR旧快照，验证编辑与删除均先锁批次，再以FOR UPDATE当前读检查调整行，发现已提交来源返回409。MySQL方言检查加锁SQL；隔离SQLite/模拟会话不代表真实InnoDB等待验证。
