from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    DECIMAL,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="operator")  # admin / operator / viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    store_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stores.id"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    store: Mapped["Store | None"] = relationship(back_populates="users")
    sales: Mapped[list["Sale"]] = relationship(back_populates="user")
    batches: Mapped[list["InventoryBatch"]] = relationship(back_populates="user")
    closed_accounting_periods: Mapped[list["AccountingPeriod"]] = relationship(
        back_populates="closed_by_user"
    )


class AuthLoginTransaction(Base):
    """Only hashes of browser/state credentials and encrypted temporary data."""
    __tablename__ = "auth_login_transactions"
    __table_args__ = {"mysql_engine": "InnoDB"}

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow: Mapped[str] = mapped_column(String(16))
    browser_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = {"mysql_engine": "InnoDB"}

    session_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(16))
    csrf_token: Mapped[str] = mapped_column(String(64))
    issuer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    oidc_nonce: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    image: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 图片路径
    product_type: Mapped[str] = mapped_column(String(16), default="stable")
    safe_stock_quantity: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    batches: Mapped[list["InventoryBatch"]] = relationship(back_populates="product")
    sales: Mapped[list["Sale"]] = relationship(back_populates="product")
    store_products: Mapped[list["StoreProduct"]] = relationship(back_populates="product")
    platform_sku_components: Mapped[list["PlatformSkuComponent"]] = relationship(
        back_populates="product"
    )
    settlement_entries: Mapped[list["SettlementEntry"]] = relationship(back_populates="product")
    impairment_rules: Mapped[list["ProductImpairmentRule"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductImpairmentRule(Base):
    """商品类型和安全库存的生效历史，用于保留已累计的跌价准备。"""
    __tablename__ = "product_impairment_rules"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "effective_date",
            name="uq_product_impairment_rules_product_effective_date",
        ),
        Index("ix_product_impairment_rules_product_effective", "product_id", "effective_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    product_type: Mapped[str] = mapped_column(String(16), default="stable")
    safe_stock_quantity: Mapped[int] = mapped_column(Integer, default=0)
    effective_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="impairment_rules")


class InventoryBatch(Base):
    """入库批次 - 每批到货单独记录，含运费的真实成本"""
    __tablename__ = "inventory_batches"
    __table_args__ = (
        Index("ix_inventory_batches_arrived_at_id", "arrived_at", "id"),
        Index("ix_inventory_batches_product_arrived_at", "product_id", "arrived_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), index=True)
    batch_no: Mapped[str] = mapped_column(String(64), unique=True)
    quantity: Mapped[int] = mapped_column(Integer)  # 入库数量
    remaining_quantity: Mapped[int] = mapped_column(Integer)  # 剩余数量（FIFO扣减）
    unit_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # 单件成本
    purchase_price: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # 采购单价
    dingtalk_order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 钉钉单号
    total_amount: Mapped[Decimal | None] = mapped_column(DECIMAL(12, 4), nullable=True)  # 入库总金额，仅留档
    shipping_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 4), default=0)  # 头程运费（整批）
    shipping_cost_no: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 头程运费单号
    last_mile_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 4), default=0)  # 尾程派送费用（整批）
    last_mile_cost_no: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 尾程派送费单号
    other_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 4), default=0)  # 其他成本（国内物流、贴标等）
    other_cost_no: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 其他成本单号
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=True)  # 创建者
    shipping_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 头程发货时间
    arrived_at: Mapped[datetime] = mapped_column(DateTime)  # 入库时间
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="batches")
    user: Mapped["User"] = relationship(back_populates="batches")
    cost_details: Mapped[list["SaleCostDetail"]] = relationship(back_populates="batch")
    period_snapshots: Mapped[list["InventoryPeriodSnapshot"]] = relationship(
        back_populates="batch"
    )

    @property
    def edit_version(self) -> str:
        """原始持久化字段的内容版本；比较时必须先锁定并刷新批次。"""
        values = {}
        for column in self.__table__.columns:
            value = getattr(self, column.key)
            if isinstance(value, Decimal):
                value = format(value.normalize(), "f")
            elif isinstance(value, datetime):
                value = value.isoformat(timespec="microseconds")
            values[column.key] = value
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return sha256(encoded.encode("utf-8")).hexdigest()


class BatchChangeLog(Base):
    """追加式批次修改记录；保留身份快照，不随批次或账号删除级联丢失。"""
    __tablename__ = "batch_change_logs"
    __table_args__ = (
        Index("ix_batch_change_logs_batch_id_id", "batch_id", "id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(Integer)
    batch_no: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16), default="update")
    actor_id: Mapped[int] = mapped_column(Integer)
    actor_username: Mapped[str] = mapped_column(String(64))
    actor_role: Mapped[str] = mapped_column(String(16))
    before_values: Mapped[dict] = mapped_column(JSON)
    after_values: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


class Sale(Base):
    """销售记录 - 每笔订单"""
    __tablename__ = "sales"
    __table_args__ = (
        Index("ix_sales_sold_at", "sold_at"),
        Index("ix_sales_user_sold_at", "user_id", "sold_at"),
        UniqueConstraint("order_claim", name="uq_sales_order_claim"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=True)  # 创建者
    store_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stores.id"), index=True, nullable=True
    )
    order_no: Mapped[str] = mapped_column(String(64), index=True)
    # 每个订单只由首条明细占位；组合明细和未回填的历史记录保持NULL。
    order_claim: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    selling_price: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # 销售单价
    total_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # FIFO算出的总成本
    platform_fee: Mapped[Decimal] = mapped_column(DECIMAL(12, 4), default=0)  # 平台费用
    profit: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # 利润
    sold_at: Mapped[datetime] = mapped_column(DateTime)  # 销售时间
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="sales")
    user: Mapped["User"] = relationship(back_populates="sales")
    store: Mapped["Store | None"] = relationship(back_populates="sales")
    cost_details: Mapped[list["SaleCostDetail"]] = relationship(back_populates="sale")
    settlement_entries: Mapped[list["SettlementEntry"]] = relationship(back_populates="sale")
    settlement_allocations: Mapped[list["SettlementEntryAllocation"]] = relationship(
        back_populates="sale"
    )


class SaleCostDetail(Base):
    """销售成本明细 - FIFO从哪些批次扣了多少"""
    __tablename__ = "sale_cost_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sale_id: Mapped[int] = mapped_column(Integer, ForeignKey("sales.id"), index=True)
    batch_id: Mapped[int] = mapped_column(Integer, ForeignKey("inventory_batches.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)  # 从该批次扣了多少件
    unit_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # 该批次的单价

    sale: Mapped["Sale"] = relationship(back_populates="cost_details")
    batch: Mapped["InventoryBatch"] = relationship(back_populates="cost_details")


class SalesImportBatch(Base):
    """One uploaded sales file and its reversible import result."""

    __tablename__ = "sales_import_batches"
    __table_args__ = (
        Index("ix_sales_import_batches_imported_at", "imported_at"),
        Index("ix_sales_import_batches_user_imported_at", "user_id", "imported_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    store_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stores.id"), nullable=True, index=True
    )
    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(32))
    sheet_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    settlement_rows: Mapped[int] = mapped_column(Integer, default=0)
    sales_created: Mapped[int] = mapped_column(Integer, default=0)
    skipped_duplicates: Mapped[int] = mapped_column(Integer, default=0)
    pending_confirmation: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="completed", index=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rolled_back_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    entries: Mapped[list["SettlementEntry"]] = relationship(back_populates="import_batch")


class SettlementEntry(Base):
    """TikTok Shop 订单详情中的原始结算行及可复用的标准化金额。"""
    __tablename__ = "settlement_entries"
    __table_args__ = (
        Index("ix_settlement_entries_settlement_date", "settlement_date"),
        Index("ix_settlement_entries_order_sku", "order_id", "platform_sku_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(128), unique=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_row_number: Mapped[int] = mapped_column(Integer)
    settlement_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payout_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    order_id: Mapped[str] = mapped_column(String(128), index=True)
    related_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform_sku_id: Mapped[str] = mapped_column(String(128), index=True)
    product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("products.id"), nullable=True, index=True
    )
    sale_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sales.id"), nullable=True, index=True
    )
    store_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stores.id"), nullable=True, index=True
    )
    import_batch_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sales_import_batches.id"), nullable=True, index=True
    )
    mapping_status: Mapped[str] = mapped_column(
        String(32), default="confirmed", server_default="confirmed", index=True
    )
    mapping_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    transaction_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exchange_rate_to_cny: Mapped[Decimal | None] = mapped_column(
        DECIMAL(18, 8), nullable=True
    )
    exchange_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exchange_rate_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settlement_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    order_created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    product_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sku_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settlement_total: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    net_product_sales: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    net_shipping: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    taxes: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    platform_commission: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    service_fee: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    sfp_service_fee: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    per_item_fee: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    tax_withholding: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    affiliate_commission: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    creator_commission: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    creator_shop_ad_commission: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    affiliate_shop_ad_commission: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    gmv_max_ad_fee: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    adjustment_amount: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    customer_payment: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    customer_refund: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    raw_data: Mapped[dict] = mapped_column(JSON)
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product | None"] = relationship(back_populates="settlement_entries")
    sale: Mapped["Sale | None"] = relationship(back_populates="settlement_entries")
    store: Mapped["Store | None"] = relationship(back_populates="settlement_entries")
    import_batch: Mapped["SalesImportBatch | None"] = relationship(back_populates="entries")
    allocations: Mapped[list["SettlementEntryAllocation"]] = relationship(
        back_populates="settlement_entry"
    )


class SettlementEntryAllocation(Base):
    """A settlement row's component-level product and FIFO allocation."""

    __tablename__ = "settlement_entry_allocations"
    __table_args__ = (
        UniqueConstraint(
            "settlement_entry_id",
            "product_id",
            name="uq_settlement_entry_allocations_entry_product",
        ),
        Index("ix_settlement_entry_allocations_product", "product_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    settlement_entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("settlement_entries.id"), index=True
    )
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), index=True)
    sale_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sales.id"), nullable=True, index=True
    )
    component_name: Mapped[str] = mapped_column(String(500))
    multiplier: Mapped[int] = mapped_column(Integer, default=1)
    quantity: Mapped[int] = mapped_column(Integer, default=0)

    settlement_entry: Mapped["SettlementEntry"] = relationship(back_populates="allocations")
    product: Mapped["Product"] = relationship()
    sale: Mapped["Sale | None"] = relationship(back_populates="settlement_allocations")


class ProductLine(Base):
    """受控产品线主数据，供后续产品线毛利分析使用。"""
    __tablename__ = "product_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    store_products: Mapped[list["StoreProduct"]] = relationship(back_populates="product_line")


class Store(Base):
    """电商店铺主数据，不保存平台授权令牌。"""
    __tablename__ = "stores"
    __table_args__ = (
        UniqueConstraint("platform", "platform_store_id", name="uq_stores_platform_store_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), default="tiktok_shop")
    platform_store_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    country_or_region: Mapped[str] = mapped_column(String(64))
    settlement_currency: Mapped[str] = mapped_column(String(3))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    legal_entity: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    users: Mapped[list["User"]] = relationship(back_populates="store")
    sales: Mapped[list["Sale"]] = relationship(back_populates="store")
    settlement_entries: Mapped[list["SettlementEntry"]] = relationship(back_populates="store")
    store_products: Mapped[list["StoreProduct"]] = relationship(back_populates="store")


class StoreProduct(Base):
    """店铺 SKU 与本地商品、产品线之间的有效期映射。"""
    __tablename__ = "store_products"
    __table_args__ = (
        UniqueConstraint("store_id", "store_sku", "effective_from", name="uq_store_products_sku_effective_from"),
        Index("ix_store_products_store_product_active", "store_id", "product_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), index=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), index=True)
    product_line_id: Mapped[int] = mapped_column(Integer, ForeignKey("product_lines.id"), index=True)
    platform_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform_sku_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    store_sku: Mapped[str] = mapped_column(String(128))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    store: Mapped["Store"] = relationship(back_populates="store_products")
    product: Mapped["Product"] = relationship(back_populates="store_products")
    product_line: Mapped["ProductLine"] = relationship(back_populates="store_products")


class PlatformSkuMapping(Base):
    """Global platform SKU mapping, independent of individual stores."""
    __tablename__ = "platform_sku_mappings"
    __table_args__ = (
        UniqueConstraint("platform", "platform_sku_id", name="uq_platform_sku_mappings_platform_sku"),
        Index("ix_platform_sku_mappings_platform_active", "platform", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), default="tiktok_shop")
    platform_sku_id: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    components: Mapped[list["PlatformSkuComponent"]] = relationship(
        back_populates="mapping", cascade="all, delete-orphan"
    )


class PlatformSkuComponent(Base):
    """A local product and its required quantity for one platform SKU sale."""
    __tablename__ = "platform_sku_components"
    __table_args__ = (
        UniqueConstraint(
            "platform_sku_mapping_id", "product_id",
            name="uq_platform_sku_components_mapping_product",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform_sku_mapping_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("platform_sku_mappings.id"), index=True
    )
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), index=True)
    quantity_per_sale: Mapped[int] = mapped_column(Integer, default=1)

    mapping: Mapped["PlatformSkuMapping"] = relationship(back_populates="components")
    product: Mapped["Product"] = relationship(back_populates="platform_sku_components")


class AccountingPeriod(Base):
    """Internal monthly snapshot marker; it has no user-facing workflow."""
    __tablename__ = "accounting_periods"
    __table_args__ = (
        UniqueConstraint("period_start", "period_end", name="uq_accounting_periods_dates"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    status: Mapped[str] = mapped_column(String(16), default="auto_closed")
    snapshot_version: Mapped[int] = mapped_column(Integer, default=0)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    closed_by_user: Mapped["User | None"] = relationship(
        back_populates="closed_accounting_periods"
    )
    inventory_snapshots: Mapped[list["InventoryPeriodSnapshot"]] = relationship(
        back_populates="accounting_period", cascade="all, delete-orphan"
    )


class InventoryPeriodSnapshot(Base):
    """An immutable internal inventory snapshot for a completed calendar month."""
    __tablename__ = "inventory_period_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "accounting_period_id", "batch_id",
            name="uq_inventory_period_snapshots_period_batch",
        ),
        Index(
            "ix_inventory_period_snapshots_period_product",
            "accounting_period_id", "product_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accounting_period_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accounting_periods.id"), index=True
    )
    batch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inventory_batches.id"), index=True
    )
    product_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("products.id"), index=True
    )
    store_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stores.id"), nullable=True, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    unit_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))
    inventory_amount: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    impairment_rate: Mapped[Decimal] = mapped_column(DECIMAL(8, 6), default=0)
    impairment_amount: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    book_value: Mapped[Decimal] = mapped_column(DECIMAL(16, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    accounting_period: Mapped["AccountingPeriod"] = relationship(
        back_populates="inventory_snapshots"
    )
    batch: Mapped["InventoryBatch"] = relationship(back_populates="period_snapshots")


class StoreProfitLossReport(Base):
    """单店单月损益表中的人工填写项及最后修改信息。"""

    __tablename__ = "store_profit_loss_reports"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "report_month",
            name="uq_store_profit_loss_reports_store_month",
        ),
        Index("ix_store_profit_loss_reports_month", "report_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), index=True)
    report_month: Mapped[date] = mapped_column(Date)
    manual_values: Mapped[dict] = mapped_column(JSON, default=dict)
    remarks: Mapped[dict] = mapped_column(JSON, default=dict)
    # Keep the database column name for compatibility without shadowing the
    # Declarative API's reserved ``metadata`` attribute.
    report_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    updated_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class InventoryCostAdjustment(Base):
    """已确认成本补差；反向单保留原单，历史销售按当前已知成本重述。"""
    __tablename__ = "inventory_cost_adjustments"
    __table_args__ = (UniqueConstraint("category", "source_ref", name="uq_cost_adjustment_source"), {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_ref: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))
    allocation_basis: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(DECIMAL(16, 4))
    currency: Mapped[str] = mapped_column(String(3))
    exchange_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 8))
    rate_date: Mapped[date] = mapped_column(Date)
    amount_cny: Mapped[Decimal] = mapped_column(DECIMAL(16, 4))
    document_date: Mapped[date] = mapped_column(Date)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(1000), default="")
    reverses_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("inventory_cost_adjustments.id"), unique=True, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class InventoryCostAdjustmentLine(Base):
    __tablename__ = "inventory_cost_adjustment_lines"
    __table_args__ = (UniqueConstraint("adjustment_id", "batch_id", name="uq_cost_adjustment_batch"), {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    adjustment_id: Mapped[int] = mapped_column(Integer, ForeignKey("inventory_cost_adjustments.id"), index=True)
    batch_id: Mapped[int] = mapped_column(Integer, ForeignKey("inventory_batches.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    allocation_weight: Mapped[Decimal] = mapped_column(DECIMAL(20, 8))
    amount_cny: Mapped[Decimal] = mapped_column(DECIMAL(16, 4))


class FinancialExpense(Base):
    __tablename__ = "financial_expenses"
    __table_args__ = (UniqueConstraint("store_id", "category", "source_ref", name="uq_financial_expense_source"), {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), index=True)
    expense_date: Mapped[date] = mapped_column(Date, index=True)
    category: Mapped[str] = mapped_column(String(64))
    source_ref: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(DECIMAL(16, 4))
    currency: Mapped[str] = mapped_column(String(3))
    exchange_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 8))
    amount_cny: Mapped[Decimal] = mapped_column(DECIMAL(16, 4))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    settlement_entry_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("settlement_entries.id"), nullable=True)
    reverses_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("financial_expenses.id"), unique=True, nullable=True)
    note: Mapped[str] = mapped_column(String(1000), default="")
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    posted_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class FinancialExpenseSource(Base):
    __tablename__ = "financial_expense_sources"
    __table_args__ = (UniqueConstraint("store_id", "report_month", "category", name="uq_expense_source_month"), {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), index=True)
    report_month: Mapped[date] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    updated_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class FinancialReceipt(Base):
    __tablename__ = "financial_receipts"
    __table_args__ = (UniqueConstraint("store_id", "source_ref", name="uq_financial_receipt_source"), {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), index=True)
    payout_id: Mapped[str] = mapped_column(String(128), index=True)
    receipt_date: Mapped[date] = mapped_column(Date)
    source_ref: Mapped[str] = mapped_column(String(128))
    amount: Mapped[Decimal] = mapped_column(DECIMAL(16, 4))
    currency: Mapped[str] = mapped_column(String(3))
    exchange_rate: Mapped[Decimal] = mapped_column(DECIMAL(18, 8))
    amount_cny: Mapped[Decimal] = mapped_column(DECIMAL(16, 4))
    note: Mapped[str] = mapped_column(String(1000), default="")
    reverses_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("financial_receipts.id"), unique=True, nullable=True)
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SettlementClassification(Base):
    __tablename__ = "settlement_classifications"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    settlement_entry_id: Mapped[int] = mapped_column(Integer, ForeignKey("settlement_entries.id"), unique=True)
    breakdown: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(1000), default="")
    confirmed_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FinancialReportVersion(Base):
    __tablename__ = "financial_report_versions"
    __table_args__ = (
        UniqueConstraint("store_id", "report_month", "version", name="uq_financial_version_number"),
        UniqueConstraint("store_id", "report_month", "source_digest", name="uq_financial_version_digest"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    store_id: Mapped[int] = mapped_column(Integer, ForeignKey("stores.id"), index=True)
    report_month: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer)
    source_digest: Mapped[str] = mapped_column(String(64))
    calculation_version: Mapped[str] = mapped_column(String(32), default="restated-cost-v1")
    status: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(1000), default="")
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FinancialAuditEvent(Base):
    __tablename__ = "financial_audit_events"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("stores.id"), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(32))
    before_values: Mapped[dict] = mapped_column(JSON, default=dict)
    after_values: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
