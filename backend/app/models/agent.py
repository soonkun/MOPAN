import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.collection import Collection
from app.models.mcp import McpTool

# Plain association tables rather than ORM classes: they carry no column of their
# own beyond the pair, and a class would only invite one. Both halves are
# ON DELETE CASCADE - deleting a collection or a tool removes it from every
# agent that listed it, which is the only truthful outcome: an agent cannot be
# "allowed" something that no longer exists.
#
# The second index on each is not optional decoration.
# tests/test_schema.py:test_every_foreign_key_is_indexed_and_not_null requires
# every FK column to lead SOME index, and the composite primary key only covers
# the first of the pair.
agent_collections = Table(
    "agent_collections",
    Base.metadata,
    Column(
        "agent_id",
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE", name="fk_agent_collections_agent_id_agents"),
        primary_key=True,
    ),
    Column(
        "collection_id",
        UUID(as_uuid=True),
        ForeignKey(
            "collections.id",
            ondelete="CASCADE",
            name="fk_agent_collections_collection_id_collections",
        ),
        primary_key=True,
    ),
    Index("ix_agent_collections_collection_id", "collection_id"),
)

agent_tools = Table(
    "agent_tools",
    Base.metadata,
    Column(
        "agent_id",
        UUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE", name="fk_agent_tools_agent_id_agents"),
        primary_key=True,
    ),
    Column(
        "tool_id",
        UUID(as_uuid=True),
        ForeignKey("mcp_tools.id", ondelete="CASCADE", name="fk_agent_tools_tool_id_mcp_tools"),
        primary_key=True,
    ),
    Index("ix_agent_tools_tool_id", "tool_id"),
)


class Agent(Base):
    """A saved configuration, and DELIBERATELY NOT CODE.

    Name, description, which prompt answers, which collections it may search,
    which MCP tools it may call, which model answers, whether the orchestrator
    runs. That is the whole thing. The moment an agent needs custom logic it
    stops being a row and becomes a deployment, and the platform's entire claim
    is that a user assembles one without a deployment. There is no hook column
    here and there is not meant to be one.

    **The two lists are permission boundaries, not hints.** They are enforced in
    `app/agents/service.py:ResolvedAgent`, which
    `app/orchestrator/plan.py:load_available` and
    `app/chat/service.py:retrieve` both go through - never in the UI and never
    only in the planner's prompt. A plan step naming a tool this agent does not
    carry is refused WHOLE, the way a hallucinated tool name already is: a model
    that named one thing it may not touch has told you what its other choices
    are worth.

    **An EMPTY list means unrestricted**, for both. That is the rule that makes
    "an empty agents table changes nothing" and "an agent that only swaps the
    prompt" the same rule rather than two, and it is what the default agent is:
    ResolvedAgent with both sets empty behaves exactly as this app did before
    agents existed. A restriction is therefore a positive act, and the admin
    screen says 전체 허용 beside an empty selection rather than 없음 - the one
    place this rule could mislead somebody is the one place it is spelled out.

    `answer_model` is nullable and means "the deployment default". It is
    re-checked against Settings.selectable_models on every request as well as on
    save: an operator can remove a model from ANSWER_MODELS long after an admin
    picked it, and the row must not be able to smuggle it past the allowlist.
    """

    __tablename__ = "agents"
    __table_args__ = (
        # The name is what the composer's picker shows and what is persisted on
        # the message and rendered in the trace. Two agents called 안전모드 make
        # "which agent answered" unanswerable.
        UniqueConstraint("name", name="uq_agents_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A NAME from the prompt store, not the text. The whole point of Slice 4's
    # prompt half is that the text is versioned, editable and attributable; an
    # agent carrying its own copy would fork it back out of that history on the
    # first save. get_prompt(name) resolves it at answer time, so activating a
    # new version of the prompt changes what this agent says with no edit here.
    prompt_name: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("'answer_agent'")
    )
    answer_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    orchestrator: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # RESTRICT and NOT NULL, exactly as mcp_servers.created_by: deleting a user
    # must not silently delete an agent every other user is answering through.
    # Accounts are deactivated, never deleted, so this is not a dead end.
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

    # lazy="selectin" because every reader of an Agent needs both lists and the
    # session is async, where a lazy load at attribute access raises
    # MissingGreenlet inside response serialisation.
    collections: Mapped[list[Collection]] = relationship(
        secondary=agent_collections, lazy="selectin", order_by=Collection.name
    )
    tools: Mapped[list[McpTool]] = relationship(
        secondary=agent_tools, lazy="selectin", order_by=McpTool.name
    )
