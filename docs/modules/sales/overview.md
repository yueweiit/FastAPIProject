# 销售与文件导入模块

## 功能与角色

提供指定店铺的手工销售、FIFO 扣库和批次成本明细；导入旧三列 CSV 或 TikTok“订单详情”CSV/XLSX/XLSM；保存结算原始行、组合 SKU 组件分摊、历史汇率和导入批次；提供待确认查询/导出、文件撤销、销售分页与人民币月度汇总。

写入/撤销仅 admin/operator；非管理员读取敏感数据按当前绑定店铺。operator 可撤销同店其他人的文件，并非只允许撤销本人文件。管理员导入可以未绑定店铺，生成记录 store_id 可空；手工销售必须选择有效店铺。依据：[routers/sales.py](../../../routers/sales.py)。

## 核心业务规则

- FIFO 按商品在全局可用批次中取最早 arrived_at/id，行锁后复查库存；总成本保存于 sales，分批数量/成本保存于 sale_cost_details。
- 订单详情严格按启用的 tiktok_shop 平台 SKU 映射解析组件，不根据商品名称猜测；组合倍数配置于主数据。
- 文件内格式/重复订单等错误阻止整体导入；商品未映射或库存不足可以保存为待确认，不部分扣减一整行组合商品。
- 同一个平台订单可有多个结算事件，source_key 使用事件字段哈希；不等于完整退款库存处理。
- 外币收入/费用通过结算行保存的汇率换算人民币，库存成本视作人民币。sales 原始 profit 不能直接替代 profit_cny。
- 撤销按原成本明细恢复原批次库存，删除该文件产生的销售/分摊/结算行，保留导入批次审计状态。

依据：[fifo.py](../../../services/fifo.py)、[exchange_rates.py](../../../services/exchange_rates.py) 和销售路由。实际流程、失败边界及待确认处理详见[workflow.md](workflow.md)。

## 文档入口

- [业务流程](workflow.md)
- [代码映射](code-map.md)
- [数据库字段](database.md)
- [模块 API](api.md)
- [知识库总索引](../../knowledge/README.md)
