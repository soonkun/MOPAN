import asyncio
import os
from pathlib import Path

import asyncpg
import fakeredis.aioredis
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.db import get_db_session
from app.core.redis import get_redis
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
TABLES_IN_DELETE_ORDER = (
    "messages",
    "conversations",
    "chunks",
    "documents",
    "collections",
    "users",
)


def _test_database_url() -> str:
    """Never run tests against the developer's mopan database."""
    override = os.getenv("TEST_DATABASE_URL")
    if override:
        return override
    base, _, _ = get_settings().database_url.rpartition("/")
    return f"{base}/mopan_test"


TEST_DATABASE_URL = _test_database_url()


async def _create_database_if_missing() -> None:
    dsn = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    admin_dsn, _, dbname = dsn.rpartition("/")
    conn = await asyncpg.connect(f"{admin_dsn}/postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Create mopan_test if needed and bring it to head. Sync fixture on purpose:
    it owns its own short-lived loop and leaves no connection behind."""
    asyncio.run(_create_database_if_missing())
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def test_engine(migrated_database):
    # NullPool: every checkout opens a fresh connection bound to the *current*
    # loop and closes it on return, so function-scoped test loops can never reuse
    # a connection created under a dead loop.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield engine
    engine.sync_engine.dispose()


@pytest.fixture(scope="session")
def test_sessionmaker(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db(test_sessionmaker):
    async with test_sessionmaker() as session:
        yield session


@pytest_asyncio.fixture
async def fake_redis():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def app(test_engine, test_sessionmaker, fake_redis, tmp_path_factory):
    """A real app instance wired to the test engine and a fake Redis. No lifespan
    is run, so nothing touches the developer's database or Redis."""
    application = create_app()
    settings = get_settings().model_copy(
        update={"upload_dir": tmp_path_factory.mktemp("uploads")}
    )
    application.state.settings = settings
    application.state.engine = test_engine
    application.state.sessionmaker = test_sessionmaker
    application.state.redis = fake_redis

    async def _override_db():
        async with test_sessionmaker() as session:
            yield session

    application.dependency_overrides[get_db_session] = _override_db
    application.dependency_overrides[get_redis] = lambda: fake_redis
    yield application
    application.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(autouse=True)
async def clean_db(test_engine):
    yield
    async with test_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE TABLE " + ", ".join(TABLES_IN_DELETE_ORDER) + " CASCADE")
        )
