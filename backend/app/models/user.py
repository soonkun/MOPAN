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
