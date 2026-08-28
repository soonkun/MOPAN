import pytest
from sqlalchemy import text

from app.core.db import engine


@pytest.mark.asyncio
async def test_all_tables_exist():
    expected = {"users", "collections", "documents", "chunks", "conversations", "messages"}
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        )
        tables = {row[0] for row in result}
    assert expected.issubset(tables)
