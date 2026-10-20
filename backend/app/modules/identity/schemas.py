"""Identity API contracts."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from datetime import datetime


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=1, max_length=1024)
    otp_code: str | None = Field(default=None, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(default=None, min_length=12, max_length=32)


class SessionRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=12, max_length=1024)
    role_id: int = Field(gt=0)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Full name cannot be blank")
        return normalized


class UserRead(BaseModel):
    id: int
    email: str
    full_name: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class RoleRead(BaseModel):
    id: int
    name: str
    permissions: list[str]


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    permissions: list[str] = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_role_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Role name cannot be blank")
        return normalized

    @field_validator("permissions")
    @classmethod
    def unique_permissions(cls, value: list[str]) -> list[str]:
        normalized = [permission.strip() for permission in value]
        if any(not permission for permission in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Permission codes must be nonblank and unique")
        return normalized


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    permissions: list[str] | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Supply non-null role fields to update")
        if self.name is not None and not self.name.strip():
            raise ValueError("Role name cannot be blank")
        if self.permissions is not None:
            normalized = [permission.strip() for permission in self.permissions]
            if any(not permission for permission in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError("Permission codes must be nonblank and unique")
            self.permissions = normalized
        if self.name is not None:
            self.name = self.name.strip()
        return self


class MembershipUpdate(BaseModel):
    is_active: bool | None = None
    role_ids: list[int] | None = None

    @field_validator("role_ids")
    @classmethod
    def unique_role_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and (any(role_id <= 0 for role_id in value) or len(set(value)) != len(value)):
            raise ValueError("Role IDs must be positive and unique")
        return value


class MembershipRead(BaseModel):
    id: int
    user_id: int
    company_id: int
    is_active: bool
    roles: list[RoleRead]


class MfaCodeRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    otp_code: str | None = Field(default=None, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(default=None, min_length=12, max_length=32)


class RecoveryRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: str = Field(min_length=12, max_length=1024)


class RecoveryAccepted(BaseModel):
    message: str


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MfaEnabledResponse(BaseModel):
    enabled: bool
    recovery_codes: list[str]


class SecurityEventRead(BaseModel):
    id: int
    user_id: int | None
    company_id: int | None
    event_type: str
    source_ip: str | None
    details: dict[str, object]
    created_at: datetime
