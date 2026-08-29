import logging
import time
import uuid

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log_event
from app.llm.base import LLMProvider
from app.models.document import Document
from app.rag.chunking.base import ChunkingStrategy
from app.rag.parsers import get_parser
from app.retrieval.vector_store import VectorItem, VectorStore

logger = logging.getLogger("mopan.pipeline")

USER_FACING_FAILURE = "문서를 처리하지 못했습니다. 파일 형식과 내용을 확인해 주세요."


async def _set_status(db: AsyncSession, document: Document, status: str) -> None:
    """Commit each transition on its own: the Documents UI polls this column, so
    a status that is only visible inside the pipeline's transaction shows the
    user nothing for the whole job."""
    document.status = status
    await db.commit()


async def process_document(
    db: AsyncSession,
    vector_store: VectorStore,
    llm_provider: LLMProvider,
    chunking_strategy: ChunkingStrategy,
    document_id: str,
) -> None:
    document = await db.get(Document, uuid.UUID(document_id))
    if document is None:
        logger.warning("document %s no longer exists; nothing to process", document_id)
        return

    started = time.perf_counter()
    try:
        await _set_status(db, document, "parsing")
        parser = get_parser(document.file_type)
        # CPU-bound and blocking (it reads the file too): a 300-page pypdf parse
        # on the worker's single event loop stalls every other queued job and
        # arq's own heartbeat. The chunking strategies thread their tiktoken
        # passes for the same reason.
        parsed = await to_thread.run_sync(parser.parse, document.storage_path)

        await _set_status(db, document, "chunking")
        candidates = await chunking_strategy.chunk(parsed.blocks, llm_provider.embed)

        await _set_status(db, document, "embedding")
        # Reuse the embeddings the semantic strategy already computed for
        # candidates it did not merge; only merged text needs a new vector.
        pending = [c for c in candidates if c.embedding is None]
        if pending:
            vectors = await llm_provider.embed([c.content for c in pending])
            for candidate, vector in zip(pending, vectors, strict=True):
                candidate.embedding = vector

        # Idempotency: arq retries and manual re-processing must not multiply the
        # corpus. Without this delete, a job that fails after the chunks were
        # flushed appends a fresh set on every retry.
        #
        # Deliberately adjacent to the upsert rather than at the top of the job.
        # The property this pipeline REQUIRES is only that the delete precedes
        # the insert - true either way. Keeping them adjacent additionally means
        # that on a transactional backend a failure between here and the final
        # commit rolls both back, so a transient re-index failure does not empty
        # the index of a document that was previously fine. Do not depend on
        # that: a remote store (Qdrant) has no transaction and would degrade to
        # "old chunks gone, new chunks missing", which is why it stays a bonus
        # and not a contract.
        await vector_store.delete_by_document(document.id)
        await vector_store.upsert(
            [
                VectorItem(
                    document_id=document.id,
                    chunk_index=index,
                    content=candidate.content,
                    token_count=candidate.token_count,
                    char_count=candidate.char_count,
                    page=candidate.page,
                    section=candidate.section,
                    metadata=candidate.metadata,
                    embedding=candidate.embedding,
                )
                for index, candidate in enumerate(candidates)
            ]
        )

        document.status = "indexed"
        document.error_message = None
        await db.commit()

        log_event(
            logger,
            "document_indexed",
            document_id=document_id,
            chunk_count=len(candidates),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except Exception:
        # The most likely failure here is a DATABASE error at a commit or a
        # flush, which leaves the session in a pending-rollback state - a bare
        # `commit()` in this handler would raise PendingRollbackError and the
        # document would be stuck mid-pipeline forever with no error_message.
        await db.rollback()
        document = await db.get(Document, uuid.UUID(document_id))
        if document is not None:
            document.status = "failed"
            # User-facing text only; the traceback goes to the log, because this
            # column is rendered in the Documents UI.
            document.error_message = USER_FACING_FAILURE
            await db.commit()
        logger.exception("document processing failed", extra={"extra_fields": {"document_id": document_id}})
        raise
