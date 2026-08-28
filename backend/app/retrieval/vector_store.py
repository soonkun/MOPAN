import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document


@dataclass
class VectorItem:
    document_id: uuid.UUID
    chunk_index: int
    content: str
    token_count: int
    char_count: int
    page: int | None = None
    section: str | None = None
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None


@dataclass(frozen=True)
class ScoredId:
    chunk_id: str
    score: float


class VectorStore(ABC):
    """The seam that lets Qdrant (or anything else) replace pgvector without
    touching the ingestion pipeline, the retrieval service, or the ORM.

    Everything crossing this boundary is a builtin or a uuid: no Vector column,
    no `<=>`, no distance operator, no SQL. A second backend implements these
    three methods and nothing else changes.
    """

    @abstractmethod
    async def upsert(self, items: list[VectorItem]) -> None:
        """Insert or replace chunks by (document_id, chunk_index).

        Items MUST be unique by (document_id, chunk_index); a duplicate raises
        ValueError. The precondition lives here, not only on PgVectorStore: a
        second backend that last-write-wins on a duplicate instead of raising
        restores exactly the per-backend divergence this interface removes.
        """

    @abstractmethod
    async def search(
        self,
        embedding: list[float],
        limit: int,
        collection_ids: list[uuid.UUID] | None = None,
    ) -> list[ScoredId]: ...

    @abstractmethod
    async def delete_by_document(self, document_id: uuid.UUID) -> None: ...


class PgVectorStore(VectorStore):
    """The only Slice 1 implementation. Does not commit - the caller owns the
    transaction boundary.

    That boundary is this seam's thinnest point, and it is relational: a remote
    store (Qdrant) has no session and no rollback, so a caller that relies on
    "upsert then rollback undoes the write" would silently keep the write after a
    backend swap. Treat writes as durable once issued; do not use rollback as an
    undo across this interface."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert(self, items: list[VectorItem]) -> None:
        """Items must be unique by (document_id, chunk_index).

        Postgres refuses to touch the same row twice in one ON CONFLICT statement
        (CardinalityViolationError), while Qdrant would last-write-win - so a
        duplicate is rejected here rather than behaving per-backend. Task 13
        enumerates chunk_index, so this is a contract guard, not a hot path.
        """
        if not items:
            return
        keys = [(item.document_id, item.chunk_index) for item in items]
        if len(set(keys)) != len(keys):
            raise ValueError("upsert items must be unique by (document_id, chunk_index)")
        # ponytail: one multi-VALUES statement, 10 bind params per item, against
        # Postgres's 65535-parameter cap - so ~6,500 chunks per call. Batch the
        # loop if a single document ever exceeds that.
        # A real upsert, not an insert. `chunks` carries UNIQUE (document_id,
        # chunk_index), so re-indexing a document without deleting it first would
        # otherwise die on a unique violation - while a Qdrant backend, which
        # overwrites by point id, would happily succeed. That difference is
        # exactly what this interface exists to hide.
        statement = insert(Chunk).values(
            [
                {
                    "id": uuid.uuid4(),
                    "document_id": item.document_id,
                    "chunk_index": item.chunk_index,
                    "content": item.content,
                    "token_count": item.token_count,
                    "char_count": item.char_count,
                    "page": item.page,
                    "section": item.section,
                    "chunk_metadata": item.metadata,
                    "embedding": item.embedding,
                }
                for item in items
            ]
        )
        # content_tsv is a generated column - Postgres maintains it, and naming it
        # here would be an error.
        # Core INSERT, so it bypasses the session identity map: a Chunk already
        # loaded in this session keeps its stale content until commit or expire.
        await self.db.execute(
            statement.on_conflict_do_update(
                constraint="uq_chunks_document_id",
                set_={
                    name: statement.excluded[name]
                    for name in (
                        "content",
                        "token_count",
                        "char_count",
                        "page",
                        "section",
                        "chunk_metadata",
                        "embedding",
                    )
                },
            )
        )

    async def search(
        self,
        embedding: list[float],
        limit: int,
        collection_ids: list[uuid.UUID] | None = None,
    ) -> list[ScoredId]:
        # cosine_distance emits the `<=>` operator, which is the only one
        # ix_chunks_embedding (HNSW, vector_cosine_ops) can serve. `<->` or `<#>`
        # would silently sequential-scan. Verified with EXPLAIN, not assumed.
        distance = Chunk.embedding.cosine_distance(embedding).label("distance")
        # A chunk whose embedding never landed distances to NULL, which ORDER BY
        # ASC puts last - so it only surfaces when there are fewer real rows than
        # `limit`, and then `float(None)` below raises. Exclude it here instead.
        query = select(Chunk.id, distance).where(Chunk.embedding.is_not(None))
        if collection_ids is not None:
            # `is not None` rather than truthiness: an empty list means "scoped to
            # no collection" and must return nothing. Reading it as "unscoped"
            # would widen a Slice 3 Super Agent query from zero collections to
            # every collection in the system.
            query = query.join(Document, Document.id == Chunk.document_id).where(
                Document.collection_id.in_(collection_ids)
            )
        query = query.order_by(distance).limit(limit)

        rows = (await self.db.execute(query)).all()
        # cosine_distance is 1 - cosine_similarity, so this hands back a plain
        # similarity in [-1.0, 1.0] and no pgvector distance convention leaks out.
        return [ScoredId(chunk_id=str(chunk_id), score=1.0 - float(dist)) for chunk_id, dist in rows]

    async def delete_by_document(self, document_id: uuid.UUID) -> None:
        await self.db.execute(delete(Chunk).where(Chunk.document_id == document_id))
