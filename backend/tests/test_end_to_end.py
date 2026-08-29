"""The slice's acceptance test: ingest a document, then prove it is retrievable,
reaches the prompt, and produces a citation with real provenance.

Every other test in this suite verifies one layer against fakes and fixtures. This
one drives the whole path over HTTP - upload, the real worker pipeline, retrieval,
the answer, the citation and the click-through - against real indexed rows. The
only thing faked is the OpenAI SDK boundary, and it is faked deterministically so
retrieval order is a property of the code and not of luck.
"""

from types import SimpleNamespace

import pytest_asyncio
from test_chat import parse_sse
from test_parsers import _write_pdf

from app.llm.base import ChatResult
from app.models.chunk import EMBEDDING_DIM
from app.rag.chunking import get_chunking_strategy
from app.rag.pipeline import process_document
from app.retrieval.vector_store import PgVectorStore

# A PDF, not the .md the rest of the suite uploads: markdown has no pages, and
# `page` is half of what a citation has to carry. The worked example the slice is
# measured against is "[연구보고서 A, p.32]", so the filename is non-ASCII too -
# it travels through multipart, the database, retrieval metadata and the SSE frame.
FILENAME = "연구보고서 A.pdf"
PAGES = [
    [
        "QUARTERLY FINANCIAL REPORT",
        "Revenue rose against the previous year and the outlook is stable.",
    ],
    [
        "TOMATO BLIGHT CONTROL",
        "Tomato blight spreads through infected soil and splashing water.",
        "Growers should rotate crops and remove crop debris.",
    ],
]
# Every token of the query has to appear in the chunk verbatim for the sparse half
# to fire at all: content_tsv and plainto_tsquery both use the 'simple' config, which
# neither stems nor drops stop words, and plainto_tsquery ANDs what is left. "How
# does tomato blight spread?" retrieves nothing from the sparse retriever - 'how',
# 'does' and the unstemmed 'spread' are absent - and the dense half silently covers
# for it. The keyword_rank assertion below is what exposed that.
QUESTION = "tomato blight spreads"
ANSWER = "역병은 감염된 토양과 튀는 물로 퍼집니다 [1]."


class DeterministicProvider:
    """Topic-keyed vectors, so retrieval is exact and the ordering below is not
    luck. Never reaches the network."""

    def __init__(self):
        self.prompts: list = []

    def _vector(self, text: str) -> list[float]:
        lowered = text.casefold()
        blight = 1.0 if ("blight" in lowered or "tomato" in lowered) else 0.0
        finance = 1.0 if ("revenue" in lowered or "financial" in lowered) else 0.0
        # The constant tail keeps every vector non-zero. pgvector's cosine distance
        # to an all-zero vector is NaN, which sorts wherever it likes - so a chunk
        # matching neither topic would make the ranking unreproducible instead of
        # simply last.
        return [blight, finance, 0.5] + [0.0] * (EMBEDDING_DIM - 3)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    async def chat(self, messages, **kwargs) -> ChatResult:
        self.prompts.append(messages)
        return ChatResult(content=ANSWER, usage={"total_tokens": 30}, model="gpt-4o")


@pytest_asyncio.fixture
async def provider(app):
    instance = DeterministicProvider()
    app.state.llm_provider = instance
    return instance


@pytest_asyncio.fixture
async def corpus(client, app, db, provider, tmp_path):
    """Register (the first account bootstraps admin and the default collection),
    create a second collection, upload a real PDF and index it with the worker's
    own entry point."""
    await client.post("/api/auth/register", json={"email": "admin@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "pw123456"})
    collection_id = (await client.post("/api/collections", json={"name": "농업"})).json()["id"]

    path = tmp_path / "report.pdf"
    _write_pdf(path, PAGES)
    upload = await client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": (FILENAME, path.read_bytes(), "application/pdf")},
    )
    assert upload.status_code == 202
    assert upload.json()["status"] == "uploaded"
    document_id = upload.json()["id"]

    # process_document is what the arq worker calls, run inline on the file the
    # upload actually stored. The strategy is pinned rather than read from the
    # operator's .env: under "fixed" this small document is one chunk, and a corpus
    # with only one topic in it cannot show that retrieval picked the right one.
    settings = app.state.settings.model_copy(update={"chunking_strategy": "semantic"})
    await process_document(db, PgVectorStore(db), provider, get_chunking_strategy(settings), document_id)

    collections = (await client.get("/api/collections")).json()
    return SimpleNamespace(
        document_id=document_id,
        collection_id=collection_id,
        other_collection_id=next(c["id"] for c in collections if c["id"] != collection_id),
    )


async def test_an_uploaded_document_becomes_a_cited_answer(client, provider, corpus):
    listed = (await client.get(f"/api/documents/{corpus.document_id}")).json()
    assert listed["status"] == "indexed"
    assert listed["filename"] == FILENAME
    # One chunk per section. At 1 the two topics have merged, and every relevance
    # assertion below would be passing on a corpus that holds no wrong answer.
    assert listed["chunk_count"] == 2

    # Retrieval alone finds the right chunk, with its provenance attached.
    search = (await client.post("/api/search", json={"query": QUESTION})).json()
    assert search["results"], "an indexed document must be retrievable"
    top = search["results"][0]
    assert "Tomato blight spreads" in top["content"]
    assert top["metadata"]["filename"] == FILENAME
    assert top["metadata"]["page"] == 2
    # Both halves of hybrid retrieval have to have found it. Without this the dense
    # and sparse paths cover for each other and either one could be dead.
    assert (top["metadata"]["vector_rank"], top["metadata"]["keyword_rank"]) == (1, 1)

    # And the answer carries a citation that resolves to that chunk.
    response = await client.post("/api/chat", json={"message": QUESTION})
    done = parse_sse(response.text)[-1]
    assert done["type"] == "done"
    assert done["content"] == ANSWER
    assert done["citations"], "an answer citing [1] must carry a citation"

    citation = done["citations"][0]
    assert citation["filename"] == FILENAME
    assert citation["page"] == 2
    assert citation["section"] == "TOMATO BLIGHT CONTROL"
    assert "Tomato blight spreads" in citation["snippet"]

    # The evidence reached the model, fenced, with the question still last.
    messages = provider.prompts[-1]
    fenced = [message for message in messages if "EVIDENCE" in message.content]
    assert fenced, "the retrieved evidence never reached the prompt"
    assert "Tomato blight spreads" in fenced[0].content
    assert "p.2" in fenced[0].content  # the label the citation is drawn from
    assert messages[-1].content == QUESTION

    # Click-through: the cited chunk id is fetchable and holds the cited text.
    chunk = await client.get(f"/api/chunks/{citation['chunk_id']}")
    assert chunk.status_code == 200
    assert "Tomato blight spreads" in chunk.json()["content"]
    assert chunk.json()["page"] == 2


async def test_collection_scoping_filters_against_a_real_corpus(client, provider, corpus):
    """Both empty cases have been default-open bugs here - once in the vector path,
    once in the sparse one - and an empty corpus cannot tell a scope that filters
    from one that is ignored."""
    body = {"query": QUESTION}
    scoped = (await client.post("/api/search", json=body | {"collection_ids": [corpus.collection_id]})).json()
    assert len(scoped["results"]) == 2

    elsewhere = await client.post("/api/search", json=body | {"collection_ids": [corpus.other_collection_id]})
    assert elsewhere.json()["results"] == []

    nowhere = await client.post("/api/search", json=body | {"collection_ids": []})
    assert nowhere.json()["results"] == []

    # Same scope on the chat path, where it is the citations that have to vanish -
    # and the evidence must never have reached the prompt in the first place.
    response = await client.post(
        "/api/chat", json={"message": QUESTION, "collection_ids": [corpus.other_collection_id]}
    )
    assert parse_sse(response.text)[-1]["citations"] == []
    assert not any("EVIDENCE" in message.content for message in provider.prompts[-1])


async def test_the_answer_stream_carries_no_prompt_and_no_uncited_evidence(client, corpus):
    response = await client.post("/api/chat", json={"message": QUESTION})

    assert {event["type"] for event in parse_sse(response.text)} <= {"status", "citations", "done"}
    assert "<<EVIDENCE" not in response.text
    assert "You are MOPAN's assistant" not in response.text
    # The financial chunk was retrieved and shown to the model as [2], but the
    # answer never cited it, so it is not the client's to see.
    assert "Revenue rose" not in response.text
