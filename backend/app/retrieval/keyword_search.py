import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.models.document import Document

# 'simple' MUST match the regconfig in the generated content_tsv column (see
# app/models/chunk.py). The failure mode is worse than a slow query and was
# measured, not assumed: the index still gets used, it just answers wrong.
# content_tsv stores what 'simple' produced ('tomatoes', 'blighted'), while
# plainto_tsquery('english', ...) asks for the stems ('tomato', 'blight'),
# which that row never stored - a silent false negative on every inflected
# word, with a healthy-looking Bitmap Index Scan in the plan. Measured on a
# seeded corpus, 'english' against this column hit 0 of 13 questions.
#
# So the query is built out of the SAME 'simple' lexemes the column holds, then
# OR-joined. plainto_tsquery (and websearch_to_tsquery, which was measured
# identically) ANDs every term, so "How does tomato blight spread?" asked for
# 'how' & 'does' & 'tomato' & 'blight' & 'spread' and matched nothing at all -
# 1 of 13 questions retrieved anything, and the dense half silently covered.
#
# 'english' appears here only as a STOPWORD ORACLE, never as the query config:
# a lexeme the english dictionary throws away ('how', 'does', 'is') is dropped
# from the OR. Without that filter recall is the same but ranking is not -
# ts_rank has no IDF, so a chunk repeating "How does this work?" outranks the
# real answer on 'how' | 'does' alone. Korean lexemes are unknown to the english
# dictionary, so they are kept, which is the wanted behaviour.
#
# The coalesce covers an all-stopword query ("how does it?"): the filtered
# aggregate is NULL, so the unfiltered lexemes are used instead. When there are
# no lexemes at all (punctuation, whitespace) both aggregates are NULL,
# to_tsquery(NULL) is NULL and `content_tsv @@ NULL` is NULL - no rows, no error.
#
# quote_literal is what keeps user text out of tsquery syntax: '|', '&', '!',
# ':*', '(' and a bare quote all arrive as ordinary characters inside a quoted
# lexeme. The lexemes themselves come from to_tsvector, never from the raw
# string, and query_text is a bound parameter, so nothing user-supplied is ever
# concatenated into SQL.
#
# ponytail: 'simple' is a whitespace tokenizer for Korean, so josa still defeats
# it - a question asking about '역병이' does not match a document that wrote
# '역병은', they are two unrelated tokens. Measured: 7 of 7 natural Korean
# questions hit, because a question tends to reuse the document's own inflected
# word - but the bare-noun probe '역병 토양' hit nothing against a document
# reading '역병은 ... 토양과'. The dense half covers that case today; the fix is a
# Korean analyzer (pg_bigm or mecab-ko) on a column of its own, which is a
# migration and a slice of its own.
_TS_QUERY = text("""to_tsquery('simple', coalesce(
    (SELECT string_agg(quote_literal(lexeme), ' | ')
       FROM unnest(to_tsvector('simple', :query_text))
      WHERE to_tsvector('english', lexeme) <> ''::tsvector),
    (SELECT string_agg(quote_literal(lexeme), ' | ')
       FROM unnest(to_tsvector('simple', :query_text)))))""")


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
    ts_query = _TS_QUERY.bindparams(query_text=query_text)
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
