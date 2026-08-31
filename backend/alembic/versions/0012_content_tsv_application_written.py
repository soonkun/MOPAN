"""content_tsv stops being a generated column and becomes application-written

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-31
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# WHY. The sparse arm now tokenizes Korean with character bigrams, in Python
# (app/retrieval/tokenize.py). A GENERATED ... STORED column may only call
# IMMUTABLE SQL, Postgres ships no Korean tokenizer, and this deployment cannot
# install one - pg_available_extensions offers pg_trgm, unaccent and vector and
# nothing else. So the value has to be written by the application.

# The expression 0001 declared, verbatim, including the ::regconfig cast that is
# how Postgres reflects it back - spelled any other way, alembic's computed
# comparison warns on every autogenerate and on every run of the drift test.
_GENERATED = "to_tsvector('simple'::regconfig, content)"


def upgrade() -> None:
    # ONE statement, because Postgres CAN un-generate a column in place:
    # ALTER COLUMN ... DROP EXPRESSION has existed since PG 13 (this deployment
    # is 16.15) and is documented as "turns a stored generated column into a
    # normal base column; existing data in the columns is retained".
    #
    # The alternative that gets reached for - add a plain column, copy, drop the
    # old, rename - is five statements, and worse: dropping the column drops
    # ix_chunks_content_tsv with it, so it forces a full GIN rebuild and leaves a
    # window with no index. DROP EXPRESSION keeps both the data and the index.
    # Kept here as a note only; if this ever has to run on PG < 13, that is the
    # fallback, and it must recreate ix_chunks_content_tsv at the end.
    #
    # NOT NULL survives - DROP EXPRESSION touches only the generated property.
    op.execute("ALTER TABLE chunks ALTER COLUMN content_tsv DROP EXPRESSION")
    # What this migration deliberately does NOT do: backfill the new tokenizer.
    # That needs the Python tokenizer, so it lives in scripts/backfill_tsv.py.
    # Every row keeps the OLD to_tsvector('simple', content) value it already
    # held, so nothing is ever NULL, NOT NULL holds, and the sparse arm keeps
    # answering exactly as it did before. Until the backfill runs,
    # SPARSE_TOKENIZER must stay 'simple' - a bigram query against
    # simple-tokenized rows retrieves nothing at all.


def downgrade() -> None:
    # Lossless: `content` is still there and the expression is a pure function of
    # it, so the column can be recomputed from scratch. Whatever the application
    # had written is discarded and replaced by to_tsvector('simple', content),
    # which is exactly what the column meant before 0012.
    #
    # There is no ADD EXPRESSION, so this direction really is drop-and-recreate,
    # and the index has to come back with it.
    op.execute("ALTER TABLE chunks DROP COLUMN content_tsv")
    op.execute(
        "ALTER TABLE chunks ADD COLUMN content_tsv tsvector "
        f"GENERATED ALWAYS AS ({_GENERATED}) STORED NOT NULL"
    )
    op.execute("CREATE INDEX ix_chunks_content_tsv ON chunks USING gin (content_tsv)")
