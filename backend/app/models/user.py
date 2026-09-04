import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

USER_ROLES = ("admin", "user")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Email is normalised to lowercase in the auth service; this makes the
        # invariant real at the database level too, so a raw INSERT cannot create
        # a case-variant duplicate.
        CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
        CheckConstraint("role in ('admin', 'user')", name="ck_users_role_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # 호칭. 새 대화 첫 화면과 잡담 응답이 "OO님"이라고 부를 때 쓰는 값이고,
    # 본인이 프로필에서 고친다. NULL이면 부르지 않는다 - 이메일 앞부분을
    # 어림해 부르는 것은 호칭이 아니라 추측이다.
    nickname: Mapped[str | None] = mapped_column(String(60), nullable=True)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", server_default=text("'user'")
    )
    # Deactivation is the only way to take an account away: rows are never deleted
    # because documents.uploaded_by and collections.created_by are ON DELETE
    # RESTRICT, so a DELETE would either fail or take the shared corpus with it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
