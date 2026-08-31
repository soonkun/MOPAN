import logging
import re
import time
import uuid

from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log_event
from app.llm.base import LLMProvider
from app.models.document import Document
from app.rag.chunking.base import ChunkingStrategy
from app.rag.chunking.hierarchy import CHARACTERS, Scheme, detect
from app.rag.parsers import get_parser
from app.rag.references import build_edges
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
    section_marker: re.Pattern[str] | None = None,
    *,
    scheme: Scheme | None = None,
    fallback: ChunkingStrategy | None = None,
) -> None:
    """`section_marker` is the collection's configured section-header pattern.
    It reaches the PARSER, not the chunker: a line matching it has to survive as a
    block of its own or the chunker never sees a boundary to cut on. Measured on
    유사상품 심사기준, 868 of its 931 markers were swallowed into the previous
    section's paragraph without it. The chunker gets the same pattern by a
    different road - `chunking_strategy` was built from the same configuration.

    `scheme` and `fallback` are the two halves of PER-DOCUMENT DETECTION and both
    default to None, so every caller that says nothing gets byte for byte the
    pipeline it had before this existed. When a scheme IS given, the collection has
    said "my documents are numbered like this", and this function asks each
    document whether it actually is: `detect` counts the levels and the citations
    it finds, the verdict and those counts are written to `documents.structure` for
    the UI to render, and a document that turns out to be ordinary prose is cut by
    `fallback` instead. Per document, because the `일반` collection here already
    holds both kinds; visible, because this project has already shipped one
    invisible automatic decision and migration 0013 exists to undo it."""
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
        parsed = await to_thread.run_sync(parser.parse, document.storage_path, section_marker)

        await _set_status(db, document, "chunking")
        # WHAT THIS DOCUMENT IS, decided by this document's own content and then
        # written down where a person can read it and disagree.
        #
        # An OVERRIDE that is already on the row wins and SURVIVES re-processing.
        # That is the whole point of storing `detected` separately: somebody who
        # corrected a verdict must not have to correct it again after every
        # re-ingest, and the screen has to be able to show both numbers at once.
        strategy = chunking_strategy
        if scheme is not None:
            detection = detect(parsed.blocks, scheme)
            structure = dict(document.structure or {})
            override = structure.get("override")
            character = override if override in CHARACTERS else detection.character
            structure = {**structure, **detection.as_json(), "character": character}
            structure["override"] = override if override in CHARACTERS else None
            structure["scheme"] = scheme.preset or "custom"
            if character != "reference_dependent" and fallback is not None:
                strategy = fallback
                # AND PARSE IT AGAIN, WITHOUT THE LEVEL PATTERN. Without this the
                # claim "a self-contained document takes exactly the path it takes
                # today" is false: `section_marker` reaches the PARSER, so every
                # 제N조 / ① / "1. " line in this document was forced into a block of
                # its own before anything looked at whether the document has a
                # hierarchy at all - and different blocks are different chunks.
                # The verdict is only knowable after a parse, so the honest answer
                # is to parse twice. It costs CPU on the worker and nothing at the
                # API, it happens once per ingest, and it only happens for a
                # document whose collection said "hierarchy" and whose content
                # said otherwise.
                if section_marker is not None:
                    parsed = await to_thread.run_sync(
                        parser.parse, document.storage_path, None
                    )
            document.structure = structure
        candidates = await strategy.chunk(parsed.blocks, llm_provider.embed)

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
        # Where content_tsv comes from, since this is the file people look in:
        # NOT here. `chunks.content_tsv` stopped being a generated column in
        # migration 0012 and is now application-written, but the write lives on
        # the column itself - `Chunk.content_tsv`'s default in app/models/chunk.py
        # tokenizes that row's own `content` with `settings.sparse_tokenizer`.
        # It has to be there rather than at this call site: this pipeline builds
        # VectorItems, and it is PgVectorStore.upsert that turns them into chunk
        # rows, so a write here would only cover the Postgres backend and only
        # this one caller.
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

        # THE REFERENCE EDGES, after the chunks exist because an edge points at
        # chunk rows. Only for a document that was actually cut on its hierarchy:
        # a self-contained document has no paths to resolve against, so building
        # edges for it would record every citation as unresolved and put a
        # misleading "0 of 130 resolved" on its screen.
        if scheme is not None and strategy is not fallback:
            counts = await build_edges(db, document.id, scheme, candidates)
            document.structure = {
                **document.structure,
                "citations": {
                    "found": counts["found"],
                    "resolved": counts["resolved"],
                    "unresolved": counts["unresolved"],
                },
                "unresolved_examples": counts["examples"],
                "parent_edges": counts["parents"],
            }

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
