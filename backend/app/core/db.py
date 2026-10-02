from collections.abc import AsyncIterator
from contextvars import ContextVar

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

# The request's sessionmaker, for code that is deliberately NOT handed a
# session. app/chat/prompt.py:get_prompt is the only reader: it is called from
# chat.service.answer(), whose signature is a guarded Slice 3 seam - it takes no
# db, no vector store and no reranker, and tests/test_chat_service.py asserts
# that - yet the prompt it loads now lives in a table. Set per request by
# RequestContextMiddleware from app.state, so it is the SAME sessionmaker the
# endpoint's own dependency uses, in tests as well as in production. Anything
# holding a session already should keep using it; this is the seam, not a
# shortcut around dependency injection.
current_sessionmaker: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "current_sessionmaker", default=None
)


def make_engine(settings: Settings) -> AsyncEngine:
    """No module-global engine: a pooled asyncpg connection is bound to the event
    loop that opened it, so a global engine breaks non-deterministically across
    loops (tests, arq, uvicorn reload) and is fork-unsafe."""
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=1800,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """받는 쪽은 반드시 `Depends(get_db_session, scope="function")`으로 쓴다(전부 같은 scope여야 세션이 하나로 묶인다).
    fastapi 0.118부터 yield 의존성은 기본이 '응답을 다 보낸 뒤 정리'라, scope 없이 쓰면 SSE 답변이 흐르는 내내
    인증 조회가 연 트랜잭션이 idle-in-transaction으로 연결을 쥔다(동시 사용자 수만큼 풀이 마른다).
    지키는 테스트: test_chat.py::test_no_connection_is_idle_in_transaction_across_the_llm_call"""
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session
