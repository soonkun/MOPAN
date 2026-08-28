import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Computed

from app.core.config import get_settings
from app.models.base import Base

# Single source of truth for the vector width. The migration and the tests read
# the same value; app startup verifies it against the deployed column.
EMBEDDING_DIM = get_settings().embedding_dim


class Chunk(Base):
    __tablename__ = "chunks"
    # Both retrieval indexes MUST be declared here. If they live only in the
    # migration, the next `alembic revision --autogenerate` emits DROP INDEX for
    # them and silently destroys hybrid retrieval.
    __table_args__ = (
        Index("ix_chunks_document_id", "document_id"),
        Index("ix_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Database-maintained generated column. Application code never writes it.
    # Typed Any because it holds a TSVECTOR, not a str.
    #
    # The explicit ::regconfig cast is how Postgres itself stores and reflects
    # this expression. Writing it any other way makes alembic's computed-default
    # comparison warn "Computed default on chunks.content_tsv cannot be modified"
    # on every autogenerate and every run of the drift test.
    #
    # nullable is stated explicitly on purpose. Left implicit, alembic suppresses
    # any nullability difference on a computed column ("Ignoring nullable change
    # on identity column") and compare_metadata returns an empty diff even when
    # the ORM and the database genuinely disagree.
    content_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, content)", persisted=True),
        nullable=False,
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(500), nullable=True)
    chunk_metadata: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
