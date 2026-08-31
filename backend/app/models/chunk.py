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
    event,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.models.base import Base
from app.retrieval.tokenize import tokenize

# Single source of truth for the vector width. The migration and the tests read
# the same value; app startup verifies it against the deployed column.
EMBEDDING_DIM = get_settings().embedding_dim


def sparse_tsvector(content: str, tokenizer: str | None = None):
    """The SQL expression every writer of `chunks.content_tsv` must use.

    ONE function, named here, because there are exactly two writers -
    `PgVectorStore.upsert` and `scripts/backfill_tsv.py` - and they must produce
    identical vectors or the sparse arm ranks a backfilled chunk differently from
    a freshly ingested one.

    `to_tsvector`, not a `'a b c'::tsvector` literal. The literal parses without
    POSITIONS, and ts_rank reads term frequency out of positions: measured on this
    database, a chunk containing a token three times ranks 0.0828 with positions
    and 0.0608 without. The literal would have silently flattened every repeated
    term to frequency 1 - the ranking would still look plausible and would not be
    the one the redesign was measured on.

    The regconfig is a `literal_column` and the tokens are the only BOUND value.
    That is deliberate on both counts: 'simple' is a constant in this file and can
    never be user input, while the tokens must stay bound so a token can never be
    read as tsvector syntax.

    This was briefly a `TypeDecorator.bind_expression` on the column instead, so
    that no writer could forget it. That is the tidier idea and it does not work:
    it adds a second bind parameter per row, and SQLAlchemy's insertmanyvalues
    compiler asserts that the positions it expands per row are contiguous. The
    failure is a bare `AssertionError` inside compiler.py naming no column, plus a
    `KeyError: 'chunks.id_m0'` on the ON CONFLICT path. Two named call sites beat
    one clever one that breaks the ORM's bulk paths.
    """
    return func.to_tsvector(
        literal_column("'simple'"),
        # `tokenizer` is an override for scripts/backfill_tsv.py, which must be
        # able to rebuild the index for a tokenizer OTHER than the configured one
        # - that is how you migrate between them, and how you roll one back.
        " ".join(tokenize(content, tokenizer or get_settings().sparse_tokenizer)),
    )


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
    # APPLICATION-WRITTEN as of migration 0012. It used to be
    # `Computed("to_tsvector('simple'::regconfig, content)", persisted=True)` -
    # Postgres maintained it and no application code touched it. It cannot stay
    # that way: the sparse arm now tokenizes Korean with character bigrams
    # (app/retrieval/tokenize.py), Postgres ships no Korean tokenizer and this
    # deployment cannot install one (pg_available_extensions offers only pg_trgm,
    # unaccent and vector), and a GENERATED column may only call IMMUTABLE SQL -
    # so the tokenizer has to run in Python, which means the value has to be
    # written, not generated.
    #
    # Typed Any because it holds a TSVECTOR, not a str.
    #
    # NOT NULL, and filled by `sparse_tsvector()` - explicitly in
    # PgVectorStore.upsert (the ingest path, a Core insert) and by the
    # before_insert listener below for anything constructed as an ORM object.
    #
    # nullable is still stated explicitly. It mattered under Computed (alembic
    # suppresses nullability diffs on computed columns and compare_metadata
    # returned an empty diff even on a genuine disagreement); it is cheap to keep
    # and the drift test now actually sees it.
    content_tsv: Mapped[Any] = mapped_column(TSVECTOR, nullable=False)
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


@event.listens_for(Chunk, "before_insert", propagate=True)
def _fill_content_tsv(mapper, connection, target) -> None:
    """Tokenise on the way in for anything built as an ORM `Chunk(...)`.

    The ingest path does NOT rely on this - `PgVectorStore.upsert` is a Core
    insert, which bypasses ORM events entirely, and it names `content_tsv`
    itself. This listener exists for the other constructor: tests and fixtures
    that build a Chunk object and add it to a session. Without it every one of
    them has to remember a column that is NOT NULL, and the failure when they
    forget is an IntegrityError naming a column they did not know existed.

    Assigning a SQL expression to the attribute is deliberate: SQLAlchemy renders
    it inline and, because the value is an expression rather than a literal, drops
    that flush out of the batched insertmanyvalues path. That is the whole reason
    this is a listener and not `TypeDecorator.bind_expression`, which was tried
    first: bind_expression adds a SECOND bind parameter per row, and the
    insertmanyvalues compiler asserts the positions it expands per row are
    contiguous. It fails as a bare AssertionError inside compiler.py naming no
    column, and as `KeyError: 'chunks.id_m0'` on the ON CONFLICT path.
    """
    if target.content_tsv is None:
        target.content_tsv = sparse_tsvector(target.content)
