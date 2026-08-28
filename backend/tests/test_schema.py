import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from app.core.config import get_settings
from app.models import Base

pytestmark = pytest.mark.integration


async def test_orm_matches_migrated_schema(test_engine):
    """The highest-value test in the project: it makes ORM/migration drift and
    silently-dropped retrieval indexes impossible to reintroduce."""

    # compare_server_default is off by default, so without it a server_default
    # that exists on one side only drifts silently - the same blindness alembic
    # applies to nullability on computed columns.
    def _diff(connection):
        context = MigrationContext.configure(connection, opts={"compare_server_default": True})
        return compare_metadata(context, Base.metadata)

    async with test_engine.connect() as conn:
        diff = await conn.run_sync(_diff)

    assert diff == [], f"ORM/migration drift detected: {diff}"


async def test_vector_extension_is_installed(test_engine):
    async with test_engine.connect() as conn:
        installed = await conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
    assert installed == 1


async def test_content_tsv_is_a_stored_generated_column(test_engine):
    async with test_engine.connect() as conn:
        generated = await conn.scalar(
            text(
                "SELECT is_generated FROM information_schema.columns "
                "WHERE table_name = 'chunks' AND column_name = 'content_tsv'"
            )
        )
    assert generated == "ALWAYS"


async def test_retrieval_indexes_exist_with_expected_access_methods(test_engine):
    async with test_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT i.relname, am.amname FROM pg_index x "
                    "JOIN pg_class i ON i.oid = x.indexrelid "
                    "JOIN pg_class t ON t.oid = x.indrelid "
                    "JOIN pg_am am ON am.oid = i.relam "
                    "WHERE t.relname = 'chunks'"
                )
            )
        ).all()
    methods = {name: am for name, am in rows}
    assert methods.get("ix_chunks_content_tsv") == "gin"
    assert methods.get("ix_chunks_embedding") == "hnsw"
    assert "ix_chunks_document_id" in methods


async def test_every_foreign_key_is_indexed_and_not_null(test_engine):
    """pg_constraint rather than information_schema: it carries confdeltype, so
    one query covers all three properties the name promises."""
    async with test_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT t.relname, a.attname, a.attnotnull, con.confdeltype, "
                    "  EXISTS (SELECT 1 FROM pg_index i "
                    "          WHERE i.indrelid = con.conrelid "
                    "            AND a.attnum = ANY (i.indkey[0:0])) AS indexed "
                    "FROM pg_constraint con "
                    "JOIN pg_class t ON t.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "JOIN pg_attribute a ON a.attrelid = con.conrelid "
                    "  AND a.attnum = con.conkey[1] "
                    "WHERE con.contype = 'f' AND n.nspname = 'public'"
                )
            )
        ).all()

    assert rows, "no foreign keys found - schema is not migrated"
    bad = [
        (table, column, notnull, ondelete, indexed)
        for table, column, notnull, ondelete, indexed in rows
        # 'a' is NO ACTION: deleting a parent raises instead of cascading.
        if not notnull or ondelete == "a" or not indexed
    ]
    assert bad == [], f"FKs missing NOT NULL, ondelete, or a leading index: {bad}"


async def test_embedding_column_width_matches_settings(test_engine):
    async with test_engine.connect() as conn:
        typmod = await conn.scalar(
            text(
                "SELECT a.atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = 'chunks' AND a.attname = 'embedding'"
            )
        )
    assert typmod == get_settings().embedding_dim


def test_downgrade_then_upgrade_round_trips(migrated_database):
    """A broken downgrade() is otherwise discovered at the worst possible moment."""
    from tests.conftest import BACKEND_DIR, TEST_DATABASE_URL

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
