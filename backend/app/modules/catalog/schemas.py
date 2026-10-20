"""Catalog request and response schemas."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Category name cannot be blank")
        return name


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("Supply non-null category fields to update")
        if self.name is not None and not self.name.strip():
            raise ValueError("Category name cannot be blank")
        return self

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CategoryRead(BaseModel):
    id: int
    company_id: int
    name: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    kind: Literal["goods", "service"]
    unit_code: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z0-9_-]+$")
    price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    category_id: int | None = Field(default=None, gt=0)
    allows_fractional_quantity: bool = False

    @field_validator("sku", "name", "unit_code")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Field cannot be blank")
        return normalized

    @field_validator("sku", mode="before")
    @classmethod
    def uppercase_sku(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    kind: Literal["goods", "service"] | None = None
    unit_code: str | None = Field(default=None, min_length=1, max_length=16, pattern=r"^[A-Z0-9_-]+$")
    price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    category_id: int | None = Field(default=None, gt=0)
    is_active: bool | None = None
    allows_fractional_quantity: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one product field must be supplied")
        if any(getattr(self, key) is None for key in self.model_fields_set if key not in {"description", "category_id"}):
            raise ValueError("Product fields cannot be null")
        if self.name is not None and not self.name.strip():
            raise ValueError("Product name cannot be blank")
        if self.sku is not None and not self.sku.strip():
            raise ValueError("Product SKU cannot be blank")
        return self

    @field_validator("sku", mode="before")
    @classmethod
    def uppercase_sku(cls, value: str | None) -> str | None:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("sku", "name", "unit_code")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProductRead(BaseModel):
    id: int
    company_id: int
    category_id: int | None
    sku: str
    name: str
    description: str | None
    kind: str
    unit_code: str
    price: Decimal
    tax_rate: Decimal
    is_active: bool
    allows_fractional_quantity: bool = False

    model_config = ConfigDict(from_attributes=True)
