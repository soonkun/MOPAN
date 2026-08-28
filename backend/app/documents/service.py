import logging

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Request

from app.core.config import Settings
from app.core.logging import log_event

logger = logging.getLogger("mopan.documents")


async def make_arq_pool(settings: Settings) -> ArqRedis:
    """arq needs its OWN client: the session/cache Redis is created with
    decode_responses=True, which corrupts arq's binary job payloads."""
    return await create_pool(RedisSettings.from_dsn(settings.redis_url))


def get_arq_pool(request: Request) -> ArqRedis:
    return request.app.state.arq_pool


async def enqueue_document_processing(pool: ArqRedis, document_id: str) -> None:
    await pool.enqueue_job("process_document", document_id)
    log_event(logger, "document_job_enqueued", document_id=document_id)
