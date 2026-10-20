"""Validated inventory API contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WarehouseCreate(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    branch_id: int | None = Field(default=None, gt=0)


class WarehouseRead(BaseModel):
    id: int
    company_id: int
    branch_id: int | None
    code: str
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class LocationCreate(BaseModel):
    warehouse_id: int = Field(gt=0)
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)


class LocationRead(BaseModel):
    id: int
    company_id: int
    warehouse_id: int
    code: str
    name: str
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class InventoryOperationRequest(BaseModel):
    kind: Literal["receipt", "issue", "transfer", "adjustment_positive", "adjustment_negative"]
    product_id: int = Field(gt=0)
    warehouse_id: int = Field(gt=0)
    destination_warehouse_id: int | None = Field(default=None, gt=0)
    location_id: int | None = Field(default=None, gt=0)
    destination_location_id: int | None = Field(default=None, gt=0)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6)
    reason: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)
    reference_type: str | None = Field(default=None, max_length=60)
    reference_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_operation(self):
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("A reason is required")
        if (self.kind == "transfer") != (self.destination_warehouse_id is not None):
            raise ValueError("Transfers require a destination warehouse; other operations forbid it")
        if self.kind == "transfer" and self.destination_warehouse_id == self.warehouse_id:
            raise ValueError("Source and destination warehouses must differ")
        if self.kind.startswith("adjustment") and not self.reason:
            raise ValueError("Adjustments require a reason")
        return self


class BalanceRead(BaseModel):
    product_id: int
    warehouse_id: int
    location_id: int | None
    quantity: Decimal
    minimum_quantity: Decimal
    reorder_point: Decimal


class MovementRead(BaseModel):
    id: int
    operation_id: int
    product_id: int
    warehouse_id: int
    location_id: int | None
    movement_type: str
    direction: int
    quantity: Decimal
    unit_cost: Decimal | None
    created_at: datetime
    reason: str
    notes: str | None
    reference_type: str | None
    reference_id: str | None
    actor_user_id: int


class OperationRead(BaseModel):
    id: int
    kind: str
    movements: list[MovementRead]
