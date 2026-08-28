import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Computed
from sqlalchemy.sql import func

from app.models.base import Base


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True)
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(500), nullable=True)
    chunk_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
