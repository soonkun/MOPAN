import asyncio
import logging
import uuid

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import make_engine, make_sessionmaker
from app.core.logging import configure_logging
from app.core.settings_store import effective_settings
from app.llm.openai_provider import OpenAIProvider
from app.models.document import TERMINAL_STATUSES, Document
from app.rag.chunking import get_chunking_strategy
from app.rag.pipeline import USER_FACING_FAILURE
from app.rag.pipeline import process_document as run_pipeline
from app.retrieval.vector_store import PgVectorStore

logger = logging.getLogger("mopan.worker")

# Our own deadline, deliberately below arq's job_timeout so it always wins. A
# hung pipeline then raises TimeoutError here instead of arq cancelling the
# task, which leaves CancelledError meaning exactly one thing - SIGTERM - and
# that is what makes job_try a sound discriminator in process_document.
PIPELINE_TIMEOUT = 870


async def startup(ctx: dict) -> None:
    """The worker owns its resources exactly like the API's lifespan does. An
    import-time module global would bind connections to whichever loop imported
    the module - not the loop arq actually runs on."""
    settings = get_settings()
    configure_logging(settings.environment)
    engine = make_engine(settings)
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["sessionmaker"] = make_sessionmaker(engine)
    ctx["llm_provider"] = OpenAIProvider(
        api_key=settings.openai_api_key,
        embedding_model=settings.embedding_model,
        answer_model=settings.answer_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        batch_size=settings.embedding_batch_size,
        batch_chars=settings.embedding_batch_chars,
        embedding_dim=settings.embedding_dim,
    )


async def shutdown(ctx: dict) -> None:
    # arq owns and closes its own Redis pool (ctx["redis"]); the worker adds no
    # session/cache client because nothing in the pipeline needs one.
    # .get(): arq's run() calls on_shutdown from a finally even when on_startup
    # raised, so indexing here would bury the real error under a KeyError.
    provider = ctx.get("llm_provider")
    engine = ctx.get("engine")
    try:
        if provider is not None:
            await provider.aclose()
    finally:
        if engine is not None:
            await engine.dispose()


async def mark_failed(ctx: dict, document_id: str) -> None:
    """Last line of defence, on its own session because the job's session is
    already unwinding. Never overwrites a document that finished."""
    try:
        async with ctx["sessionmaker"]() as db:
            document = await db.get(Document, uuid.UUID(document_id))
            if document is not None and document.status not in TERMINAL_STATUSES:
                document.status = "failed"
                document.error_message = USER_FACING_FAILURE
                await db.commit()
    except Exception:
        logger.exception(
            "could not mark the document failed", extra={"extra_fields": {"document_id": document_id}}
        )


async def process_document(ctx: dict, document_id: str) -> None:
    try:
        async with asyncio.timeout(PIPELINE_TIMEOUT):
            async with ctx["sessionmaker"]() as db:
                # Per JOB, not per worker start. The chunking knobs are editable
                # from the 고급 설정 screen, and this is what makes an edit apply
                # to the next document instead of to the next deploy - the same
                # "no restart, no invalidation message to lose" rule get_prompt
                # follows. There is no middleware here to publish a sessionmaker
                # into current_sessionmaker, so the job's own session is passed
                # explicitly.
                settings = await effective_settings(db, ctx["settings"])
                await run_pipeline(
                    db,
                    PgVectorStore(db),
                    ctx["llm_provider"],
                    get_chunking_strategy(settings),
                    document_id,
                )
    except BaseException as exc:
        # BaseException, and here rather than in a WorkerSettings hook: arq 0.26
        # has no on_job_failure, and get_kwargs() silently DROPS any attribute
        # that is not a Worker parameter - so a hook by that name would look
        # configured and never run.
        #
        # arq retries only Retry/RetryJob/CancelledError. Everything else -
        # including the TimeoutError from PIPELINE_TIMEOUT above - is finished on
        # the spot, so this is the document's last chance to be recorded and the
        # mark is unconditional.
        #
        # CancelledError therefore means SIGTERM, which arq DOES re-queue.
        # Marking those failed would tell every user mid-deploy that their file
        # was bad; the retry carries the document instead. On the last try there
        # is no retry left, so mark it. Default to max_tries so a missing
        # job_try fails safe rather than silently disabling the handler.
        # Shielded: the cleanup must survive the cancellation that caused it.
        # `is_shutdown`, not `shutdown`: this module registers the module-level
        # `shutdown` coroutine as WorkerSettings.on_shutdown, and a local by that
        # name reads like a rebinding of the hook.
        is_shutdown = isinstance(exc, asyncio.CancelledError)
        if not is_shutdown or ctx.get("job_try", WorkerSettings.max_tries) >= WorkerSettings.max_tries:
            await asyncio.shield(mark_failed(ctx, document_id))
        raise


class WorkerSettings:
    functions = [process_document]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # Defaults are 300s / 5 tries. A long PDF gets killed mid-parse at 300s, and
    # 5 tries multiplied the corpus before the pipeline deleted first. The
    # margin over PIPELINE_TIMEOUT keeps arq's cancellation unreachable, so a
    # hung job is ours to mark rather than arq's to silently finish.
    job_timeout = PIPELINE_TIMEOUT + 30
    max_tries = 2
    keep_result = 3600
    # ponytail: a SIGKILL/OOM leaves the document at `parsing` with no try left
    # to reap it. A sweeper for non-terminal documents older than job_timeout
    # belongs in the observability slice, not here.
