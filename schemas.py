from datetime import datetime
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
    sold_at: datetime
    created_at: datetime
    cost_details: list[CostDetailResponse] = []


class SalePageResponse(BaseModel):
    items: list[SaleResponse]
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
