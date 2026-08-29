import asyncio
import inspect
import uuid

import pytest
import pytest_asyncio
from arq.worker import Worker
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app import worker as worker_module
from app.core.config import get_settings
from app.models.chunk import EMBEDDING_DIM, Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.rag import pipeline
from app.rag.chunking.fixed import FixedChunking
from app.rag.pipeline import USER_FACING_FAILURE, process_document
from app.retrieval.vector_store import PgVectorStore


class FakeLLMProvider:
    """Deterministic full-width vectors. A 3-dim vector into Vector(1536) fails
    with `expected 1536 dimensions, not 3` - which is exactly what revision 1's
    pipeline test did, proving it had never been run.

    Also the reason no test in this file reaches the network."""

    def __init__(self):
        self.embed_calls = 0

    async def embed(self, texts):
        self.embed_calls += 1
        return [[0.1, 0.2, 0.3] + [0.0] * (EMBEDDING_DIM - 3) for _ in texts]

    async def chat(self, messages, **kwargs):
        raise NotImplementedError


@pytest_asyncio.fixture
async def document(db, tmp_path):
    user = User(email="pipeline@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.flush()
    collection = Collection(name="Test", created_by=user.id)
    db.add(collection)
    await db.flush()

    source = tmp_path / "note.txt"
    source.write_text("Hello world. " * 60, encoding="utf-8")

    doc = Document(
        collection_id=collection.id,
        filename="note.txt",
        file_type="txt",
        size_bytes=source.stat().st_size,
        storage_path=str(source),
        status="uploaded",
        uploaded_by=user.id,
    )
    db.add(doc)
    await db.commit()
    return doc


async def test_process_document_indexes_chunks(db, document):
    await process_document(
        db,
        PgVectorStore(db),
        FakeLLMProvider(),
        FixedChunking(chunk_size=100, overlap=10),
        str(document.id),
    )

    await db.refresh(document)
    assert document.status == "indexed"
    assert document.error_message is None

    chunks = (await db.scalars(select(Chunk).where(Chunk.document_id == document.id))).all()
    assert len(chunks) > 1
    assert all(c.embedding is not None for c in chunks)
    assert all(len(c.embedding) == EMBEDDING_DIM for c in chunks)
    # chunk_index is what the vector store upserts on; a gap or a repeat here
    # means re-indexing would leave orphans behind.
    assert sorted(c.chunk_index for c in chunks) == list(range(len(chunks)))


async def test_reprocessing_is_idempotent(db, document):
    """arq retries and manual re-processing must not multiply the corpus."""
    strategy = FixedChunking(chunk_size=100, overlap=10)
    await process_document(db, PgVectorStore(db), FakeLLMProvider(), strategy, str(document.id))
    first = (await db.scalars(select(Chunk).where(Chunk.document_id == document.id))).all()

    await process_document(db, PgVectorStore(db), FakeLLMProvider(), strategy, str(document.id))
    second = (await db.scalars(select(Chunk).where(Chunk.document_id == document.id))).all()

    assert len(first) == len(second)
    assert sorted(c.chunk_index for c in second) == list(range(len(second)))

    # Re-indexing the SAME text is not enough to prove it: upsert overwrites by
    # (document_id, chunk_index), so an identical run lands on the same indexes
    # whether or not the pipeline deletes first. Only a run that produces FEWER
    # chunks exposes the stale tail a missing delete leaves behind.
    await process_document(
        db,
        PgVectorStore(db),
        FakeLLMProvider(),
        FixedChunking(chunk_size=400, overlap=10),
        str(document.id),
    )
    third = (await db.scalars(select(Chunk).where(Chunk.document_id == document.id))).all()
    assert len(third) < len(second)
    assert sorted(c.chunk_index for c in third) == list(range(len(third)))


async def test_status_transitions_are_persisted(db, document, monkeypatch):
    """Every stage's status has to be COMMITTED before that stage starts: the
    Documents UI polls this column, and a status visible only inside the
    pipeline's own transaction tells the user nothing."""
    seen: list[str] = []
    real_set_status = pipeline._set_status

    async def spy(session, doc, status):
        await real_set_status(session, doc, status)
        # A fresh SELECT after the commit, not the identity map: this asserts
        # durability, not just that an attribute was assigned.
        seen.append(await session.scalar(select(Document.status).where(Document.id == doc.id)))

    monkeypatch.setattr(pipeline, "_set_status", spy)

    await process_document(
        db,
        PgVectorStore(db),
        FakeLLMProvider(),
        FixedChunking(chunk_size=100, overlap=10),
        str(document.id),
    )

    assert seen == ["parsing", "chunking", "embedding"]
    assert await db.scalar(select(Document.status).where(Document.id == document.id)) == "indexed"


async def test_a_missing_document_is_not_an_error(db):
    """A job for a document deleted between enqueue and dequeue must not crash
    the worker into a retry loop."""
    await process_document(db, PgVectorStore(db), FakeLLMProvider(), FixedChunking(), str(uuid.uuid4()))


async def test_parser_failure_marks_the_document_failed(db, document):
    document.storage_path = "/nonexistent/file.txt"
    await db.commit()

    with pytest.raises(FileNotFoundError):
        await process_document(db, PgVectorStore(db), FakeLLMProvider(), FixedChunking(), str(document.id))

    await db.refresh(document)
    assert document.status == "failed"
    assert document.error_message


async def test_database_failure_still_marks_the_document_failed(db, document):
    """Revision 1 only tested a pure-Python parser error, where the session is
    clean. The realistic failure is a DB error, which puts the session into
    pending-rollback and made the old handler raise PendingRollbackError -
    leaving the document stuck mid-pipeline with no error_message."""

    class OverlongSectionStrategy(FixedChunking):
        async def chunk(self, blocks, embed_fn):
            candidates = await super().chunk(blocks, embed_fn)
            for candidate in candidates:
                candidate.section = "x" * 600  # section is String(500)
            return candidates

    # DBAPIError, not Exception: the point of this test is that a DATABASE error
    # reaches the handler, and a plain Exception would also pass if the strategy
    # merely raised a TypeError.
    with pytest.raises(DBAPIError):
        await process_document(
            db,
            PgVectorStore(db),
            FakeLLMProvider(),
            OverlongSectionStrategy(chunk_size=100, overlap=10),
            str(document.id),
        )

    await db.refresh(document)
    assert document.status == "failed"
    assert document.error_message


async def test_a_failure_at_the_final_commit_still_marks_the_document_failed(db, document):
    """The one the old test missed: the DB error lands on the pipeline's LAST
    commit, so the failure handler runs against a session whose flush already
    blew up. Every chunk is written by then; only the status update is left."""

    class DirtyingStore(PgVectorStore):
        """Lets the upsert succeed, then leaves an invalid pending change behind
        so the next flush - the pipeline's final commit - is what fails."""

        def __init__(self, db, document):
            super().__init__(db)
            self.document = document

        async def upsert(self, items):
            await super().upsert(items)
            self.document.filename = "x" * 600  # filename is String(500)

    with pytest.raises(DBAPIError):
        await process_document(
            db,
            DirtyingStore(db, document),
            FakeLLMProvider(),
            FixedChunking(chunk_size=100, overlap=10),
            str(document.id),
        )

    await db.refresh(document)
    assert document.status == "failed"
    assert document.error_message == USER_FACING_FAILURE
    assert document.filename == "note.txt"


async def test_error_message_never_contains_a_traceback(db, document):
    document.storage_path = "/nonexistent/file.txt"
    await db.commit()
    with pytest.raises(FileNotFoundError):
        await process_document(db, PgVectorStore(db), FakeLLMProvider(), FixedChunking(), str(document.id))
    await db.refresh(document)
    # This column is rendered in the Documents UI; internals must not leak.
    assert "Traceback" not in document.error_message
    assert "/nonexistent" not in document.error_message
    assert "sk-" not in document.error_message


def test_worker_settings_declares_only_real_arq_parameters():
    """arq builds the Worker with get_kwargs(), which SILENTLY DROPS every
    attribute that is not a Worker parameter. arq 0.26 has no `on_job_failure`
    hook, so a WorkerSettings that declares one looks configured, never runs,
    and leaves a timed-out job's document at `parsing` forever."""
    allowed = set(inspect.signature(Worker).parameters)
    declared = {name for name in vars(worker_module.WorkerSettings) if not name.startswith("_")}
    assert declared <= allowed, sorted(declared - allowed)


def test_worker_bounds_job_timeout_and_retries():
    settings = worker_module.WorkerSettings
    # arq's defaults are 300s / 5 tries: a long PDF is killed mid-parse, and
    # five tries used to append a fresh set of chunks each time.
    assert settings.job_timeout > 300
    assert settings.max_tries == 2


async def test_a_cancelled_job_marks_the_document_failed(db, document, test_sessionmaker, monkeypatch):
    """job_timeout cancels the job's task, so the pipeline's own `except
    Exception` never runs. Without the worker-level handler the document sits at
    `parsing` until someone notices."""

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(worker_module, "run_pipeline", cancelled)
    ctx = {
        "settings": get_settings(),
        "sessionmaker": test_sessionmaker,
        "llm_provider": FakeLLMProvider(),
    }

    with pytest.raises(asyncio.CancelledError):
        await worker_module.process_document(ctx, str(document.id))

    await db.refresh(document)
    assert document.status == "failed"
    assert document.error_message == USER_FACING_FAILURE


async def test_the_worker_failure_handler_leaves_a_terminal_document_alone(db, document, test_sessionmaker):
    """A late failure must not rewrite the outcome of a document that already
    finished - re-queueing an indexed document and losing the worker mid-cleanup
    would otherwise mark a perfectly good index `failed`."""
    document.status = "indexed"
    await db.commit()

    await worker_module.mark_failed({"sessionmaker": test_sessionmaker}, str(document.id))

    await db.refresh(document)
    assert document.status == "indexed"
    assert document.error_message is None


async def test_worker_shutdown_disposes_its_resources():
    closed: list[str] = []

    class Recorder:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            closed.append(self.name)

        async def dispose(self):
            closed.append(self.name)

    await worker_module.shutdown({"llm_provider": Recorder("provider"), "engine": Recorder("engine")})
    assert sorted(closed) == ["engine", "provider"]
