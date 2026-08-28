from fastapi import Request
from redis.asyncio import Redis

from app.core.config import Settings


def make_redis(settings: Settings) -> Redis:
    """Sessions and cache ONLY. decode_responses=True is right for JSON/str values
    but breaks arq, which stores binary payloads - arq gets its own ArqRedis
    (see app/documents/service.py)."""
    return Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis(request: Request) -> Redis:
    return request.app.state.redis
