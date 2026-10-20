"""Branch API contracts."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BranchCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Z0-9][A-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Branch name cannot be blank")
        return normalized


class BranchUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=20, pattern=r"^[A-Z0-9][A-Z0-9_-]*$")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set:
            raise ValueError("At least one branch field must be supplied")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Branch fields cannot be null")
        if self.name is not None and not self.name.strip():
            raise ValueError("Branch name cannot be blank")
        if self.code is not None and not self.code.strip():
            raise ValueError("Branch code cannot be blank")
        return self

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class BranchRead(BaseModel):
    id: int
    company_id: int
    code: str
    name: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
