from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict, model_validator, field_serializer


class FinancialMoneyRequest(BaseModel):
    amount: Decimal = Field(max_digits=16, decimal_places=4)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    exchange_rate: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=8)

    @model_validator(mode="after")
    def check_financial_rate(self):
        if self.currency == "CNY":
            if self.exchange_rate not in (None, Decimal(1)):
                raise ValueError("CNY汇率必须为1")
            self.exchange_rate = Decimal(1)
        elif self.exchange_rate is None:
            raise ValueError("外币必须明确填写有效人民币汇率")
        return self


class CostAllocationRequest(BaseModel):
    batch_id: int = Field(gt=0)
    weight: Decimal | None = Field(default=None, gt=0, max_digits=20, decimal_places=8)


class CostAdjustmentRequest(FinancialMoneyRequest):
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    source_ref: str = Field(min_length=1, max_length=128)
    category: Literal["purchase", "head_shipping", "last_mile", "other"]
    allocation_basis: Literal["quantity", "weight", "volume", "value"] = "quantity"
    rate_date: date
    document_date: date
    allocations: list[CostAllocationRequest] = Field(min_length=1, max_length=100)
    is_final: bool = False
    note: str = Field(default="", max_length=1000)


class FinanceReversalRequest(BaseModel):
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    note: str = Field(min_length=1, max_length=1000)


class ExpenseRequest(FinancialMoneyRequest):
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    store_id: int = Field(gt=0)
    expense_date: date
    category: str = Field(min_length=1, max_length=64)
    source_ref: str = Field(min_length=1, max_length=128)
    settlement_entry_id: int | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=1000)


class ExpenseSourceRequest(BaseModel):
    store_id: int = Field(gt=0)
    report_month: date
    category: str = Field(min_length=1, max_length=64)
    mode: Literal["manual", "ledger"]


class ReceiptRequest(FinancialMoneyRequest):
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    store_id: int = Field(gt=0)
    payout_id: str = Field(min_length=1, max_length=128)
    receipt_date: date
    source_ref: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=1000)


class SettlementClassificationRequest(BaseModel):
    breakdown: dict[str, Decimal]
    note: str = Field(default="", max_length=1000)


class FinancialVersionRequest(BaseModel):
    request_key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    store_id: int = Field(gt=0)
    report_month: date
    note: str = Field(default="", max_length=1000)


# ========== 认证 ==========
class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user_id: int
    username: str
    role: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    is_active: bool = True
    store_id: int | None = None
    store_name: str | None = None
    created_at: datetime


# ========== 商品 ==========
class ProductCreate(BaseModel):
    sku: str = Field(..., max_length=64, examples=["SKU-001"])
    name: str = Field(..., max_length=255, examples=["蓝牙耳机"])


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    image: str | None = None
    product_type: str = "stable"
    safe_stock_quantity: int = 0
    created_at: datetime
    # 前端计算填充
    stock_quantity: int = 0
    stock_value: Decimal = Decimal("0")
    last_batch_at: str | None = None


class ProductOptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    name: str
    image: str | None = None


class ProductPageResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    page_size: int


class ProductImportError(BaseModel):
    field: str = Field(max_length=32)
    message: str = Field(max_length=500)


class ProductImportRow(BaseModel):
    row_number: int = Field(ge=2, le=10001)
    sku: str = Field(default="", max_length=128)
    name: str = Field(default="", max_length=512)
    product_type: str | None = Field(default=None, max_length=64)
    safe_stock_quantity: int | None = None
    status: Literal["new", "skipped", "error", "created"] = "new"
    message: str = Field(default="", max_length=500)
    existing_product_id: int | None = None
    existing_product_name: str | None = Field(default=None, max_length=255)
    product_id: int | None = None
    errors: list[ProductImportError] = Field(default_factory=list, max_length=10)


class ProductImportResponse(BaseModel):
    total_rows: int
    new_count: int
    skipped_count: int
    error_count: int
    created_count: int = 0
    can_import: bool = False
    preview_token: str | None = None
    message: str
    rows: list[ProductImportRow]


class ProductImportResultRequest(BaseModel):
    rows: list[ProductImportRow] = Field(max_length=2000)


# ========== 入库批次 ==========
class BatchCreate(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)
    purchase_price: Decimal = Field(default=0, ge=0, examples=[100.00])
    dingtalk_order_no: str | None = Field(default=None, max_length=64, examples=["202608201234000000001"])
    total_amount: Decimal | None = Field(default=None, ge=0, examples=[10000.00])
    shipping_cost: Decimal = Field(default=0, ge=0, examples=[500.00])
    shipping_cost_no: str | None = Field(default=None, max_length=64)
    last_mile_cost: Decimal = Field(default=0, ge=0, examples=[300.00])
    last_mile_cost_no: str | None = Field(default=None, max_length=64)
    other_cost: Decimal = Field(default=0, ge=0)
    other_cost_no: str | None = Field(default=None, max_length=64)
    shipping_date: datetime | None = None
    arrived_at: datetime | None = None


class BatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    user_id: int | None = None
    batch_no: str
    quantity: int
    remaining_quantity: int
    unit_cost: Decimal
    purchase_price: Decimal
    dingtalk_order_no: str | None = None
    total_amount: Decimal | None = None
    shipping_cost: Decimal
    shipping_cost_no: str | None = None
    last_mile_cost: Decimal
    last_mile_cost_no: str | None = None
    other_cost: Decimal
    other_cost_no: str | None = None
    shipping_date: datetime | None = None
    arrived_at: datetime
    created_at: datetime
    base_unit_cost: Decimal | None = None
    cost_adjustment_per_unit: Decimal = Decimal(0)
    cost_status: str = "unverified"


class BatchPageResponse(BaseModel):
    items: list[BatchResponse]
    total: int
    page: int
    page_size: int


class BatchChangeLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    batch_no: str
    action: str
    actor_id: int
    actor_username: str
    actor_role: str
    before_values: dict
    after_values: dict
    created_at: datetime

    @field_serializer("created_at")
    def serialize_time(self, value: datetime):
        # 日志记录绝对时间；批次发货/入库日期仍沿用无时区业务日期。
        return value.replace(tzinfo=timezone.utc).isoformat()


class BatchChangeLogPageResponse(BaseModel):
    items: list[BatchChangeLogResponse]
    total: int
    page: int
    page_size: int


# ========== 销售 ==========
class SaleCreate(BaseModel):
    product_id: int
    store_id: int | None = Field(default=None, ge=1)
    order_no: str = Field(..., max_length=64, examples=["ORD-20240101-001"])
    quantity: int = Field(..., gt=0)
    selling_price: Decimal = Field(default=0, ge=0, examples=[180.00])
    platform_fee: Decimal = Field(default=0, ge=0, examples=[10.00])
    sold_at: datetime | None = None


class CostDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: int
    batch_no: str
    quantity: int
    unit_cost: Decimal
    base_unit_cost: Decimal | None = None


class SaleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int | None = None
    user_id: int | None = None
    order_no: str
    quantity: int
    selling_price: Decimal
    total_cost: Decimal
    original_total_cost: Decimal | None = None
    cost_adjustment_cny: Decimal = Decimal(0)
    cost_status: str = "unverified"
    platform_fee: Decimal
    profit: Decimal
    currency: str = "CNY"
    exchange_rate_to_cny: Decimal | None = Decimal("1")
    exchange_rate_date: date | None = None
    exchange_rate_source: str | None = None
    selling_price_cny: Decimal | None = None
    sales_revenue_cny: Decimal | None = None
    platform_fee_cny: Decimal | None = None
    profit_cny: Decimal | None = None
    store_name: str | None = None
    sold_at: datetime
    created_at: datetime
    cost_details: list[CostDetailResponse] = []
    record_type: str = "sale"
    display_sku: str | None = None
    display_name: str | None = None


class SalePageResponse(BaseModel):
    items: list[SaleResponse]
    total: int
    page: int
    page_size: int


class MonthlySalesSummaryResponse(BaseModel):
    month: str
    sales_count: int
    sold_quantity: int
    sales_revenue_cny: Decimal | None = None
    sales_cost_cny: Decimal = Decimal("0")
    platform_fee_cny: Decimal | None = None
    gross_profit_cny: Decimal | None = None
    fx_missing_count: int = 0
    settlement_event_count: int = 0


class SalesImportError(BaseModel):
    row: int
    message: str
    identifier: str | None = None


class SalesImportResponse(BaseModel):
    success: bool
    file_type: str
    sheet_name: str | None = None
    total_rows: int
    settlement_rows: int
    sales_created: int
    skipped_duplicates: int
    failed_rows: int
    pending_confirmation: int = 0
    import_batch_id: int | None = None
    errors: list[SalesImportError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SalesImportBatchResponse(BaseModel):
    id: int
    file_name: str
    file_type: str
    sheet_name: str | None = None
    imported_by: str | None = None
    store_name: str | None = None
    total_rows: int
    settlement_rows: int
    sales_created: int
    skipped_duplicates: int
    pending_confirmation: int
    status: str
    imported_at: datetime
    rolled_back_at: datetime | None = None
    can_rollback: bool = False


class SalesImportRollbackResponse(BaseModel):
    success: bool
    import_batch_id: int
    restored_quantity: int
    sales_removed: int
    settlement_rows_removed: int


class PendingConfirmationResponse(BaseModel):
    id: int
    source_file: str | None = None
    source_sheet: str | None = None
    source_row_number: int
    order_id: str
    platform_sku_id: str
    mapping_status: str
    mapping_error: str | None = None
    store_name: str | None = None
    settlement_date: datetime | None = None
    order_created_at: datetime | None = None
    quantity: int
    product_name: str | None = None
    sku_name: str | None = None
    settlement_total: Decimal
    transaction_type: str | None = None
    currency: str | None = None
    other_expense: Decimal
    imported_at: datetime


class PendingConfirmationPageResponse(BaseModel):
    items: list[PendingConfirmationResponse]
    total: int
    page: int
    page_size: int


# ========== 报表 ==========
class MonthlyReportItem(BaseModel):
    """月度产品件数售卖和库存报表"""
    month: str  # "2024-01"
    product_id: int
    sku: str
    product_name: str
    sold_quantity: int  # 当月售卖件数
    inventory_quantity: int  # 当前库存件数
    inventory_value: Decimal = Decimal("0")  # 库存价值


class StoreProfitLossCell(BaseModel):
    current: Decimal | None = None
    previous: Decimal | None = None
    ytd: Decimal | None = None


class StoreProfitLossRowResponse(BaseModel):
    key: str
    label: str
    kind: str
    is_auto: bool = False
    is_formula: bool = False
    source: str | None = None
    value: StoreProfitLossCell
    manual_value: StoreProfitLossCell | None = None
    input_source: Literal["manual", "ledger"] = "manual"
    remark: str = ""


class StoreProfitLossUpdateRequest(BaseModel):
    store_id: int = Field(..., ge=1)
    report_month: date
    update_scope: Literal["all", "current"] = "all"
    manual_values: dict[str, StoreProfitLossCell] = Field(default_factory=dict)
    remarks: dict[str, str] = Field(default_factory=dict)


class StoreProfitLossResponse(BaseModel):
    store_id: int
    store_name: str
    store_platform: str
    report_month: str
    period_label: str
    can_edit: bool
    updated_at: datetime | None = None
    rows: list[StoreProfitLossRowResponse]
    warnings: list[str] = Field(default_factory=list)


# ========== 财务主数据 ==========
class ProductLineRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    is_active: bool = True


class ProductLineResponse(ProductLineRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class StoreRequest(BaseModel):
    platform: str = Field(default="tiktok_shop", min_length=1, max_length=32)
    platform_store_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)
    country_or_region: str = Field(..., min_length=1, max_length=64)
    settlement_currency: str = Field(..., min_length=3, max_length=3)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)
    legal_entity: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class StoreResponse(StoreRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class StoreProductRequest(BaseModel):
    store_id: int
    product_id: int
    product_line_id: int
    platform_product_id: str | None = Field(default=None, max_length=128)
    platform_sku_id: str | None = Field(default=None, max_length=128)
    store_sku: str = Field(..., min_length=1, max_length=128)
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True


class StoreProductResponse(StoreProductRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_name: str
    product_sku: str
    product_name: str
    product_line_code: str
    product_line_name: str
    created_at: datetime
    updated_at: datetime


class PlatformSkuComponentRequest(BaseModel):
    product_id: int = Field(..., ge=1)
    quantity_per_sale: int = Field(..., ge=1)


class PlatformSkuMappingCreateRequest(BaseModel):
    platform: str = Field(default="tiktok_shop", min_length=1, max_length=32)
    platform_sku_ids: list[str] = Field(..., min_length=1, max_length=100)
    components: list[PlatformSkuComponentRequest] = Field(..., min_length=1)


class PlatformSkuMappingUpdateRequest(BaseModel):
    platform_sku_id: str = Field(..., min_length=1, max_length=128)
    components: list[PlatformSkuComponentRequest] = Field(..., min_length=1)
    is_active: bool = True


class PlatformSkuComponentResponse(BaseModel):
    product_id: int
    product_sku: str
    product_name: str
    quantity_per_sale: int


class PlatformSkuMappingResponse(BaseModel):
    id: int
    platform: str
    platform_sku_id: str
    is_active: bool
    is_bundle: bool
    components: list[PlatformSkuComponentResponse]
    created_at: datetime
    updated_at: datetime
