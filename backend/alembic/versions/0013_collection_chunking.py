"""collections.chunking - per-collection chunking configuration

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-31
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# WHY. The section-marker chunking strategy used to select itself by sniffing
# every parsed document for a hardcoded Korean goods-classification regex. That
# made one corpus work and every other user's documents unreachable by it. The
# strategy is now chosen, and configured, by the collection that holds the
# documents - see app/rag/chunking/table.py.
#
# NOT NULL with a '{}' default, so every existing collection reads as "chunk my
# documents as prose", which is exactly what they all did before this column
# existed. Nothing is re-chunked by this migration; a document is re-cut when it
# is re-ingested, which is a deliberate act.


def upgrade() -> None:
    op.add_column(
        "collections",
        sa.Column(
            "chunking",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("collections", "chunking")
