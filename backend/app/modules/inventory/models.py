"""Inventory persistence; stock movements are immutable source-of-truth rows."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin


class Warehouse(TimestampMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_warehouses_company_code"),
        Index("ix_warehouses_company_active", "company_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class InventoryLocation(TimestampMixin, Base):
    __tablename__ = "inventory_locations"
    __table_args__ = (UniqueConstraint("warehouse_id", "code", name="uq_inventory_locations_warehouse_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class InventoryOperation(TimestampMixin, Base):
    __tablename__ = "inventory_operations"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_inventory_operation_idempotency"),
        CheckConstraint("kind IN ('receipt','issue','transfer','adjustment_positive','adjustment_negative')", name="ck_inventory_operation_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(60))
    reference_id: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)


class InventoryBalance(Base):
    """Atomic balance projection; rebuildable from inventory_movements."""
    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint("company_id", "product_id", "warehouse_id", "location_id", name="uq_inventory_balance_position"),
        CheckConstraint("minimum_quantity >= 0 AND reorder_point >= 0", name="ck_inventory_thresholds_nonnegative"),
        Index("ix_inventory_balances_company_product", "company_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_locations.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"), nullable=False)
    minimum_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"), nullable=False)
    reorder_point: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"), nullable=False)


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_inventory_movement_positive_quantity"),
        Index("ix_inventory_movements_company_created", "company_id", "created_at"),
        Index("ix_inventory_movements_product_warehouse", "product_id", "warehouse_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    operation_id: Mapped[int] = mapped_column(ForeignKey("inventory_operations.id", ondelete="RESTRICT"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_locations.id", ondelete="RESTRICT"))
    movement_type: Mapped[str] = mapped_column(String(24), nullable=False)
    direction: Mapped[int] = mapped_column(nullable=False)  # +1 or -1
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    operation: Mapped[InventoryOperation] = relationship()
