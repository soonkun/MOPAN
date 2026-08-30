import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ATTACHMENT_KINDS = ("image", "document")


class Attachment(Base):
    """A file attached to ONE user's chat turn - deliberately not part of the
    shared RAG corpus. That is why any authenticated user may create one while
    POST /api/documents is admin-only: a corpus document becomes the evidence base
    for every other user's answers, so writing there is a corpus-poisoning vector,
    whereas an attachment can only ever influence its own owner's answer."""

    __tablename__ = "attachments"
    __table_args__ = (CheckConstraint("kind in ('image', 'document')", name="ck_attachments_kind_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The only nullable FK in this schema, and the orphan story rests on it: the
    # composer has to show a thumbnail before the message exists, so a file is
    # stored first and claimed onto its message afterwards. NULL therefore means
    # "uploaded, never sent", and `message_id IS NULL AND created_at < now() -
    # <ttl>` is already a complete cleanup predicate - so no expires_at column and
    # no second migration when a cleanup job is finally written.
    # tests/test_schema.py:test_every_foreign_key_is_indexed_and_not_null carries
    # this one pair as its single documented exception.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Extracted at UPLOAD time, not at answer time: the user is already waiting on
    # the model then, and a 40-page PDF parse is seconds of that wait. NULL for
    # kind 'image' - those reach the model as image parts, not as text.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
