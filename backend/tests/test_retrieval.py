import logging
import uuid

import pytest
import pytest_asyncio

from app.models.chunk import EMBEDDING_DIM, Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.retrieval.evidence import RetrievedChunk, chunk_to_evidence
from app.retrieval.keyword_search import keyword_search
from app.retrieval.reranker import NoneReranker, Reranker
from app.retrieval.service import hybrid_search
from app.retrieval.vector_store import PgVectorStore, ScoredId, VectorStore


def vec(*leading: float) -> list[float]:
    return list(leading) + [0.0] * (EMBEDDING_DIM - len(leading))


class FakeLLMProvider:
    def __init__(self, query_vector):
        self.query_vector = query_vector

    async def embed(self, texts):
        return [self.query_vector for _ in texts]

    async def chat(self, messages, **kwargs):
        raise NotImplementedError


class ReverseReranker(Reranker):
    async def rerank(self, query, candidates):
        reversed_candidates = list(reversed(candidates))
        for position, candidate in enumerate(reversed_candidates):
            candidate.rerank_score = 1.0 / (position + 1)
        return reversed_candidates


class DuplicatingVectorStore(VectorStore):
    """A retriever whose output is malformed: the same id twice. RRF de-duplicates
    per ranking and scores the FIRST occurrence, so the rank the service records
    has to agree with it - see test_a_duplicate_from_a_retriever_ranks_and_scores_first."""

    def __init__(self, chunk_ids: list[str]):
        self.chunk_ids = chunk_ids

    async def search(self, embedding, limit, collection_ids=None):
        return [ScoredId(chunk_id=cid, score=1.0) for cid in self.chunk_ids][:limit]

    async def upsert(self, items):
        raise NotImplementedError

    async def delete_by_document(self, document_id):
        raise NotImplementedError


@pytest_asyncio.fixture
async def corpus(db):
    user = User(email="retrieval@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.flush()
    collection_a = Collection(name="A", created_by=user.id)
    collection_b = Collection(name="B", created_by=user.id)
    db.add_all([collection_a, collection_b])
    await db.flush()

    def _doc(collection, name):
        return Document(
            collection_id=collection.id,
            filename=name,
            file_type="txt",
            size_bytes=1,
            storage_path="x",
            status="indexed",
            uploaded_by=user.id,
        )

    doc_a = _doc(collection_a, "연구보고서 A.pdf")
    doc_b = _doc(collection_b, "other.pdf")
    db.add_all([doc_a, doc_b])
    await db.flush()

    chunks = [
        Chunk(
            document_id=doc_a.id,
            chunk_index=0,
            content="tomato blight treatment guide",
            token_count=5,
            char_count=29,
            page=32,
            section="방제",
            chunk_metadata={},
            embedding=vec(1.0, 0.0, 0.0),
        ),
        Chunk(
            document_id=doc_a.id,
            chunk_index=1,
            content="unrelated financial report notes",
            token_count=5,
            char_count=32,
            page=2,
            section=None,
            chunk_metadata={},
            embedding=vec(0.0, 1.0, 0.0),
        ),
        Chunk(
            document_id=doc_b.id,
            chunk_index=0,
            content="tomato blight in another collection",
            token_count=6,
            char_count=35,
            page=1,
            section=None,
            chunk_metadata={},
            embedding=vec(1.0, 0.0, 0.0),
        ),
        # No embedding: reachable by the keyword retriever only. Its content shares
        # no lexeme with "tomato blight", so it stays invisible to every other test.
        Chunk(
            document_id=doc_a.id,
            chunk_index=2,
            content="durian ripeness sampling protocol",
            token_count=5,
            char_count=33,
            page=7,
            section=None,
            chunk_metadata={},
            embedding=None,
        ),
    ]
    db.add_all(chunks)
    await db.commit()
    return {"a": collection_a, "b": collection_b, "chunks": chunks}


async def _search(db, corpus, **kwargs):
    return await hybrid_search(
        db,
        kwargs.pop("vector_store", None) or PgVectorStore(db),
        FakeLLMProvider(vec(1.0, 0.0, 0.0)),
        kwargs.pop("reranker", NoneReranker()),
        kwargs.pop("query", "tomato blight"),
        top_n=kwargs.pop("top_n", 5),
        rrf_k=60,
        candidate_limit=20,
        **kwargs,
    )


async def test_hybrid_search_ranks_the_relevant_chunk_first(db, corpus):
    evidence = await _search(db, corpus)
    assert evidence[0].content.startswith("tomato blight")
    assert evidence[0].source_type == "rag"
    assert evidence[0].ref.startswith("chunk:")


async def test_evidence_carries_the_provenance_needed_for_a_citation(db, corpus):
    evidence = await _search(db, corpus, collection_ids=[corpus["a"].id])
    metadata = evidence[0].metadata
    assert metadata["filename"] == "연구보고서 A.pdf"
    assert metadata["page"] == 32
    assert metadata["section"] == "방제"


async def test_per_stage_scores_are_kept_separate(db, corpus):
    evidence = await _search(db, corpus)
    metadata = evidence[0].metadata
    assert metadata["vector_rank"] == 1
    assert metadata["keyword_rank"] is not None
    assert metadata["rrf_score"] > 0
    assert metadata["rerank_score"] is None  # NoneReranker does not score


async def test_collection_filter_excludes_other_collections(db, corpus):
    evidence = await _search(db, corpus, collection_ids=[corpus["a"].id])
    assert all(e.metadata["filename"] == "연구보고서 A.pdf" for e in evidence)


async def test_an_empty_collection_id_list_scopes_to_nothing(db, corpus):
    """`[]` means "no collection", not "every collection" - in BOTH retrievers.
    Reading it as unscoped would widen a Slice 3 Super Agent query from zero
    collections to the whole corpus."""
    assert await _search(db, corpus, collection_ids=[]) == []
    assert await keyword_search(db, "tomato blight", 20, collection_ids=[]) == []


async def test_reranker_can_promote_a_candidate_past_the_top_n_cut(db, corpus):
    """Proves the reranker runs BEFORE truncation: with top_n=1 a reversing
    reranker must be able to change which single chunk survives."""
    default = await _search(db, corpus, top_n=1)
    reversed_result = await _search(db, corpus, top_n=1, reranker=ReverseReranker())

    assert default[0].ref != reversed_result[0].ref
    assert reversed_result[0].metadata["rerank_score"] is not None
    # The promoted chunk is the one RRF ranked LAST, so it could only survive
    # top_n=1 if the whole candidate set reached the reranker.
    ranked = await _search(db, corpus, top_n=20)
    assert reversed_result[0].ref == ranked[-1].ref


async def test_a_chunk_only_one_retriever_found_is_still_fused(db, corpus):
    """The keyword-only chunk has no embedding, so the vector retriever cannot
    see it; the fusion is a union, not an intersection."""
    evidence = await _search(db, corpus, query="durian ripeness")
    keyword_only = [e for e in evidence if e.content.startswith("durian")]
    assert len(keyword_only) == 1
    assert keyword_only[0].metadata["vector_rank"] is None
    assert keyword_only[0].metadata["keyword_rank"] == 1


async def test_a_query_no_keyword_can_match_still_returns_vector_evidence(db, corpus):
    """One retriever returning nothing must not break fusion."""
    assert await keyword_search(db, "zzzznomatch", 20) == []
    evidence = await _search(db, corpus, query="zzzznomatch")
    assert evidence
    assert all(e.metadata["keyword_rank"] is None for e in evidence)
    assert evidence[0].metadata["vector_rank"] == 1


async def test_a_duplicate_from_a_retriever_ranks_and_scores_first(db, corpus):
    """RRF de-duplicates per ranking and scores the first occurrence. The rank the
    service records must agree: recording the last occurrence would put a rank in
    the trace that no longer explains the score beside it."""
    chunk_ids = [str(c.id) for c in corpus["chunks"]]
    duplicating = DuplicatingVectorStore([chunk_ids[0], chunk_ids[1], chunk_ids[0]])
    evidence = await _search(db, corpus, query="zzzznomatch", vector_store=duplicating)

    by_id = {e.metadata["chunk_id"]: e.metadata for e in evidence}
    assert by_id[chunk_ids[0]]["vector_rank"] == 1
    assert by_id[chunk_ids[0]]["rrf_score"] == 1 / 61
    assert by_id[chunk_ids[1]]["vector_rank"] == 2
    assert by_id[chunk_ids[1]]["rrf_score"] == 1 / 62


async def test_empty_corpus_returns_no_evidence(db):
    evidence = await hybrid_search(
        db,
        PgVectorStore(db),
        FakeLLMProvider(vec(1.0)),
        NoneReranker(),
        "anything",
        top_n=5,
        rrf_k=60,
        candidate_limit=20,
    )
    assert evidence == []


@pytest.mark.parametrize("query", ["", "   ", "!!! ??? ---", "\n\t"])
async def test_keyword_search_survives_a_query_with_no_lexemes(db, corpus, query):
    """plainto_tsquery reduces these to an empty tsquery, which matches nothing.
    A crash here would 500 on a user pasting punctuation into the search box."""
    assert await keyword_search(db, query, 20) == []


@pytest.mark.parametrize("query", ["", "   ", "!!! ??? ---", "\n\t"])
async def test_hybrid_search_survives_a_query_with_no_lexemes(db, corpus, query):
    """End to end, not just the sparse half. The sparse side contributes nothing
    for any of these, so fusion runs on a single ranking and the answer path still
    gets evidence - a user pasting punctuation into the box must not 500."""
    evidence = await _search(db, corpus, query=query)
    assert evidence
    assert all(e.metadata["keyword_rank"] is None for e in evidence)


async def test_hybrid_search_logs_its_stage_counts(db, corpus, caplog):
    """Slice 5's dashboard reads these fields off the record, so they are an
    interface, not a debug print. Unpinned, the whole log_event call could be
    deleted and the suite would stay green."""
    with caplog.at_level(logging.INFO, logger="mopan.retrieval"):
        await _search(db, corpus, top_n=1)
    record = next(r for r in caplog.records if r.getMessage() == "hybrid_search")
    fields = record.extra_fields
    assert fields["vector_hits"] > 0
    assert fields["keyword_hits"] > 0
    assert fields["candidates"] >= fields["selected"] == 1
    assert fields["duration_ms"] >= 0


async def test_keyword_search_scopes_to_the_named_collections(db, corpus):
    scoped = await keyword_search(db, "tomato blight", 20, collection_ids=[corpus["b"].id])
    assert scoped == [str(corpus["chunks"][2].id)]


async def test_keyword_search_respects_its_limit(db, corpus):
    assert len(await keyword_search(db, "tomato blight", 1)) == 1


def test_retrieved_chunk_defaults_are_explicit():
    chunk = RetrievedChunk(chunk_id="c", document_id="d", filename="f.pdf", content="x")
    assert chunk.rerank_score is None
    assert chunk.rrf_score == 0.0


def test_evidence_score_prefers_the_reranker_and_falls_back_to_rrf():
    chunk = RetrievedChunk(chunk_id="c", document_id="d", filename="f.pdf", content="x")
    chunk.rrf_score = 0.5
    assert chunk_to_evidence(chunk).score == 0.5
    # 0.0 is a legitimate reranker verdict and must not fall back to the RRF score.
    chunk.rerank_score = 0.0
    assert chunk_to_evidence(chunk).score == 0.0


def test_evidence_keeps_every_stage_score_in_metadata():
    """Slice 5's Conversation Trace enumerates retrieval rank, RRF score and
    reranker score separately; they must not collapse into one number."""
    chunk = RetrievedChunk(
        chunk_id=str(uuid.uuid4()),
        document_id=str(uuid.uuid4()),
        filename="f.pdf",
        content="x",
        vector_rank=3,
        keyword_rank=1,
        rrf_score=0.03,
        rerank_score=0.9,
    )
    metadata = chunk_to_evidence(chunk).metadata
    assert metadata["vector_rank"] == 3
    assert metadata["keyword_rank"] == 1
    assert metadata["rrf_score"] == 0.03
    assert metadata["rerank_score"] == 0.9
