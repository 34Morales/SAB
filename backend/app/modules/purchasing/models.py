"""Company-scoped purchasing documents and payables."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_suppliers_company_code"),
        Index("ix_suppliers_company_active_name", "company_id", "is_active", "legal_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    postal_code: Mapped[str | None] = mapped_column(String(24))
    country_code: Mapped[str] = mapped_column(String(2), default="MX", nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(40))
    payment_terms_days: Mapped[int] = mapped_column(default=0, nullable=False)
    preferred_currency: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    internal_notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("company_id", "number", name="uq_purchase_orders_company_number"),
        CheckConstraint("status IN ('draft','confirmed','cancelled','closed')", name="ck_purchase_orders_status"),
        Index("ix_purchase_orders_company_status_created", "company_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    confirmed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(order_by="PurchaseOrderLine.id", cascade="all, delete-orphan")


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0 AND received_quantity >= 0 AND received_quantity <= quantity", name="ck_purchase_order_line_quantities"),
        CheckConstraint("unit_price >= 0 AND discount_percent >= 0 AND discount_percent <= 100 AND tax_rate >= 0 AND tax_rate <= 100", name="ck_purchase_order_line_rates"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    description: Mapped[str] = mapped_column(String(240), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)


class PurchaseOrderEvent(Base):
    __tablename__ = "purchase_order_events"
    __table_args__ = (Index("ix_purchase_order_events_order_created", "purchase_order_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PurchaseReceipt(Base):
    __tablename__ = "purchase_receipts"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_purchase_receipt_idempotency"),
        Index("ix_purchase_receipts_company_order", "company_id", "purchase_order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lines: Mapped[list["PurchaseReceiptLine"]] = relationship(order_by="PurchaseReceiptLine.id", cascade="all, delete-orphan")


class PurchaseReceiptLine(Base):
    __tablename__ = "purchase_receipt_lines"
    __table_args__ = (CheckConstraint("accepted_quantity >= 0 AND damaged_quantity >= 0 AND accepted_quantity + damaged_quantity > 0", name="ck_purchase_receipt_line_quantities"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("purchase_receipts.id", ondelete="RESTRICT"), nullable=False)
    purchase_order_line_id: Mapped[int] = mapped_column(ForeignKey("purchase_order_lines.id", ondelete="RESTRICT"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_locations.id", ondelete="RESTRICT"))
    accepted_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    damaged_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500))
    inventory_operation_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_operations.id", ondelete="RESTRICT"))


class SupplierReturn(Base):
    __tablename__ = "supplier_returns"
    __table_args__ = (UniqueConstraint("company_id", "idempotency_key", name="uq_supplier_return_idempotency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("purchase_receipts.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lines: Mapped[list["SupplierReturnLine"]] = relationship(order_by="SupplierReturnLine.id", cascade="all, delete-orphan")


class SupplierReturnLine(Base):
    __tablename__ = "supplier_return_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_supplier_return_line_quantity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_return_id: Mapped[int] = mapped_column(ForeignKey("supplier_returns.id", ondelete="RESTRICT"), nullable=False)
    receipt_line_id: Mapped[int] = mapped_column(ForeignKey("purchase_receipt_lines.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    inventory_operation_id: Mapped[int] = mapped_column(ForeignKey("inventory_operations.id", ondelete="RESTRICT"), nullable=False)


class SupplierInvoice(TimestampMixin, Base):
    __tablename__ = "supplier_invoices"
    __table_args__ = (
        UniqueConstraint("company_id", "supplier_id", "document_number", name="uq_supplier_invoice_document"),
        CheckConstraint("status IN ('unpaid','partial','paid','void')", name="ck_supplier_invoice_status"),
        CheckConstraint("total >= 0 AND paid_amount >= 0 AND paid_amount <= total", name="ck_supplier_invoice_amounts"),
        Index("ix_supplier_invoices_company_due", "company_id", "status", "due_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False)
    purchase_order_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"))
    receipt_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_receipts.id", ondelete="RESTRICT"))
    document_number: Mapped[str] = mapped_column(String(80), nullable=False)
    issued_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="unpaid", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    lines: Mapped[list["SupplierInvoiceLine"]] = relationship(order_by="SupplierInvoiceLine.id", cascade="all, delete-orphan")


class SupplierInvoiceLine(Base):
    __tablename__ = "supplier_invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("supplier_invoices.id", ondelete="RESTRICT"), nullable=False)
    purchase_order_line_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order_lines.id", ondelete="RESTRICT"))
    receipt_line_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_receipt_lines.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(240), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)


class SupplierPayment(Base):
    __tablename__ = "supplier_payments"
    __table_args__ = (UniqueConstraint("company_id", "idempotency_key", name="uq_supplier_payment_idempotency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("supplier_invoices.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(120))
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
