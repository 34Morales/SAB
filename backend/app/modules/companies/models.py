"""Initial company and branch data model."""

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database.base import Base, TimestampMixin


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (Index("ix_companies_active", "is_active"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str] = mapped_column(String(2), default="MX", nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), default="MXN", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="America/Mexico_City", nullable=False)
    locale: Mapped[str] = mapped_column(String(16), default="es-MX", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_negative_stock: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    branches: Mapped[list["Branch"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Branch(TimestampMixin, Base):
    __tablename__ = "branches"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_branches_company_code"),
        Index("ix_branches_company_active", "company_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    company: Mapped[Company] = relationship(back_populates="branches")
