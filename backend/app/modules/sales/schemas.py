"""Request and response contracts for company-scoped sales."""

from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CustomerCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    legal_name: str = Field(min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=24)
    country_code: str = Field(default="MX", pattern=r"^[A-Z]{2}$")
    tax_id: str | None = Field(default=None, max_length=40)
    payment_terms_days: int = Field(default=0, ge=0, le=3650)
    preferred_currency: str = Field(default="MXN", pattern=r"^[A-Z]{3}$")
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)

    @field_validator("code", "legal_name")
    @classmethod
    def trim_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value


class CustomerUpdate(BaseModel):
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=24)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    tax_id: str | None = Field(default=None, max_length=40)
    payment_terms_days: int | None = Field(default=None, ge=0, le=3650)
    preferred_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    is_active: bool | None = None

    @model_validator(mode="after")
    def valid_patch(self):
        nullable = {"contact_name", "email", "phone", "address", "city", "region", "postal_code", "tax_id", "credit_limit"}
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set if k not in nullable):
            raise ValueError("Supply valid customer fields")
        if self.legal_name is not None:
            self.legal_name = self.legal_name.strip()
            if not self.legal_name:
                raise ValueError("Customer name cannot be blank")
        return self


class CustomerRead(BaseModel):
    id: int
    company_id: int
    code: str
    legal_name: str
    contact_name: str | None
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    region: str | None
    postal_code: str | None
    country_code: str
    tax_id: str | None
    payment_terms_days: int
    preferred_currency: str
    credit_limit: Decimal | None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class SalesLineInput(BaseModel):
    product_id: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=240)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)


class QuoteCreate(BaseModel):
    customer_id: int = Field(gt=0)
    number: str = Field(min_length=1, max_length=40)
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[SalesLineInput] = Field(min_length=1, max_length=200)


class QuoteUpdate(BaseModel):
    customer_id: int | None = Field(default=None, gt=0)
    currency_code: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[SalesLineInput] | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set if k not in {"notes", "valid_until"}):
            raise ValueError("Supply draft quote fields")
        return self


class QuoteConvert(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    warehouse_id: int = Field(gt=0)
    branch_id: int | None = Field(default=None, gt=0)

    @field_validator("number")
    @classmethod
    def trim_number(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Order number cannot be blank")
        return value


class SalesLineRead(BaseModel):
    id: int
    product_id: int
    description: str
    quantity: Decimal
    fulfilled_quantity: Decimal = Decimal("0")
    unit_price: Decimal
    discount_percent: Decimal
    tax_rate: Decimal
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    model_config = ConfigDict(from_attributes=True)


class QuoteRead(BaseModel):
    id: int
    company_id: int
    customer_id: int
    number: str
    status: str
    currency_code: str
    valid_until: date | None
    notes: str | None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    lines: list[SalesLineRead]
    model_config = ConfigDict(from_attributes=True)


class OrderCreate(BaseModel):
    customer_id: int = Field(gt=0)
    branch_id: int | None = Field(default=None, gt=0)
    warehouse_id: int = Field(gt=0)
    number: str = Field(min_length=1, max_length=40)
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[SalesLineInput] = Field(min_length=1, max_length=200)


class OrderRead(BaseModel):
    id: int
    company_id: int
    customer_id: int
    quote_id: int | None
    branch_id: int | None
    warehouse_id: int
    number: str
    status: str
    currency_code: str
    notes: str | None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    lines: list[SalesLineRead]
    model_config = ConfigDict(from_attributes=True)


class DeliveryLineInput(BaseModel):
    order_line_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    location_id: int | None = Field(default=None, gt=0)


class DeliveryCreate(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[DeliveryLineInput] = Field(min_length=1, max_length=200)


class DeliveryLineRead(BaseModel):
    id: int
    order_line_id: int
    quantity: Decimal
    inventory_operation_id: int


class DeliveryRead(BaseModel):
    id: int
    order_id: int
    warehouse_id: int
    notes: str | None
    created_at: datetime
    lines: list[DeliveryLineRead]


class ReturnCreate(BaseModel):
    delivery_line_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    reason: str = Field(min_length=1, max_length=500)
    invoice_id: int | None = Field(default=None, gt=0)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Return reason required")
        return value


class ReturnRead(BaseModel):
    id: int
    delivery_line_id: int
    quantity: Decimal
    reason: str
    inventory_operation_id: int


class InvoiceCreate(BaseModel):
    order_id: int = Field(gt=0)
    number: str = Field(min_length=1, max_length=80)
    issued_date: date
    due_date: date
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_dates(self):
        self.number = self.number.strip()
        if not self.number or self.due_date < self.issued_date:
            raise ValueError("Invoice number and dates are invalid")
        return self


class InvoiceRead(BaseModel):
    id: int
    customer_id: int
    order_id: int
    number: str
    issued_date: date
    due_date: date
    currency_code: str
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    paid_amount: Decimal
    credited_amount: Decimal
    balance_due: Decimal
    status: str
    notes: str | None
    lines: list[dict]


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    paid_on: date
    reference: str | None = Field(default=None, max_length=120)


class PaymentRead(BaseModel):
    id: int
    invoice_id: int
    amount: Decimal
    paid_on: date
    reference: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class StockAvailabilityRead(BaseModel):
    product_id: int
    warehouse_id: int
    physical_quantity: Decimal
    reserved_quantity: Decimal
    available_quantity: Decimal
