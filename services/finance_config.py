"""新增财务结构需先核验迁移，再显式开启；默认不影响现有数据库。"""
import os

FINANCE_TABLES = {
    "inventory_cost_adjustments", "inventory_cost_adjustment_lines", "financial_expenses",
    "financial_expense_sources", "financial_receipts", "settlement_classifications",
    "financial_report_versions", "financial_audit_events",
}


def financial_controls_enabled():
    return os.getenv("FIFO_FINANCIAL_CONTROLS_ENABLED", "0").strip().lower() in {"1", "true", "yes"}
