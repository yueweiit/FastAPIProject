from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, Field, ConfigDict


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


class BatchPageResponse(BaseModel):
    items: list[BatchResponse]
    total: int
    page: int
    page_size: int


# ========== 销售 ==========
class SaleCreate(BaseModel):
    product_id: int
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


class SaleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    user_id: int | None = None
    order_no: str
    quantity: int
    selling_price: Decimal
    total_cost: Decimal
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
    remark: str = ""


class StoreProfitLossUpdateRequest(BaseModel):
    store_id: int = Field(..., ge=1)
    report_month: date
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


class AccountingPeriodRequest(BaseModel):
    period_start: date
    period_end: date
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)


class AccountingPeriodResponse(AccountingPeriodRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    snapshot_version: int
    closed_at: datetime | None = None
    closed_by_user_id: int | None = None
    snapshot_count: int = 0
    confirmation_deadline: datetime | None = None
    created_at: datetime
    updated_at: datetime
