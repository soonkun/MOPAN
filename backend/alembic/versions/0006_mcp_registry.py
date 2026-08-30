"""MCP server registry and discovered tools

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("base_url", sa.String(1000), nullable=False),
        sa.Column("auth_kind", sa.String(20), nullable=False, server_default=sa.text("'none'")),
        # Plaintext, and the admin screen says so. There is no key management in
        # this deployment to encrypt against, and a hard-coded key would be
        # obfuscation sold as encryption. It is write-only over the API: no
        # endpoint returns it and no log line carries it.
        sa.Column("auth_token", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_servers"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_mcp_servers_created_by_users", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("name", name="uq_mcp_servers_name"),
        sa.CheckConstraint("auth_kind in ('none', 'bearer')", name="ck_mcp_servers_auth_kind_valid"),
    )
    op.create_index("ix_mcp_servers_created_by", "mcp_servers", ["created_by"])

    op.create_table(
        "mcp_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("server_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "input_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # risk_level is here in this table's FIRST migration, per the Slice 1
        # note that the MCP registry must carry it from the start: Slice 3's
        # human-approval gate reads it, and adding it later would mean a
        # migration plus a re-discovery pass over every registered server.
        # 'write' is the default on purpose - an unclassified tool must not be
        # the cheap one.
        sa.Column("risk_level", sa.String(20), nullable=False, server_default=sa.text("'write'")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "discovered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mcp_tools"),
        sa.ForeignKeyConstraint(
            ["server_id"], ["mcp_servers.id"], name="fk_mcp_tools_server_id_mcp_servers", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),
        sa.CheckConstraint(
            "risk_level in ('read', 'write', 'destructive')", name="ck_mcp_tools_risk_level_valid"
        ),
    )
    op.create_index("ix_mcp_tools_server_id", "mcp_tools", ["server_id"])


def downgrade() -> None:
    # Tools first: mcp_tools.server_id references mcp_servers. Every pytest
    # session opens with `downgrade base`, so this path runs constantly.
    op.drop_table("mcp_tools")
    op.drop_table("mcp_servers")
