from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, Integer, DECIMAL, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="operator")  # admin / operator / viewer
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sales: Mapped[list["Sale"]] = relationship(back_populates="user")
    batches: Mapped[list["InventoryBatch"]] = relationship(back_populates="user")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    image: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 图片路径
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    batches: Mapped[list["InventoryBatch"]] = relationship(back_populates="product")
    sales: Mapped[list["Sale"]] = relationship(back_populates="product")


class InventoryBatch(Base):
    """入库批次 - 每批到货单独记录，含运费的真实成本"""
    __tablename__ = "inventory_batches"

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


class Sale(Base):
    """销售记录 - 每笔订单"""
    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=True)  # 创建者
    order_no: Mapped[str] = mapped_column(String(64), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    selling_price: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # 销售单价
    total_cost: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # FIFO算出的总成本
    platform_fee: Mapped[Decimal] = mapped_column(DECIMAL(12, 4), default=0)  # 平台费用
    profit: Mapped[Decimal] = mapped_column(DECIMAL(12, 4))  # 利润
    sold_at: Mapped[datetime] = mapped_column(DateTime)  # 销售时间
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    product: Mapped["Product"] = relationship(back_populates="sales")
    user: Mapped["User"] = relationship(back_populates="sales")
    cost_details: Mapped[list["SaleCostDetail"]] = relationship(back_populates="sale")


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
