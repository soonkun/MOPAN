import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

DOCUMENT_STATUSES = ("uploaded", "parsing", "chunking", "embedding", "indexed", "failed")
TERMINAL_STATUSES = ("indexed", "failed")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status in ('uploaded', 'parsing', 'chunking', 'embedding', 'indexed', 'failed')",
            name="ck_documents_status_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="uploaded", server_default=text("'uploaded'")
    )
    # User-facing text only. Tracebacks go to the logs, never to this column -
    # it is rendered in the Documents UI.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # WHAT WAS INFERRED ABOUT THIS DOCUMENT, and what a person said about it.
    # `{}` - the default - means nothing has been inferred yet, which is every
    # document whose collection asks for prose or for the classification table.
    #
    # Per DOCUMENT, not per collection, and that is the point. The `일반`
    # collection of this deployment already holds 특허·실용신안 심사기준
    # (reference-dependent) beside 연구보고서 A and 농약 안전사용 지침
    # (self-contained); a collection-wide verdict is guaranteed wrong there.
    #
    # It is written by the pipeline and READ BY THE UI. That is not decoration:
    # this project already shipped one silent automatic decision - a chunking
    # strategy that selected itself by sniffing for a hardcoded Korean-IP regex -
    # and migration 0013 exists to undo it. An inference nobody can see is the
    # worst case, so the counts that produced the verdict are stored beside it and
    # rendered, and `override` lets a person disagree. `detected` is kept
    # separately from `character` so the screen can always show both.
    #
    # JSONB rather than columns for the reason `collections.chunking` is: the keys
    # belong to the detector (`app/rag/chunking/hierarchy.py:Detection.as_json`),
    # and the next document character will count something else.
    structure: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
