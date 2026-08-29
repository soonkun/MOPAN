"""users.is_active and a unique collection name

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default, not just a Python-side default: every row that predates this
    # migration has to become active, and get_current_user rejects is_active=false,
    # so a NULL or false backfill would log out every existing session.
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_unique_constraint("uq_collections_name", "collections", ["name"])


def downgrade() -> None:
    op.drop_constraint("uq_collections_name", "collections", type_="unique")
    op.drop_column("users", "is_active")
