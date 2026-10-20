"""Company API schemas."""

from pydantic import BaseModel, ConfigDict, Field
from pydantic import field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class CompanyCreate(BaseModel):
    legal_name: str = Field(min_length=1, max_length=200)
    tax_id: str | None = Field(default=None, max_length=20)
    country_code: str = Field(default="MX", min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    currency_code: str = Field(default="MXN", min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    timezone: str = Field(default="America/Mexico_City", min_length=1, max_length=64)
    locale: str = Field(default="es-MX", min_length=2, max_length=16, pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")

    @field_validator("legal_name")
    @classmethod
    def normalize_legal_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Legal name cannot be blank")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class CompanySettingsUpdate(BaseModel):
    tax_id: str | None = Field(default=None, max_length=20)
    country_code: str | None = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Z]{2}$")
    currency_code: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    locale: str | None = Field(default=None, min_length=2, max_length=16, pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    allow_negative_stock: bool | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one setting must be supplied")
        if any(getattr(self, field) is None for field in self.model_fields_set if field != "tax_id"):
            raise ValueError("Settings cannot be null")
        return self

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class CompanyRead(BaseModel):
    id: int
    legal_name: str
    tax_id: str | None
    country_code: str
    currency_code: str
    timezone: str
    locale: str
    is_active: bool
    allow_negative_stock: bool = False

    model_config = ConfigDict(from_attributes=True)
