import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.attachment import Attachment
from app.models.base import Base

MESSAGE_ROLES = ("user", "assistant")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # viewonly: the claim is a single conditional UPDATE (app/attachments/service.py)
    # whose `message_id IS NULL` predicate is the double-claim guard, and a writable
    # relationship would offer a second path that skips it. lazy="selectin" because
    # MessageResponse serialises this and the session is async, where a lazy load
    # at attribute access raises MissingGreenlet.
    attachments: Mapped[list[Attachment]] = relationship(
        lazy="selectin", order_by=Attachment.created_at, viewonly=True
    )

    # Observability seam (Slice 5 reads these; Slice 4 fills prompt_version).
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    usage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # clock_timestamp(), NOT now(): now() is transaction start time, so the user
    # and assistant messages written in one commit would share a timestamp and
    # history ordering would be non-deterministic.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
