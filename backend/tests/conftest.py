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
    # Before `messages`, which it points at. Truncated per test like every other
    # row a test writes - and `app_settings` below is the one that MATTERS: a
    # leftover override would silently change retrieval for every later test, and
    # a "behaves like .env when the table is empty" test would pass with its
    # guard removed because the table was never empty.
    "message_feedback",
    "app_settings",
    "messages",
    "conversations",
    # Before `collections` and `mcp_tools`, which they point at, and before
    # `workflows`, which they cascade from. Without these here a "when the
    # workflows table is empty" test would pass with its guard removed, because
    # the table would never actually be empty - the trap this list already
    # documents for app_settings. `workflow_versions` is in the same position:
    # it cascades from `workflows`, and a leftover version row is a graph that
    # would still be listed in the `@` menu.
    "workflow_collections",
    "workflow_tools",
    "workflow_versions",
    "workflows",
    "chunks",
    "documents",
    "collections",
    # mcp_tools before mcp_servers, and both before users: mcp_servers.created_by
    # is ON DELETE RESTRICT, so truncating users alone would fail. CASCADE on the
    # statement covers it either way; the order is stated so the list still reads
    # as a dependency order.
    "mcp_tools",
    "mcp_servers",
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
    # And drop the prompt rows the migrations seed.
    #
    # `clean_db` truncates users CASCADE after every DB test, which takes
    # `prompts` with it - so from the second such test onward the table is empty
    # and `get_prompt` answers from the module constant. The FIRST one saw the
    # seeded rows, and that was invisible only while 0004's row happened to be
    # identical to the constant. It is not any more: 0009 activates a later
    # version, so "which prompt version answered" started depending on test
    # ORDER. Emptying it here makes every DB test start where all but one of
    # them already did. What the migrations seed is asserted by the two
    # integration tests that re-run them and read the table directly.
    asyncio.run(_truncate_prompts())


async def _truncate_prompts() -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE prompts CASCADE"))
    finally:
        await engine.dispose()


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
            # Pinned OFF for the same reason as the two above, and this one is
            # worth spelling out. Most tests here run /api/chat against an empty
            # or tiny corpus, so retrieval legitimately comes back weak and the
            # clarification branch legitimately fires - which would make five
            # tests that are about something else entirely (usage capture, prompt
            # versioning, workflow prompt selection) start asserting against
            # "clarify_agent". Those tests are not wrong; they are simply not
            # about this branch.
            #
            # The branch itself is covered by tests/test_clarify.py, which
            # switches it back on deliberately. The cost of this line is that the
            # SHIPPED default (True) is not exercised end to end, which is why
            # the live check in the plan's Task 146 is not optional.
            "clarify_on_weak_evidence": False,
            # Pinned for the same reason again: docker-compose.yml now defaults
            # this to true so the bundled 생활정보 MCP(컨테이너망 주소) can be
            # registered, and inheriting that would turn the SSRF-guard tests
            # into connection attempts. The escape-hatch test switches it on
            # deliberately (tests/test_mcp.py).
            "mcp_allow_private_networks": False,
            # Pinned EMPTY for the same reason: compose sets this so the deployed
            # backend auto-registers the bundled 표 조회 MCP on first admin
            # registration, and inheriting it would make every first-register
            # fixture attempt a (refused) discovery and leave an extra
            # mcp_servers row under unrelated tests. The seeding tests in
            # tests/test_mcp.py set it deliberately.
            "bundled_mcp_seed_url": "",
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
