import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.collection import Collection
from app.models.mcp import McpTool

# Plain association tables rather than ORM classes: they carry no column of their
# own beyond the pair, and a class would only invite one. Both halves are
# ON DELETE CASCADE - deleting a collection or a tool removes it from every
# workflow that listed it, which is the only truthful outcome: a workflow cannot
# be "allowed" something that no longer exists.
#
# The second index on each is not optional decoration.
# tests/test_schema.py:test_every_foreign_key_is_indexed_and_not_null requires
# every FK column to lead SOME index, and the composite primary key only covers
# the first of the pair.
workflow_collections = Table(
    "workflow_collections",
    Base.metadata,
    Column(
        "workflow_id",
        UUID(as_uuid=True),
        ForeignKey(
            "workflows.id", ondelete="CASCADE", name="fk_workflow_collections_workflow_id_workflows"
        ),
        primary_key=True,
    ),
    Column(
        "collection_id",
        UUID(as_uuid=True),
        ForeignKey(
            "collections.id",
            ondelete="CASCADE",
            name="fk_workflow_collections_collection_id_collections",
        ),
        primary_key=True,
    ),
    Index("ix_workflow_collections_collection_id", "collection_id"),
)

workflow_tools = Table(
    "workflow_tools",
    Base.metadata,
    Column(
        "workflow_id",
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE", name="fk_workflow_tools_workflow_id_workflows"),
        primary_key=True,
    ),
    Column(
        "tool_id",
        UUID(as_uuid=True),
        ForeignKey("mcp_tools.id", ondelete="CASCADE", name="fk_workflow_tools_tool_id_mcp_tools"),
        primary_key=True,
    ),
    Index("ix_workflow_tools_tool_id", "tool_id"),
)


class Workflow(Base):
    """A procedure A PERSON AUTHORED, saved. Formerly `agents`.

    **The word "에이전트" is retired.** 워크플로우 is a graph a person drew;
    슈퍼 에이전트 is the mode where the model draws one per question. Both produce
    the same thing and go through the same executor. Renaming the UI and leaving
    `agent` in the code would hand the next person exactly the confusion this
    slice exists to remove, so the table, the columns, the API paths and the code
    moved together in migration 0010.

    **`orchestrator` IS GONE.** That column is what let "a fixed procedure" switch
    on "autonomous planning" - two layers wired to one checkbox. A workflow is by
    definition not autonomous planning; 슈퍼 에이전트 is a per-conversation choice,
    and the workflow's remaining job on that path is the scope check.

    **The two lists are permission boundaries, not hints.** Enforced in
    `app/workflow/catalogue.py:ResolvedWorkflow`, which `load_available` and
    `app/chat/service.py:retrieve` both go through - never in the UI and never
    only in a prompt. A graph naming a tool this workflow does not carry is
    refused AT SAVE, which is the fourth acceptance criterion of the design.

    **An EMPTY list means unrestricted**, for both. That is what makes "an empty
    workflows table changes nothing" true, and the admin screen prints 전체 허용
    beside an empty selection rather than 없음.
    """

    __tablename__ = "workflows"
    __table_args__ = (
        # The name is what the composer's `@` menu shows, what a `workflow:` node
        # in another graph refers to, and what is persisted on the message. Two
        # workflows called 안전모드 make "which one answered" unanswerable.
        UniqueConstraint("name", name="uq_workflows_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A NAME from the prompt store, not the text. get_prompt(name) resolves it at
    # answer time, so activating a new version of the prompt changes what this
    # workflow says with no edit here.
    prompt_name: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("'answer_agent'")
    )
    answer_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # WHICH VERSION RUNS lives on the version row as `is_active`, not as an
    # `active_version_id` here. Two reasons, and the second is the one that
    # decided it: `prompts` already answers the identical question that way, and a
    # nullable FK pointing the other direction would be the third entry in
    # tests/test_schema.py:NULLABLE_FK_EXCEPTIONS plus a circular
    # workflows <-> workflow_versions constraint that alembic can only create with
    # use_alter. A partial unique index makes "exactly one active version" a
    # database guarantee instead of app code.
    #
    # RESTRICT and NOT NULL, exactly as mcp_servers.created_by: deleting a user
    # must not silently delete a workflow every other user is answering through.
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

    # lazy="selectin" because every reader needs both lists and the session is
    # async, where a lazy load at attribute access raises MissingGreenlet inside
    # response serialisation.
    collections: Mapped[list[Collection]] = relationship(
        secondary=workflow_collections, lazy="selectin", order_by=Collection.name
    )
    tools: Mapped[list[McpTool]] = relationship(
        secondary=workflow_tools, lazy="selectin", order_by=McpTool.name
    )


class WorkflowVersion(Base):
    """One saved graph. **Versions are kept**, the same conclusion the prompt
    store already reached and for the same two reasons: a person editing a
    procedure can make it worse and has to be able to go back, and
    `messages.workflow_version` pointing at a version only means something if the
    version is still there to point at.

    `graph` is the whole thing - nodes, edges, and **node coordinates**. The
    coordinates are stored because a person arranged them and reopening the
    canvas has to show the same picture; they had no column to live in while this
    was `agents`, which is the entire reason the old canvas had no free layout.
    """

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_workflow_version"),
        # "Exactly one active version per workflow" as a DB constraint rather
        # than app code, the way `prompts` already does it: a partial unique
        # index makes a second active row an IntegrityError, so a half-finished
        # activation cannot leave two rows active and `load_available` cannot
        # silently run whichever one it saw first.
        Index(
            "uq_workflow_versions_workflow_active",
            "workflow_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE", name="fk_workflow_versions_workflow_id_workflows"),
        nullable=False,
        index=True,
    )
    # 1, 2, 3 ... per workflow. An integer rather than a timestamp because it is
    # what a person says out loud ("2번으로 되돌려 주세요") and what the message row
    # records.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    graph: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
