import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_BYTES, MIN_PASSWORD_LENGTH


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def _within_bcrypt_limit(cls, value: str) -> str:
        # NOT Field(max_length=...): that counts CHARACTERS, and bcrypt's limit is
        # BYTES. "가" * 72 is 72 characters but 216 bytes - it would pass schema
        # validation and then raise out of hash_password as a 500 instead of a 422.
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return value.strip().lower()


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str

    model_config = {"from_attributes": True}


class AdminUserResponse(UserResponse):
    """The user-management list. Kept separate from UserResponse, which is what
    /api/auth/me returns to every logged-in user - is_active and created_at are
    for the admin screen, not for advertising to the account itself."""

    is_active: bool
    created_at: datetime


class UserUpdate(BaseModel):
    """PATCH body, dumped with `exclude_unset=True, exclude_none=True`. Both
    columns are NOT NULL, so an explicit null can only mean "no change" here -
    unlike CollectionUpdate.description, where null is a real value."""

    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None
