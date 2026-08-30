import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# HTTP only. stdio is deliberately not supported: it would mean this container
# spawning and supervising child processes an admin named through a web form -
# a second lifecycle, a sandbox question per registered server, and no answer
# for horizontal scaling. Recorded here as well as in the design doc because
# this is where someone would add it. See the Slice 2 section of
# docs/superpowers/specs/2026-08-30-slices-2-to-5-design.md.
MCP_AUTH_KINDS = ("none", "bearer")

# Ordered least to most dangerous; `RISK_LEVELS.index` is the comparison.
RISK_LEVELS = ("read", "write", "destructive")

# The default a newly discovered tool gets, and NOT `read`. An unclassified tool
# must not be the cheap one: the server author's own description is not a
# security boundary, and the cost of mis-defaulting downward is an unattended
# destructive call. An admin demotes a tool to `read` deliberately, per tool.
DEFAULT_RISK_LEVEL = "write"


class McpServer(Base):
    """An MCP server an admin registered, reachable over HTTP.

    `auth_token` is write-only over the API: accepted on create/update, never
    returned by any endpoint, never logged, and stripped out of anything a
    server sends back (app/mcp/client.py:redact). It is stored in plaintext
    because this deployment has no key management to encrypt it with, and the
    admin screen says so in as many words rather than implying a protection that
    is not there.
    """

    __tablename__ = "mcp_servers"
    __table_args__ = (
        # The name is what the tool picker and every citation ref
        # ("mcp:{server}/{tool}") show, so two servers called the same thing
        # make a citation unresolvable.
        UniqueConstraint("name", name="uq_mcp_servers_name"),
        CheckConstraint("auth_kind in ('none', 'bearer')", name="ck_mcp_servers_auth_kind_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    auth_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none", server_default=text("'none'")
    )
    auth_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # RESTRICT and NOT NULL, exactly as collections.created_by and
    # documents.uploaded_by: deleting a user must not silently delete a server
    # every other user's answers may be citing. Accounts are deactivated, never
    # deleted (app/models/user.py), so this is not a dead end.
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class McpTool(Base):
    """One tool as `tools/list` last reported it.

    A tool that DISAPPEARS from a later discovery is disabled, never deleted.
    `messages.citations` and `messages.trace` reference it by name, and a
    citation pointing at a row that no longer exists is worse than a tombstone
    that says "this server stopped offering this tool".
    """

    __tablename__ = "mcp_tools"
    __table_args__ = (
        UniqueConstraint("server_id", "name", name="uq_mcp_tools_server_name"),
        CheckConstraint(
            "risk_level in ('read', 'write', 'destructive')", name="ck_mcp_tools_risk_level_valid"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # PRESENT FROM THIS TABLE'S FIRST MIGRATION, and not decoration: Slice 3's
    # human-approval gate reads it, and adding it later would mean a migration
    # plus a re-discovery pass over every registered server. Slice 2 already
    # enforces it - the manual invocation path refuses `destructive` outright,
    # because the gate that would ask a human does not exist yet.
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DEFAULT_RISK_LEVEL, server_default=text("'write'")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
