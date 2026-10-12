# 商品 API

前缀 /products，共 10 个端点；入口均允许 admin/operator/viewer。所有数据为共享范围。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /products` | [create_product](../../../routers/products.py) | admin/operator/viewer | sku、name、product_type、safe_stock_quantity、image | ProductResponse |
| `GET /products/import-template` | [product_import_template](../../../routers/products.py) | admin/operator/viewer | 无 | 商品导入模板 .xlsx |
| `POST /products/import/preview` | [preview_products](../../../routers/products.py) | admin/operator/viewer | multipart：file | ProductImportResponse；只读预览 |
| `POST /products/import` | [import_products](../../../routers/products.py) | admin/operator/viewer | multipart：file、preview_token | ProductImportResponse；整批新增 |
| `POST /products/import/result` | [export_product_import_result](../../../routers/products.py) | admin/operator/viewer | JSON：rows | 校验/导入结果 .xlsx |
| `GET /products/options` | [list_product_options](../../../routers/products.py) | admin/operator/viewer | 无 | list[ProductOptionResponse] |
| `GET /products` | [list_products](../../../routers/products.py) | admin/operator/viewer | keyword、page、page_size | list[ProductResponse] \| ProductPageResponse |
| `GET /products/export` | [export_products](../../../routers/products.py) | admin/operator/viewer | product_ids | Excel 文件 |
| `PUT /products/{product_id}` | [update_product](../../../routers/products.py) | admin/operator/viewer | product_id、sku、name、product_type、safe_stock_quantity、image | ProductResponse |
| `DELETE /products/{product_id}` | [delete_product](../../../routers/products.py) | admin/operator/viewer | product_id | ok |

## 请求和查询

单条商品 POST/PUT 使用 multipart/form-data；sku、name 必填，image 可选。POST 的 product_type 默认 stable、safe_stock_quantity 默认 0；PUT 的两个规则字段可不传，保留现值。PUT 是商品表单更新，不是 JSON ProductCreate；schemas.py 中 ProductCreate 不能直接当该接口的接收契约。

GET 列表支持 keyword（长度≤255）、page（≥1）、page_size（默认20，1–100）；名称或 SKU 模糊查询。不传 page 是数组，传 page 是 ProductPageResponse。options 返回 id、sku、name、image，适合下拉选择。

导出通过重复参数 product_ids 选择，例如 /products/export?product_ids=1&product_ids=2；未传 ID 导出全部，不支持后端 keyword 查询。无记录返回 404；SKU 重复返回 400；商品规则不合法返回 422；删除仍被平台组件引用的商品返回 400。

响应 ProductResponse 的 stock_quantity、stock_value、last_batch_at 在列表/导出时聚合；创建/更新即时响应填 0/空，需重新查询。完整契约见 [schemas.py](../../../schemas.py)，路径和入参见 [products.py](../../../routers/products.py)。

## 批量新增契约

模板四列为 SKU、商品名称、商品类型、安全库存，详细校验规则及事务边界由[导入流程](workflow.md#商品-excel-批量新增)维护。模板与结果均为 openpyxl 生成的 .xlsx，上传只支持 .xlsx，最多 5 MB、2,000 个非空数据行。预览不写数据，确认必须重新上传同一文件和服务端签发的 preview_token；仅提交前端 rows 不会新增商品。

`ProductImportResponse` 字段：

| 字段 | 含义 |
| --- | --- |
| total_rows | 非空数据行数 |
| new_count | 当前可新增行数；成功后为 0 |
| skipped_count | 已存在、保留原资料的行数 |
| error_count | 错误行数，不是字段错误的数量 |
| created_count | 本次成功新增数量；预览为 0 |
| can_import | 有可新增行且所有行无错误时为 true |
| preview_token | 仅可提交的预览返回；绑定用户/文件/结果，有效 15 分钟 |
| message | 中文处理说明 |
| rows | 每行原始 row_number、sku、name、product_type、safe_stock_quantity、status、message、errors、已有商品 ID/名称、新增 product_id |

行状态：new 可新增、skipped 已存在跳过、error 需修正、created 已新增。errors 为 `{field,message}` 数组。字段错误及文件内重复 SKU 的预览返回 HTTP 200，error_count > 0、can_import=false、preview_token=null；文件格式、表头、条数等无法解析错误返回 400，超 5 MB 返回 413。缺少必需参数返回 422，无效预览返回 400，过期/变更/并发唯一键冲突返回 409，确认失败不会保留部分新增。

结果下载接收 `ProductImportResultRequest(rows)`，最多 2,000 行，经 Pydantic 校验后将当前清单转换为 Excel，不读取或写入数据库。它只生成展示文件，不能作为服务器对客户端行状态的真实性背书。原始行号、商品 ID、状态和错误保留；SKU、名称等文本不会被转换为 Excel 公式。实现：[product_import.py](../../../services/product_import.py)；测试：[test_product_import.py](../../../tests/test_product_import.py)。
