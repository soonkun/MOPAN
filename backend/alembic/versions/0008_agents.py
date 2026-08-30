"""agents, their allowed collections and tools, and which agent answered

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # A NAME from `prompts`, never the text. The prompt store owns versioning
        # and attribution; an agent carrying its own copy would fork it back out.
        sa.Column(
            "prompt_name", sa.String(100), nullable=False, server_default=sa.text("'answer_agent'")
        ),
        # NULL means the deployment's ANSWER_MODEL. Re-checked against
        # Settings.selectable_models on every request as well as on save - a row
        # must not be able to smuggle a de-allowlisted model past the gate.
        sa.Column("answer_model", sa.String(100), nullable=True),
        sa.Column("orchestrator", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_agents"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_agents_created_by_users", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("name", name="uq_agents_name"),
    )
    op.create_index("ix_agents_created_by", "agents", ["created_by"])

    # THE PERMISSION BOUNDARY, as two join tables. Empty means unrestricted - see
    # the class docstring in app/models/agent.py - so an agent with no rows in
    # either behaves exactly as this app did before agents existed, which is what
    # makes deploying this migration a no-op.
    op.create_table(
        "agent_collections",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("agent_id", "collection_id", name="pk_agent_collections"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name="fk_agent_collections_agent_id_agents", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name="fk_agent_collections_collection_id_collections",
            ondelete="CASCADE",
        ),
    )
    # The composite primary key indexes agent_id only. collection_id needs its
    # own or tests/test_schema.py:test_every_foreign_key_is_indexed_and_not_null
    # fails - and a cascade from a deleted collection would seq-scan.
    op.create_index("ix_agent_collections_collection_id", "agent_collections", ["collection_id"])

    op.create_table(
        "agent_tools",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("agent_id", "tool_id", name="pk_agent_tools"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name="fk_agent_tools_agent_id_agents", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"], ["mcp_tools.id"], name="fk_agent_tools_tool_id_mcp_tools", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_agent_tools_tool_id", "agent_tools", ["tool_id"])

    # Beside `model` and `prompt_name` on the message, because it is the same
    # kind of fact. NULL on every row written before this migration and on every
    # answer given by the default agent, which the trace screen renders as 기본.
    # A string rather than an FK on purpose: see app/models/message.py.
    op.add_column("messages", sa.Column("agent_name", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "agent_name")
    # Join tables first: both reference `agents`. Every pytest session opens with
    # `downgrade base`, so this path runs constantly.
    op.drop_table("agent_tools")
    op.drop_table("agent_collections")
    op.drop_table("agents")
