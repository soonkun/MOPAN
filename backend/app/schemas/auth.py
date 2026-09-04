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
    nickname: str | None = None

    model_config = {"from_attributes": True}


class ProfileUpdateRequest(BaseModel):
    # None = 그대로 두기, "" = 지우기. 프런트는 지금 nickname만 보내지만 PATCH
    # 의미론을 처음부터 지켜 둔다 - 생략된 키는 건드리지 않는다.
    nickname: str | None = None


class DeleteAccountRequest(BaseModel):
    # 파괴적 동작은 세션 쿠키만으로 충분하지 않다. 자리를 비운 화면에서 클릭
    # 몇 번으로 계정이 사라지면 안 되므로 비밀번호를 다시 받는다.
    password: str


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
