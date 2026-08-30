"""conversation trace, message feedback, runtime settings

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT NULL with a server_default rather than a nullable column: every message
    # written before this migration gets {}, which app/schemas/observability.py
    # already has to handle for an assistant answer whose retrieval produced
    # nothing. A nullable column would add a second empty case meaning the same
    # thing. The server_default stays on the column so a plain INSERT that names
    # no trace - a user turn - is legal.
    op.add_column(
        "messages",
        sa.Column("trace", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.create_table(
        "message_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.String(10), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_message_feedback"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_message_feedback_message_id_messages",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_message_feedback_user_id_users",
            ondelete="CASCADE",
        ),
        # One rating per user per message, as a database rule. Two clicks racing
        # each other lose the second to this constraint instead of writing two
        # rows that disagree about what the user thinks.
        sa.UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
        sa.CheckConstraint("rating in ('up', 'down')", name="ck_message_feedback_rating_valid"),
    )
    op.create_index("ix_message_feedback_message_id", "message_feedback", ["message_id"])
    op.create_index("ix_message_feedback_user_id", "message_feedback", ["user_id"])

    # Deliberately NOT seeded. An empty table is the "no overrides" state, and it
    # has to be the state a fresh deployment starts in: seeding the .env values
    # here would freeze them into the database at migration time, so a later
    # change to .env would silently stop applying.
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("key", name="pk_app_settings"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("message_feedback")
    op.drop_column("messages", "trace")
