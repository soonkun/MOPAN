import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document


async def keyword_search(
    db: AsyncSession,
    query_text: str,
    limit: int,
    collection_ids: list[uuid.UUID] | None = None,
) -> list[str]:
    """The sparse half of hybrid retrieval: ordered chunk ids, best first.

    Returns ids, not rows, so it is symmetric with `VectorStore.search` and
    nothing ORM-shaped or Postgres-shaped reaches the fusion stage.
    """
    # 'simple' MUST match the regconfig in the generated content_tsv column (see
    # app/models/chunk.py). The failure mode is worse than a slow query and was
    # measured, not assumed: the index still gets used, it just answers wrong.
    # content_tsv stores what 'simple' produced ('tomatoes', 'blighted'), while
    # plainto_tsquery('english', ...) asks for the stems ('tomato', 'blight'),
    # which that row never stored - a silent false negative on every inflected
    # word, with a healthy-looking Bitmap Index Scan in the plan.
    ts_query = func.plainto_tsquery("simple", query_text)
    # is_comparison=True types the result as boolean; without it the expression
    # inherits TSVECTOR and only happens to render correctly in a WHERE clause.
    query = select(Chunk.id).where(Chunk.content_tsv.op("@@", is_comparison=True)(ts_query))
    if collection_ids is not None:
        # `is not None`, not truthiness: an empty list means "scoped to no
        # collection" and must return nothing, exactly as in PgVectorStore.search.
        # Reading [] as unscoped would widen a Slice 3 Super Agent query from zero
        # collections to every collection in the system.
        query = query.join(Document, Document.id == Chunk.document_id).where(
            Document.collection_id.in_(collection_ids)
        )
    # ts_rank is not indexable and never filters - the @@ predicate does that, and
    # ts_rank only orders the rows the index already returned. Tie-broken by id
    # because ts_rank scores two chunks carrying the same lexemes at the same
    # positions identically, and Postgres may return those in any order; an
    # unstable sparse ranking would make RRF's own tie-break non-reproducible.
    query = query.order_by(func.ts_rank(Chunk.content_tsv, ts_query).desc(), Chunk.id).limit(limit)

    # ponytail: a GIN index defaults to fastupdate=on, so the rows a bulk ingest
    # just wrote sit in the pending list and the planner costs the index high
    # enough to pick a Seq Scan instead - measured at 20k rows, 3.9ms vs 0.02ms,
    # until VACUUM flushed it. Autovacuum fixes it on its own and Slice 1's
    # corpora are small; if first-query latency after a large ingest ever
    # matters, VACUUM chunks at the end of the pipeline or set fastupdate=off.
    result = await db.scalars(query)
    return [str(chunk_id) for chunk_id in result]
