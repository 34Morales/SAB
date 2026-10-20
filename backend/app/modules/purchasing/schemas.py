"""Validated contracts for purchasing and accounts payable."""

from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SupplierCreate(BaseModel):
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
    internal_notes: str | None = Field(default=None, max_length=4000)

    @field_validator("code", "legal_name")
    @classmethod
    def trim_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value


class SupplierUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
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
    internal_notes: str | None = Field(default=None, max_length=4000)
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set if key not in {"contact_name", "email", "phone", "address", "city", "region", "postal_code", "tax_id", "internal_notes"}):
            raise ValueError("Supply valid non-null supplier fields")
        if self.code is not None:
            self.code = self.code.strip().upper()
            if not self.code:
                raise ValueError("Supplier code cannot be blank")
        if self.legal_name is not None:
            self.legal_name = self.legal_name.strip()
            if not self.legal_name:
                raise ValueError("Legal name cannot be blank")
        return self


class SupplierRead(BaseModel):
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
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class SupplierDetailRead(SupplierRead):
    internal_notes: str | None


class PurchaseOrderLineInput(BaseModel):
    product_id: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=240)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    discount_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)


class PurchaseOrderCreate(BaseModel):
    supplier_id: int = Field(gt=0)
    branch_id: int | None = Field(default=None, gt=0)
    warehouse_id: int = Field(gt=0)
    number: str = Field(min_length=1, max_length=40)
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[PurchaseOrderLineInput] = Field(min_length=1, max_length=200)

    @field_validator("number")
    @classmethod
    def trim_number(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Order number cannot be blank")
        return value


class PurchaseOrderUpdate(BaseModel):
    supplier_id: int | None = Field(default=None, gt=0)
    branch_id: int | None = Field(default=None, gt=0)
    warehouse_id: int | None = Field(default=None, gt=0)
    currency_code: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[PurchaseOrderLineInput] | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set if key not in {"notes", "branch_id"}):
            raise ValueError("Supply at least one non-null draft field")
        return self


class PurchaseOrderLineRead(BaseModel):
    id: int
    product_id: int
    description: str
    quantity: Decimal
    received_quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal
    tax_rate: Decimal
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    model_config = ConfigDict(from_attributes=True)


class PurchaseOrderRead(BaseModel):
    id: int
    company_id: int
    supplier_id: int
    branch_id: int | None
    warehouse_id: int
    number: str
    status: str
    currency_code: str
    notes: str | None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    created_by_user_id: int
    confirmed_by_user_id: int | None
    confirmed_at: datetime | None
    cancelled_by_user_id: int | None
    cancelled_at: datetime | None
    lines: list[PurchaseOrderLineRead]
    model_config = ConfigDict(from_attributes=True)


class ReceiptLineInput(BaseModel):
    purchase_order_line_id: int = Field(gt=0)
    accepted_quantity: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=6)
    damaged_quantity: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=6)
    location_id: int | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def positive_receipt(self):
        if self.accepted_quantity + self.damaged_quantity <= 0:
            raise ValueError("Receipt line must contain accepted or damaged quantity")
        return self


class ReceiptCreate(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[ReceiptLineInput] = Field(min_length=1, max_length=200)


class ReceiptLineRead(BaseModel):
    id: int
    purchase_order_line_id: int
    product_id: int
    location_id: int | None
    accepted_quantity: Decimal
    damaged_quantity: Decimal
    notes: str | None
    inventory_operation_id: int | None = None


class ReceiptRead(BaseModel):
    id: int
    purchase_order_id: int
    warehouse_id: int
    notes: str | None
    actor_user_id: int
    created_at: datetime
    lines: list[ReceiptLineRead]


class PurchaseOrderEventRead(BaseModel):
    id: int
    event_type: str
    actor_user_id: int
    details: dict[str, object]
    created_at: datetime


class SupplierReturnLineInput(BaseModel):
    receipt_line_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)


class SupplierReturnCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    lines: list[SupplierReturnLineInput] = Field(min_length=1, max_length=200)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Return reason is required")
        return value


class SupplierReturnLineRead(BaseModel):
    id: int
    receipt_line_id: int
    quantity: Decimal
    inventory_operation_id: int


class SupplierReturnRead(BaseModel):
    id: int
    receipt_id: int
    reason: str
    actor_user_id: int
    created_at: datetime
    lines: list[SupplierReturnLineRead]


class InvoiceLineInput(BaseModel):
    description: str = Field(min_length=1, max_length=240)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    purchase_order_line_id: int | None = Field(default=None, gt=0)
    receipt_line_id: int | None = Field(default=None, gt=0)


class SupplierInvoiceCreate(BaseModel):
    supplier_id: int = Field(gt=0)
    document_number: str = Field(min_length=1, max_length=80)
    purchase_order_id: int | None = Field(default=None, gt=0)
    receipt_id: int | None = Field(default=None, gt=0)
    issued_date: date
    due_date: date
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    notes: str | None = Field(default=None, max_length=4000)
    lines: list[InvoiceLineInput] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_dates(self):
        self.document_number = self.document_number.strip()
        if not self.document_number:
            raise ValueError("Document number cannot be blank")
        if self.due_date < self.issued_date:
            raise ValueError("Due date cannot precede issue date")
        return self


class SupplierInvoiceRead(BaseModel):
    id: int
    supplier_id: int
    purchase_order_id: int | None
    receipt_id: int | None
    document_number: str
    issued_date: date
    due_date: date
    currency_code: str
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    paid_amount: Decimal
    balance_due: Decimal
    status: str
    notes: str | None
    lines: list["SupplierInvoiceLineRead"] = Field(default_factory=list)


class SupplierInvoiceLineRead(BaseModel):
    id: int
    purchase_order_line_id: int | None
    receipt_line_id: int | None
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal


SupplierInvoiceRead.model_rebuild()


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
    actor_user_id: int
    created_at: datetime
