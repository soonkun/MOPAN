import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

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
    "attachments",
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


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """Ensure mopan_test exists, without requiring a schema. Sync fixture on
    purpose: it owns its own short-lived loop and leaves no connection behind."""
    asyncio.run(_create_database_if_missing())
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def migrated_database(test_database_url) -> None:
    """Rebuild mopan_test from scratch. Separate from test_database_url so a test
    that only needs a connection does not drag in alembic.

    downgrade base first, not just upgrade head: 0001 is amended in place until
    Slice 1 ships, and upgrade head is a no-op on a database already stamped at
    0001 - so the drift test would compare against a stale schema."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(config, "base")
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
    # Every value the suite depends on is pinned here rather than inherited from
    # the operator's .env. `allow_self_registration` was inherited, and setting
    # it to false before opening a public tunnel turned 29 tests red - a
    # deployment decision must not be able to fail the suite. A test that cares
    # about the flag overrides it locally (see test_auth.py); the rest get the
    # default this fixture states out loud.
    settings = get_settings().model_copy(
        update={
            "upload_dir": tmp_path_factory.mktemp("uploads"),
            "allow_self_registration": True,
            "environment": "development",
            # Pinned for the same reason allow_self_registration is: the model
            # allowlist is a deployment decision, and an operator adding a model
            # to .env must not be able to change what the suite asserts. A test
            # that cares overrides it locally (tests/test_chat.py).
            "answer_model": "gpt-4o",
            "answer_models": [],
        }
    )
    application.state.settings = settings
    application.state.engine = test_engine
    application.state.sessionmaker = test_sessionmaker
    application.state.redis = fake_redis
    # Stubbed here rather than per-test so an upload from any client fixture fails
    # legibly instead of AttributeError-ing into a 500.
    application.state.arq_pool = AsyncMock()

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
async def clean_db(request):
    """Truncate only after tests that actually touched the database.

    Requesting test_engine eagerly would drag every pure unit test through a
    CREATE DATABASE probe, an alembic upgrade and a six-table TRUNCATE, and would
    make them fail on any machine without Postgres. fixturenames is the resolved
    closure, so an indirect dependency (client -> app -> test_engine) still counts.
    """
    yield
    if "test_engine" not in request.fixturenames:
        return
    engine = request.getfixturevalue("test_engine")
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE " + ", ".join(TABLES_IN_DELETE_ORDER) + " CASCADE"))
