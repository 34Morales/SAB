"""Company-scoped product and category persistence models."""

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin


class CatalogCategory(TimestampMixin, Base):
    __tablename__ = "catalog_categories"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_catalog_categories_company_name"),
        Index("ix_catalog_categories_company_active", "company_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("company_id", "sku", name="uq_products_company_sku"),
        CheckConstraint("price >= 0", name="ck_products_nonnegative_price"),
        CheckConstraint("tax_rate >= 0 AND tax_rate <= 100", name="ck_products_tax_rate_range"),
        CheckConstraint("kind IN ('goods', 'service')", name="ck_products_kind"),
        Index("ix_products_company_active", "company_id", "is_active"),
        Index("ix_products_company_category", "company_id", "category_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_categories.id", ondelete="SET NULL"))
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)
    allows_fractional_quantity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    category: Mapped[CatalogCategory | None] = relationship(back_populates="products")
