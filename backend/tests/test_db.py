import asyncio

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import make_engine


@pytest.mark.integration
def test_engine_survives_sequential_event_loops(test_database_url):
    """The previous implementation created the engine at module import. Pooled
    asyncpg connections stayed bound to the loop that opened them, so the second
    of three sequential asyncio.run() calls failed non-deterministically with
    'Event loop is closed' then "'NoneType' object has no attribute 'send'".

    make_engine is per-lifespan, so each loop builds and disposes its own pool.
    Deliberately uses the real pooled engine -- conftest's NullPool test_engine
    sidesteps the property under test.
    """
    settings = get_settings().model_copy(update={"database_url": test_database_url})

    async def roundtrip() -> int:
        engine = make_engine(settings)
        try:
            async with engine.connect() as conn:
                return await conn.scalar(text("SELECT 1"))
        finally:
            await engine.dispose()

    assert [asyncio.run(roundtrip()) for _ in range(3)] == [1, 1, 1]
