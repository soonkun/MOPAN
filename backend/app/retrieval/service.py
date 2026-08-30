import logging
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log_event
from app.llm.base import LLMProvider
from app.models.chunk import Chunk
from app.models.document import Document
from app.retrieval.evidence import Evidence, RetrievedChunk, chunk_to_evidence
from app.retrieval.keyword_search import keyword_search
from app.retrieval.neighbors import ExpansionMode, expand
from app.retrieval.reranker import Reranker
from app.retrieval.rrf import reciprocal_rank_fusion
from app.retrieval.vector_store import VectorStore

logger = logging.getLogger("mopan.retrieval")


async def _load_chunks(db: AsyncSession, chunk_ids: list[str]) -> dict[str, tuple[Chunk, str]]:
    """Private on purpose: Chunk is an ORM model and must not leave this module."""
    rows = (
        await db.execute(
            select(Chunk, Document.filename)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.id.in_([uuid.UUID(cid) for cid in chunk_ids]))
        )
    ).all()
    return {str(chunk.id): (chunk, filename) for chunk, filename in rows}


def _ranks(ids: list[str]) -> dict[str, int]:
    """1-based rank per id, first occurrence winning.

    `dict.fromkeys` is not decoration: reciprocal_rank_fusion de-duplicates a
    ranking the same way and scores the FIRST occurrence. A plain enumerate()
    would record the LAST one, so a malformed retriever - the only way a
    duplicate arrives - would put a rank in the trace that does not explain the
    score printed beside it.
    """
    return {chunk_id: rank for rank, chunk_id in enumerate(dict.fromkeys(ids), start=1)}


async def hybrid_search(
    db: AsyncSession,
    vector_store: VectorStore,
    llm_provider: LLMProvider,
    reranker: Reranker,
    query: str,
    *,
    top_n: int,
    rrf_k: int,
    candidate_limit: int,
    sparse_weight: float = 1.0,
    collection_ids: list[uuid.UUID] | None = None,
    neighbor_expansion: ExpansionMode = "off",
    chunk_overlap: int = 0,
    token_budget: int = 0,
) -> list[Evidence]:
    """Query -> (dense + sparse) -> RRF -> rerank -> top-N -> expand -> Evidence.

    RRF and the reranker are separate, separately configurable stages: RRF is
    arithmetic over two rank lists, the reranker is a model. Swapping in a
    cross-encoder means passing a different `Reranker`; nothing here changes.

    The last three arguments are neighbour expansion, and they DEFAULT TO OFF so
    that every caller that says nothing gets exactly the behaviour it got before
    expansion existed. The application wiring opts in, in chat/service.py, the
    same way it opts into `sparse_weight`. Expansion runs AFTER the reranker and
    after the top-N cut - a neighbour is not a candidate competing for a slot, it
    is text added to a slot that was already won - and BEFORE build_prompt's
    budget, which is why `token_budget` is passed rather than assumed.
    """
    started = time.perf_counter()
    # Embedding first, before the first DB statement, so THIS function opens no
    # transaction across the network call. That is only half of it: the property
    # holds end to end only if the caller has nothing open either. A caller that
    # loads the conversation from `db` and then calls in here re-opens the exact
    # hazard - Task 17 is that caller, and it must commit or close first.
    [query_embedding] = await llm_provider.embed([query])

    vector_hits = await vector_store.search(query_embedding, candidate_limit, collection_ids)
    vector_ids = [hit.chunk_id for hit in vector_hits]
    keyword_ids = await keyword_search(db, query, candidate_limit, collection_ids)

    # The dense list is weighted 1.0 and the sparse list below it, because on the
    # Korean corpus they are not peers - see the note over Settings.sparse_weight
    # for the measurement. The default here is 1.0, plain RRF, so this function
    # keeps its textbook behaviour for a caller that says nothing; only the
    # application wiring in chat/service.py opts into the demotion.
    fused = reciprocal_rank_fusion(
        [vector_ids, keyword_ids], k=rrf_k, weights=[1.0, sparse_weight]
    )[:candidate_limit]
    vector_rank = _ranks(vector_ids)
    keyword_rank = _ranks(keyword_ids)

    # The union of two candidate_limit-long lists can be twice candidate_limit,
    # so the slice above is a real cap on what the reranker is asked to score.
    loaded = await _load_chunks(db, [chunk_id for chunk_id, _ in fused]) if fused else {}

    candidates: list[RetrievedChunk] = []
    for chunk_id, score in fused:
        entry = loaded.get(chunk_id)
        # A chunk deleted between the two retrievals and this load. Skip it
        # rather than fabricate an Evidence with no content.
        if entry is None:
            continue
        chunk, filename = entry
        candidates.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                document_id=str(chunk.document_id),
                filename=filename,
                content=chunk.content,
                page=chunk.page,
                section=chunk.section,
                chunk_index=chunk.chunk_index,
                vector_rank=vector_rank.get(chunk_id),
                keyword_rank=keyword_rank.get(chunk_id),
                rrf_score=score,
            )
        )

    # Rerank the whole candidate set, THEN truncate. Truncating first would make
    # the reranker structurally unable to promote anything - it would only ever
    # reorder rows that were already going to be used.
    reranked = await reranker.rerank(query, candidates)
    selected = reranked[:top_n]

    # After the truncation, on the items that survived it. Expanding the whole
    # candidate set instead would pay for 20 neighbours to use 14, and expanding
    # BEFORE the rerank would let a neighbour's text change the score of the
    # chunk it was attached to.
    await expand(
        db,
        selected,
        mode=neighbor_expansion,
        overlap_chars=chunk_overlap,
        token_budget=token_budget,
        query=query,
    )

    log_event(
        logger,
        "hybrid_search",
        vector_hits=len(vector_ids),
        keyword_hits=len(keyword_ids),
        candidates=len(candidates),
        selected=len(selected),
        expanded=sum(1 for chunk in selected if chunk.neighbors),
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return [chunk_to_evidence(chunk) for chunk in selected]
