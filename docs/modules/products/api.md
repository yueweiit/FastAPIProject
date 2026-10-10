# 商品 API

前缀 /products，共 6 个端点；入口均允许 admin/operator/viewer。所有数据为共享范围。

| 方法与路径 | 处理函数 | 权限依赖 | 主要输入 | 响应 |
| --- | --- | --- | --- | --- |
| `POST /products` | [create_product](../../../routers/products.py) | admin/operator/viewer | sku、name、product_type、safe_stock_quantity、image | ProductResponse |
| `GET /products/options` | [list_product_options](../../../routers/products.py) | admin/operator/viewer | 无 | list[ProductOptionResponse] |
| `GET /products` | [list_products](../../../routers/products.py) | admin/operator/viewer | keyword、page、page_size | list[ProductResponse] \| ProductPageResponse |
| `GET /products/export` | [export_products](../../../routers/products.py) | admin/operator/viewer | product_ids | Excel 文件 |
| `PUT /products/{product_id}` | [update_product](../../../routers/products.py) | admin/operator/viewer | product_id、sku、name、product_type、safe_stock_quantity、image | ProductResponse |
| `DELETE /products/{product_id}` | [delete_product](../../../routers/products.py) | admin/operator/viewer | product_id | ok |

## 请求和查询

POST/PUT 使用 multipart/form-data；sku、name 必填，image 可选。POST 的 product_type 默认 stable、safe_stock_quantity 默认 0；PUT 的两个规则字段可不传，保留现值。PUT 是商品表单更新，不是 JSON ProductCreate；schemas.py 中 ProductCreate 不能直接当该接口的接收契约。

GET 列表支持 keyword（长度≤255）、page（≥1）、page_size（默认20，1–100）；名称或 SKU 模糊查询。不传 page 是数组，传 page 是 ProductPageResponse。options 返回 id、sku、name、image，适合下拉选择。

导出通过重复参数 product_ids 选择，例如 /products/export?product_ids=1&product_ids=2；未传 ID 导出全部，不支持后端 keyword 查询。无记录返回 404；SKU 重复返回 400；商品规则不合法返回 422；删除仍被平台组件引用的商品返回 400。

响应 ProductResponse 的 stock_quantity、stock_value、last_batch_at 在列表/导出时聚合；创建/更新即时响应填 0/空，需重新查询。完整契约见 [schemas.py](../../../schemas.py)，路径和入参见 [products.py](../../../routers/products.py)。
