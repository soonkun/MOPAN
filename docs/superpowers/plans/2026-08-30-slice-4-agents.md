# MOPAN Slice 4 (remainder) — Agent management — Implementation Plan

> **Scope:** the last item on the roadmap. Slice 4's prompt half shipped as `docs/superpowers/plans/2026-08-30-prompt-admin.md`; this document is the agent half and does not amend that one or any other plan.

**What the spec asks for** (`docs/superpowers/specs/2026-08-30-slices-2-to-5-design.md`, Slice 4):

> An agent is a saved configuration: name, prompt (from the prompt store), allowed collections, allowed MCP tools, model, enabled. The chat picks an agent the way it now picks a model. … **An agent's allowed-tool list is a permission boundary, not a hint.** A plan step naming a tool the agent does not carry is refused server-side. Otherwise "restrict this agent to read-only tools" means nothing.

**What ships:**
- An `agents` table with two join tables, and migration `0008`. An empty table changes nothing.
- `messages.agent_name`, beside `model` and `prompt_version`, rendered in the transcript and in the Slice 5 trace.
- `app/agents/service.py:ResolvedAgent` — the boundary object, applied inside `load_available`, inside `retrieve` and inside `load_tool_calls`.
- Admin CRUD at `/api/agents`, plus `GET /api/agents/selectable` for any authenticated user.
- `POST /api/prompts`, because an agent that could only ever name the deployment's own system prompt is missing the field the feature is about.
- 에이전트 관리 under the sidebar's existing 관리 section, and an agent picker in the composer beside the model picker.

## Decisions

**Configuration, not code, and the schema is where that is enforced.** There is no hook column, no expression column and no `eval`. The row holds a name, a description, a prompt NAME, a model, two boolean flags and two id lists. The moment an agent needs custom logic it stops being a row and becomes a deployment, and the platform's whole claim is that a user assembles one without a deployment.

**The two lists are permission boundaries, and they live in three functions rather than in the router.** `load_available` (the planner's catalogue), `retrieve` (the direct RAG path) and `load_tool_calls` (the composer's own tool picker) each apply the agent themselves. The router narrows too, and that is not redundancy worth deleting: `scope_collections` is idempotent, so applying it twice costs nothing, and a future caller that forgets is still fenced. Two of the three have a test that fails when only that one layer is removed; the end-to-end test needs both the router's and `retrieve`'s removed before evidence from the other collection reaches the trace, which is the layering working.

**A refused plan is refused WHOLE.** A tool the agent does not carry never enters the catalogue, so `validate_plan` cannot resolve the name and throws — the same treatment a hallucinated name already got, and for the reason `app/orchestrator/plan.py` already states: a model that named one thing it may not touch has told you what its other choices are worth. Filtering the plan down to the allowed steps would hand the user a plausible answer with no sign that what they paid a planner call for had been rewritten.

**`collections: []` meant "everything", and that was the hole.** The planner is entitled to omit the field, and the executor turned the resulting empty tuple back into `collection_ids=None` — every collection in the DATABASE, not every collection in the catalogue. So an agent's restriction was one omitted JSON key away from gone. `validate_plan` now writes the catalogue out into the step, and the executor's `or None` is deleted: an empty list now means an empty catalogue, which `hybrid_search` reads as an `IN ()` predicate matching no row. That is the truthful answer; `or None` was the inversion.

**EMPTY MEANS UNRESTRICTED, for both lists.** This is the one rule here that could mislead somebody, so it is stated in the model docstring, in the API schema, on the admin form beside each empty selection (전체 허용) and in the table (전체, never 없음). It is what makes "an empty agents table changes nothing" and "an agent that only swaps the prompt" the same rule rather than two, and it is exactly what `DEFAULT_AGENT` is — a `ResolvedAgent` with both sets empty, not a special case handled somewhere.

**`messages.agent_name` is a string, not a foreign key.** `model` and `prompt_name` are already denormalised strings for this reason. An agent is configuration an admin deletes when it stops being useful, and a transcript that answers "which agent said this" with a 404 — or worse, cascades the message away with it — is not a record. `uq_agents_name` makes the name identify one row while it exists, and the string outlives it.

**The agent supplies defaults, never a ceiling.** `model = payload.model or agent.answer_model or settings.answer_model`, and the `selectable_models` allowlist still decides what reaches the provider — an operator can drop a model from `ANSWER_MODELS` long after an admin picked it, so the row is re-checked on every request as well as on save. The orchestrator is the one asymmetry: an agent that carries it turns it on and there is no way to turn it off for one, because that is the agent's configuration. The composer shows the toggle pressed and DISABLED with a Korean title saying why, rather than letting a click be silently ignored.

**A refusal happens before the conversation row exists.** The agent is resolved first in `POST /api/chat`, before the model, the attachments and the tools, because everything below is resolved against it — and because every pre-flight check in that router runs before the row for the same reason: once a `StreamingResponse` has begun there is no status line left to set, and a refusal would degrade into an error frame inside a 200 having left a titled empty conversation in the sidebar.

**The pause re-loads the agent.** `POST /api/chat/approve` stores the agent id, not the resolved object, and re-loads it — so an agent an admin disabled, or whose tool list they trimmed, while the user was deciding refuses the resumed plan exactly as it would refuse a fresh one. That is the same rule the stored plan already followed.

**`POST /api/prompts` is a real addition and not scope creep.** `POST /api/prompts/{name}/versions` 404s on an unknown name so a typo cannot silently fork the answer prompt; there was therefore no way to create a third prompt name at all, and an agent's "prompt from the prompt store" could only ever be the deployment's own. The new endpoint 409s on a name that already exists, so between the two there is no way to create a prompt by accident.

## Global Constraints

- Every user-facing `detail=` is natural Korean. `frontend/lib/api.ts:detailText` drops a `detail` with no Hangul, so an English string is invisible to the user.
- Alembic only. Both directions must work: `tests/conftest.py:migrated_database` runs `downgrade base` at the start of every session.
- The `compare_metadata` drift test stays green, and `test_every_foreign_key_is_indexed_and_not_null` means every FK column here is NOT NULL, has an `ondelete`, and leads some index — which is why both join tables carry a second explicit index.
- The suite is serial-only. One pytest session at a time, never `-n auto`.
- Tokens only in the UI. A raw hex or a Tailwind default-palette class is a defect.
- No test makes a real network call or a real OpenAI API call.

---

### Task 1: The `agents` table, its join tables, and migration 0008

**Files:**
- Create: `backend/app/models/agent.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/message.py`
- Create: `backend/alembic/versions/0008_agents.py`
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Produces: `Agent`, `agent_collections`, `agent_tools`, `uq_agents_name`, `messages.agent_name`.
- Consumed by: `app/agents/service.py` (Task 2), the admin routes (Task 3), and `tests/test_schema.py:test_orm_matches_migrated_schema`, which fails if the ORM and the migration disagree on any of it.

- [ ] **Step 1: Write `backend/app/models/agent.py`**

Two plain association tables and one mapped class. The second index on each join table is not decoration: the composite primary key indexes only the first column of the pair, and the schema test requires every FK column to lead some index.

```python
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
```

- [ ] **Step 2: Modify `backend/app/models/__init__.py`**

`Base.metadata` has to see the new tables or the drift test compares against a schema that does not contain them.

```python
from app.models.agent import Agent, agent_collections, agent_tools
from app.models.app_setting import AppSetting
```

- [ ] **Step 3: Modify `backend/app/models/message.py`**

Beside `model` and `prompt_name`, and a string for the same reason they are.

```python
    # WHICH AGENT ANSWERED, beside the model and the prompt version because it is
    # the same kind of fact: what this answer was produced under. NULL means the
    # default agent - the app behaving exactly as it did before agents existed -
    # which is also every row written before migration 0008.
    #
    # A NAME, not a foreign key into `agents`, and that is the deliberate part:
    # `model` and `prompt_name` are already denormalised strings for this reason.
    # An agent is configuration an admin deletes when it stops being useful, and
    # a transcript that answers "which agent said this" with a 404 - or worse,
    # cascades the message away with it - is not a record. uq_agents_name makes
    # the name identify one row while it exists, and the string outlives it.
    agent_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
```

- [ ] **Step 4: Create `backend/alembic/versions/0008_agents.py`**

Both directions run constantly: every pytest session opens with `downgrade base`.

```python
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
```

- [ ] **Step 5: Modify `backend/tests/conftest.py`**

The trap this list already documents for `app_settings`, now for `agents`: a session-scoped database means a "when the table is empty" test can pass with its guard removed because the table was never empty.

```python
    # Before `collections` and `mcp_tools`, which they point at, and before
    # `agents`, which they cascade from. Without these two here a "when the
    # agents table is empty" test would pass with its guard removed, because the
    # table would never actually be empty - the trap this list already documents
    # for app_settings.
    "agent_collections",
    "agent_tools",
    "agents",
```

---

### Task 2: `ResolvedAgent` — the boundary object

**Files:**
- Create: `backend/app/agents/__init__.py` (empty)
- Create: `backend/app/agents/service.py`

**Interfaces:**
- Produces: `ResolvedAgent`, `DEFAULT_AGENT`, `AgentScopeError`, `resolve()`, `load_agent()`, and the four Korean refusal strings.
- Consumed by: `load_available`, `retrieve` and `load_tool_calls` below, and the chat router in Task 3.

This is the whole of the enforcement, in one detached, frozen dataclass with no session attached — the same rule `MCPTarget` follows, because it travels through the streaming generator and into the executor, both of which run with no database session open.

- [ ] **Step 1: Write `backend/app/agents/service.py`**

```python
"""What an agent is at request time, and the boundary that enforces it.

THE TWO LISTS ARE PERMISSION BOUNDARIES, NOT HINTS. An admin who reads
"이 에이전트는 A 분류만 사용" on a screen has been told something, and the only
way that sentence is true is if the restriction lives where nothing routes
around it. So it lives here, in one object, and the two functions that decide
what a question may reach - `app/orchestrator/plan.py:load_available` and
`app/chat/service.py:retrieve` - both apply it themselves rather than trusting
the router to have narrowed first. Applying it twice is free: intersecting an
already-intersected set changes nothing.

The refusal posture is the orchestrator's, deliberately. A plan naming a tool
this agent does not carry is not filtered down to the steps that are allowed -
`load_available` never puts the tool in the catalogue, so `validate_plan` cannot
resolve the name and refuses the plan WHOLE and falls back to plain RAG. A model
that named one thing it may not touch has told you what its other choices are
worth.

EMPTY MEANS UNRESTRICTED, for both lists. That is what makes the default agent -
`DEFAULT_AGENT`, used when the request names none - identical to the app as it
behaved before agents existed, and it is why an empty `agents` table changes
nothing. It is the one rule here that could mislead an admin, so the admin
screen prints 전체 허용 beside an empty selection rather than 없음.
"""

import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent

AGENT_NOT_FOUND_MESSAGE = "에이전트를 찾을 수 없습니다."
AGENT_DISABLED_MESSAGE = "사용이 중지된 에이전트입니다. 관리자에게 문의해 주세요."
COLLECTION_NOT_ALLOWED_MESSAGE = "이 에이전트가 사용할 수 없는 분류입니다."
TOOL_NOT_ALLOWED_MESSAGE = "이 에이전트가 사용할 수 없는 도구입니다."

# The prompt an agent answers with unless it names another. Matches
# app/chat/service.py:answer's own default, which is what makes the default
# agent a no-op rather than a second code path.
DEFAULT_PROMPT_NAME = "answer_agent"


class AgentScopeError(ValueError):
    """The request asked for something outside this agent's boundary.

    A ValueError rather than an HTTPException so the boundary object stays
    usable off the request path (the executor, the tests, a future worker). The
    router turns it into a 400 with this message, which is already Korean.
    """


@dataclass(frozen=True)
class ResolvedAgent:
    """One agent, flattened to what a request needs, with no session attached.

    Detached on purpose, the same rule `app/mcp/client.py:MCPTarget` follows: it
    travels through the streaming generator and into the executor, both of which
    run with no database session open.
    """

    id: uuid.UUID | None = None
    # None is the default agent, and it is what lands in `messages.agent_name`:
    # NULL there means "the app answered as it always did".
    name: str | None = None
    prompt_name: str = DEFAULT_PROMPT_NAME
    answer_model: str | None = None
    orchestrator: bool = False
    # EMPTY = UNRESTRICTED for both. See the module docstring.
    collection_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    tool_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def scope_collections(self, requested: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        """The collections this question may actually search.

        None out means "no restriction" - which is what `hybrid_search` reads as
        every collection. A LIST out is a closed set, and an empty list is a
        closed set of nothing: `collection_ids=[]` renders as an IN () predicate
        that matches no row, so it returns no evidence rather than silently
        falling back to everything. That distinction is the whole guard; the
        `or None` that used to sit in the executor is exactly how it gets lost.

        Idempotent, so both the router and `load_available` can call it.
        """
        if not self.collection_ids:
            return requested
        if requested is None:
            return sorted(self.collection_ids, key=str)
        allowed = [c for c in requested if c in self.collection_ids]
        if not allowed:
            # Refused, not silently emptied. A question scoped to a collection
            # this agent cannot reach is a mistake worth a sentence; answering it
            # from nothing would look like the corpus had no answer.
            raise AgentScopeError(COLLECTION_NOT_ALLOWED_MESSAGE)
        return allowed

    def allows_tool(self, tool_id: uuid.UUID) -> bool:
        return not self.tool_ids or tool_id in self.tool_ids


# The agent a request gets when it names none: every field at the value the app
# used before agents existed. `answer()` already defaults to `answer_agent`, the
# orchestrator already defaults to off, and both sets are empty, so nothing about
# this object narrows anything.
DEFAULT_AGENT = ResolvedAgent()


def resolve(agent: Agent) -> ResolvedAgent:
    return ResolvedAgent(
        id=agent.id,
        name=agent.name,
        prompt_name=agent.prompt_name,
        answer_model=agent.answer_model,
        orchestrator=agent.orchestrator,
        collection_ids=frozenset(c.id for c in agent.collections),
        tool_ids=frozenset(t.id for t in agent.tools),
    )


async def load_agent(db: AsyncSession, agent_id: uuid.UUID | None) -> ResolvedAgent:
    """Resolve the agent a chat request named, or refuse.

    Called BEFORE the conversation is created and before the StreamingResponse
    begins, for the reason every other pre-flight check in that router is: once
    the status line is on the wire a refusal degrades into an error frame inside
    a 200, and a bad agent id must not leave a titled, empty conversation in the
    sidebar.
    """
    if agent_id is None:
        return DEFAULT_AGENT
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if agent is None:
        raise HTTPException(status_code=404, detail=AGENT_NOT_FOUND_MESSAGE)
    if not agent.enabled:
        # 409, not the 404 above: the row exists and an admin turned it off, so
        # there is nothing to conceal - only a state to explain. Same rule
        # app/mcp/service.py:load_tool_calls follows for a disabled tool.
        raise HTTPException(status_code=409, detail=AGENT_DISABLED_MESSAGE)
    return resolve(agent)
```

#### The three places the boundary is applied

Every one of these has a test that fails when it alone is removed. The pair in `chat/service.py` and the router is the exception and deliberately so: either layer alone catches the leak, and the end-to-end test needs both gone.

- [ ] **Step 2: Modify `backend/app/orchestrator/plan.py`** — the catalogue narrows to the agent

```python
async def load_available(
    db: AsyncSession,
    collection_ids: list[uuid.UUID] | None = None,
    agent: ResolvedAgent = DEFAULT_AGENT,
) -> AvailableResources:
    """What this question may reach.

    `collection_ids` is the scope the REQUEST asked for, and narrowing here is
    what makes "the planner may only name collections that were passed to it"
    true rather than aspirational: a user who scoped their question to one
    collection gets a plan that cannot search another, and `validate_plan`
    refuses one that tries. Slice 1's authorization model already says every
    authenticated user may READ every collection, so this is a scoping boundary
    on top of that, not a replacement for it.

    `agent` is the SECOND, stronger narrowing, and THIS IS THE PLACE IT CANNOT BE
    BYPASSED. An agent's allowed-collection and allowed-tool lists are permission
    boundaries: a tool the agent does not carry never enters the catalogue, so
    `validate_plan` cannot resolve its name and refuses the whole plan - the same
    treatment a hallucinated tool name gets, and for the same reason. Doing it in
    the router instead would make the boundary a habit; doing it here makes it a
    property of the only function that can produce a catalogue.

    The agent scope is re-applied here even though the caller has usually applied
    it already. That is deliberate and free - intersecting an already-intersected
    set changes nothing - and it is what stops a future caller that forgets from
    quietly widening the boundary. `scope_collections` raises AgentScopeError for
    a request that names a collection outside the agent, which the router turns
    into a Korean 400; it never silently empties the scope.

    Tools are exactly what `GET /api/mcp/tools` lists - enabled tools on enabled
    servers - so a tool an admin turned off is not merely un-runnable, it is
    invisible to the planner and unnameable in a plan.
    """
    scoped = agent.scope_collections(collection_ids)
    query = select(Collection).order_by(Collection.name)
    if scoped is not None:
        query = query.where(Collection.id.in_(scoped))
    collections = tuple(
        AvailableCollection(id=row.id, name=row.name, description=row.description)
        for row in (await db.scalars(query)).all()
    )
    tool_query = (
        select(McpTool, McpServer)
        .join(McpServer, McpServer.id == McpTool.server_id)
        .where(McpTool.enabled.is_(True), McpServer.enabled.is_(True))
        .order_by(McpServer.name, McpTool.name)
    )
    if agent.tool_ids:
        tool_query = tool_query.where(McpTool.id.in_(agent.tool_ids))
    rows = (await db.execute(tool_query)).all()
    tools = tuple(
        AvailableTool(
            id=tool.id,
            server_name=server.name,
            tool_name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema or {},
            risk_level=tool.risk_level,
            target=MCPTarget(name=server.name, base_url=server.base_url, auth_token=server.auth_token),
        )
        for tool, server in rows
    )
    return AvailableResources(collections=collections, tools=tools)
```

- [ ] **Step 3: Modify `backend/app/orchestrator/plan.py`** — a step that names no collections gets the catalogue written out

This is the hole. `collections: []` meant "every collection in the database"; it now means "every collection this question may reach", resolved in the same place every other name is.

```python
            # NO NAMES MEANS THE WHOLE CATALOGUE, WRITTEN OUT. It used to mean an
            # empty tuple that the executor turned back into `collection_ids=None`
            # - every collection in the database, whatever the catalogue held -
            # and that was the one way an agent's collection restriction could be
            # walked around: a planner that simply omitted "collections" searched
            # outside the agent. `resources.collections` is already narrowed to
            # what this question may reach, so resolving the default here closes
            # it in the same place every other name is resolved.
            chosen = [by_name[name] for name in names] if names else list(resources.collections)
```

- [ ] **Step 4: Modify `backend/app/orchestrator/executor.py`** — delete the `or None`

With the step now carrying a closed set, `or None` would turn "this agent may reach nothing" into "search every collection in the database".

```python
                        # NO `or None`. `validate_plan` now writes the whole
                        # catalogue into a step that named no collections, so an
                        # empty tuple here means the catalogue was empty - and
                        # `or None` would turn "this agent may reach nothing"
                        # into "search every collection in the database", which
                        # is the exact inversion an agent's restriction exists to
                        # prevent. hybrid_search reads [] as an IN () predicate
                        # that matches no row, which is the truthful answer.
                        collection_ids=list(step.collection_ids),
```

- [ ] **Step 5: Modify `backend/app/chat/service.py`** — `retrieve` narrows, and `answer` takes a prompt name

`retrieve` owns its narrowing for the same reason it owns its commit: a boundary a caller has to remember is not a boundary. `answer`'s new parameter is a defaulted keyword and still carries no session and no retrieval collaborator, which is the property `tests/test_chat_service.py` pins.

```python
async def retrieve(
    db: AsyncSession,
    vector_store: VectorStore,
    llm_provider: LLMProvider,
    reranker: Reranker,
    question: str,
    *,
    settings: Settings,
    collection_ids: list[uuid.UUID] | None = None,
    agent: ResolvedAgent = DEFAULT_AGENT,
) -> list[Evidence]:
    """Slice 3's Orchestrator will produce list[Evidence] a different way (a plan
    running RAG and MCP steps) and hand it to the same answer() below.

    THE AGENT'S COLLECTION RESTRICTION IS APPLIED HERE, not by the caller, for
    the same reason this function owns its own commit below: a boundary a caller
    has to remember is not a boundary. The router narrows too, and that is fine -
    `scope_collections` is idempotent - but this is the line that makes "an agent
    restricted to A cannot return evidence from B" true of the direct RAG path
    however it is reached. DEFAULT_AGENT restricts nothing, so /api/search and
    every pre-agent caller behave exactly as before."""
    # hybrid_search embeds before its first statement, so it opens no transaction
    # across that network call - but only half the property is its to keep. The
    # caller has typically just read the conversation and its history from this
    # same session, and SQLAlchemy autobegins on the first SELECT, so without this
    # the connection sits idle-in-transaction for the whole embedding round trip
    # and the pool is exhausted at a handful of concurrent chats. Ending it here
    # rather than asking every caller to remember is what makes the constraint
    # hold end to end. commit, not rollback: at this point the session holds
    # reads, and rollback would silently discard a caller's pending write.
    # Depends on expire_on_commit=False (app.core.db.make_sessionmaker), so the
    # caller's already-loaded Conversation survives the commit unexpired.
    scoped = agent.scope_collections(collection_ids)
    await db.commit()
    return await hybrid_search(
        db,
        vector_store,
        llm_provider,
        reranker,
        question,
        top_n=settings.retrieval_top_n,
        rrf_k=settings.rrf_k,
        candidate_limit=settings.retrieval_candidate_limit,
        sparse_weight=settings.sparse_weight,
        collection_ids=scoped,
```

- [ ] **Step 6: Modify `backend/app/chat/service.py`** — the prompt name, and the agent on the row

```python
    prompt_name: str = "answer_agent",
) -> ChatAnswer:
    """Deliberately knows nothing about where `evidence` came from: no session, no
    vector store, no reranker. That is the whole point of the split - Slice 3 runs
    an execution plan over RAG and MCP steps, merges the results into one
    list[Evidence], and calls this function unchanged.

    `prompt_name` is Slice 4's agent, and it is a defaulted keyword rather than a
    new collaborator: an agent picks WHICH stored prompt answers, so this stays
    one `get_prompt` call and the signature that
    tests/test_chat_service.py pins - no session, no retrieval collaborator -
    is unchanged. The default is the name every caller used before agents
    existed, which is why the default agent is not a second code path."""
    template = await get_prompt(prompt_name)
```


```python
        # NULL for the default agent, which is the app behaving as it always
        # did. Written beside `model` for the same reason: it survives a reload,
        # so a transcript can still say what answered it.
        agent_name=agent_name,
```

- [ ] **Step 7: Modify `backend/app/mcp/service.py`** — the manual half of the same boundary

Fencing the planner while leaving the composer's own tool picker open would fence the machine and not the human, which is the wrong way round.

```python
async def load_tool_calls(
    db: AsyncSession,
    requested: list[tuple[uuid.UUID, dict]],
    agent: ResolvedAgent = DEFAULT_AGENT,
) -> list[PendingToolCall]:
    """Resolve tool ids to callable targets, or refuse.

    Called BEFORE the conversation is created, for the same reason
    `load_claimable` is: a bad tool id must not leave a titled, empty
    conversation in the sidebar, and once a StreamingResponse has begun there is
    no status line left to set - a 404 would degrade into an error frame inside
    a 200.

    THE AGENT CHECK IS HERE, not in the router, because this is the manual half
    of the same boundary `load_available` keeps for the planner. Restricting an
    agent to read-only tools would mean nothing if the user could pick a
    `destructive` one out of the composer's own tool picker on the very same
    turn - the planner would be fenced and the human would not be, which is the
    wrong way round. DEFAULT_AGENT allows everything, so the pre-agent behaviour
    is unchanged.
    """
    if not requested:
        return []
    ids = [tool_id for tool_id, _ in requested]
    rows = (
        await db.execute(
            select(McpTool, McpServer).join(McpServer, McpServer.id == McpTool.server_id).where(
                McpTool.id.in_(ids)
            )
        )
    ).all()
    by_id = {tool.id: (tool, server) for tool, server in rows}

    calls: list[PendingToolCall] = []
    for tool_id, arguments in requested:
        found = by_id.get(tool_id)
        if found is None:
            raise HTTPException(status_code=404, detail=TOOL_NOT_FOUND_MESSAGE)
        tool, server = found
        if not tool.enabled or not server.enabled:
            # 409, not the 404 above: the row exists and an admin turned it off,
            # so there is nothing to conceal - only a state to explain.
            raise HTTPException(status_code=409, detail=TOOL_UNAVAILABLE_MESSAGE)
        if not agent.allows_tool(tool.id):
            # 403, not the 409 above: the tool is fine and enabled, the CALLER is
            # not allowed to reach it through this agent. Checked before the
            # risk_level rule so the message names the real reason.
            raise HTTPException(status_code=403, detail=TOOL_NOT_ALLOWED_MESSAGE)
        if tool.risk_level == "destructive":
            raise HTTPException(status_code=400, detail=DESTRUCTIVE_MESSAGE)
```

---

### Task 3: The API — admin CRUD, the picker's list, and a way to make a prompt

**Files:**
- Create: `backend/app/schemas/agent.py`
- Create: `backend/app/agents/router.py`
- Modify: `backend/app/schemas/prompt.py`
- Modify: `backend/app/prompts/router.py`
- Modify: `backend/app/schemas/chat.py`
- Modify: `backend/app/schemas/observability.py`
- Modify: `backend/app/observability/router.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `GET/POST /api/agents`, `PATCH/DELETE /api/agents/{id}`, `GET /api/agents/selectable`, `POST /api/prompts`, `ChatRequest.agent_id`, `MessageResponse.agent_name`, `TraceResponse.agent_name`.
- Consumed by: the composer's agent picker, the 에이전트 관리 screen and the trace dialog (Task 4).

- [ ] **Step 1: Write `backend/app/schemas/agent.py`**

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AgentCollectionRef(BaseModel):
    id: uuid.UUID
    name: str


class AgentToolRef(BaseModel):
    """A tool an agent carries, named the way the planner and the citations name
    it: `server/tool`. `risk_level` rides along because it is the one property an
    admin composing a read-only agent is actually choosing on."""

    id: uuid.UUID
    server_name: str
    name: str
    risk_level: str


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # A NAME from the prompt store. Checked against `prompts` in the router
    # rather than with a Literal here: the set of prompt names is data an admin
    # adds to, and a Literal would freeze it at import time.
    prompt_name: str = Field(default="answer_agent", min_length=1, max_length=100)
    # None means the deployment's ANSWER_MODEL. Validated against
    # Settings.selectable_models in the router, for the same reason
    # ChatRequest.model is: it is operator configuration, and a Field(pattern=...)
    # would freeze it at import time and answer in English.
    answer_model: str | None = Field(default=None, max_length=100)
    orchestrator: bool = False
    enabled: bool = True
    # EMPTY MEANS UNRESTRICTED, for both, and the screen says 전체 허용 rather
    # than 없음 beside an empty selection. See app/models/agent.py.
    collection_ids: list[uuid.UUID] = Field(default_factory=list)
    tool_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _stripped(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("에이전트 이름을 입력해 주세요.")
        return stripped

    @field_validator("description", "answer_model")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AgentUpdate(BaseModel):
    """PATCH semantics: an OMITTED field is left alone.

    The two lists are the exception that proves it - sending `collection_ids: []`
    means "unrestricted", which is a real state an admin has to be able to get
    back to, so they are replaced wholesale when present and untouched when
    absent."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    prompt_name: str | None = Field(default=None, min_length=1, max_length=100)
    answer_model: str | None = Field(default=None, max_length=100)
    orchestrator: bool | None = None
    enabled: bool | None = None
    collection_ids: list[uuid.UUID] | None = None
    tool_ids: list[uuid.UUID] | None = None

    @field_validator("name")
    @classmethod
    def _stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("에이전트 이름을 입력해 주세요.")
        return stripped

    @field_validator("description", "answer_model")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AgentResponse(BaseModel):
    """The admin screen's row. Admin only, because it names the collections and
    the tools an agent may reach and that is the configuration itself."""

    id: uuid.UUID
    name: str
    description: str | None
    prompt_name: str
    answer_model: str | None
    orchestrator: bool
    enabled: bool
    collections: list[AgentCollectionRef] = []
    tools: list[AgentToolRef] = []
    created_by_email: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentOption(BaseModel):
    """GET /api/agents/selectable - what the composer's agent picker lists.

    Deliberately narrower than AgentResponse and deliberately readable by any
    authenticated user, exactly as GET /api/models and GET /api/mcp/tools are:
    it lists only what POST /api/chat would accept, so it discloses nothing a
    user could not learn by picking an agent and being answered. It carries no
    collection list and no tool list - those are the boundary, and enumerating a
    boundary is how you tell someone what to try next.
    """

    id: uuid.UUID
    name: str
    description: str | None
    # Shown so the composer can move its own model picker to the agent's model
    # when one is chosen. Null means "the deployment default", which is what the
    # picker already shows as 기본.
    answer_model: str | None
    # Shown because the composer's 슈퍼 에이전트 toggle is forced on for an agent
    # that carries it, and a control that ignores a click without saying why is
    # a bug report.
    orchestrator: bool
```

- [ ] **Step 2: Write `backend/app/agents/router.py`**

Admin for everything except `/selectable`, which any authenticated user may read for the same reason `GET /api/models` is readable: it returns exactly what `POST /api/chat` accepts, so it discloses nothing a user could not learn by picking an agent and being answered. It carries neither list — a boundary is not an inventory to publish.

The PATCH semantics are worth reading. `model_fields_set`, not `is not None`: the row's 중지 button sends `{"enabled": false}` and nothing else, and an is-not-None read of a NULLABLE field cannot tell that from an explicit null — so pausing an agent silently cleared the model an admin had chosen for it. Found by driving it.

```python
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.chat.prompt import _FALLBACK_PROMPTS
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.models.agent import Agent
from app.models.collection import Collection
from app.models.mcp import McpServer, McpTool
from app.models.prompt import Prompt
from app.models.user import User
from app.schemas.agent import (
    AgentCollectionRef,
    AgentCreate,
    AgentOption,
    AgentResponse,
    AgentToolRef,
    AgentUpdate,
)

logger = logging.getLogger("mopan.agents")
router = APIRouter(prefix="/api/agents", tags=["agents"])

AGENT_NOT_FOUND_MESSAGE = "에이전트를 찾을 수 없습니다."
DUPLICATE_NAME_MESSAGE = "같은 이름의 에이전트가 이미 있습니다."
UNKNOWN_PROMPT_MESSAGE = "등록되지 않은 프롬프트입니다: {name}"
UNKNOWN_MODEL_MESSAGE = "사용할 수 없는 답변 모델입니다: {name}"
UNKNOWN_COLLECTION_MESSAGE = "등록되지 않은 분류가 포함되어 있습니다."
UNKNOWN_TOOL_MESSAGE = "등록되지 않은 MCP 도구가 포함되어 있습니다."


async def _server_names(db: AsyncSession) -> dict[uuid.UUID, str]:
    return dict((await db.execute(select(McpServer.id, McpServer.name))).all())


def _response(agent: Agent, email: str | None, servers: dict[uuid.UUID, str]) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        prompt_name=agent.prompt_name,
        answer_model=agent.answer_model,
        orchestrator=agent.orchestrator,
        enabled=agent.enabled,
        collections=[AgentCollectionRef(id=c.id, name=c.name) for c in agent.collections],
        tools=[
            AgentToolRef(
                id=t.id,
                # The id, not a join: a tool whose server row vanished would be a
                # foreign key violation, so this only falls back for a session
                # that has not loaded the map.
                server_name=servers.get(t.server_id, ""),
                name=t.name,
                risk_level=t.risk_level,
            )
            for t in agent.tools
        ],
        created_by_email=email,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


async def _validate_prompt(db: AsyncSession, name: str) -> None:
    """A prompt an agent names has to exist, or the first question it answers
    dies inside the stream where nothing can explain it.

    `get_prompt` falls back to the module constant, so the built-in names are
    valid even before migration 0004/0007 has seeded them - which is also what
    keeps this check honest on a database whose `prompts` table is empty.
    """
    if name in _FALLBACK_PROMPTS:
        return
    exists = await db.scalar(select(Prompt.id).where(Prompt.name == name).limit(1))
    if exists is None:
        raise HTTPException(status_code=400, detail=UNKNOWN_PROMPT_MESSAGE.format(name=name[:100]))


def _validate_model(model: str | None, settings: Settings) -> None:
    """The SAME allowlist POST /api/chat enforces. Checked here as well as there
    because a Korean sentence on the form an admin is filling in is worth more
    than a refusal on somebody else's question three days later - and checked
    THERE as well as here because an operator can drop a model from ANSWER_MODELS
    long after this row was saved."""
    if model is not None and model not in settings.selectable_models:
        raise HTTPException(status_code=400, detail=UNKNOWN_MODEL_MESSAGE.format(name=model[:100]))


async def _load_collections(db: AsyncSession, ids: list[uuid.UUID]) -> list[Collection]:
    if not ids:
        return []
    rows = list((await db.scalars(select(Collection).where(Collection.id.in_(ids)))).all())
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=400, detail=UNKNOWN_COLLECTION_MESSAGE)
    return rows


async def _load_tools(db: AsyncSession, ids: list[uuid.UUID]) -> list[McpTool]:
    if not ids:
        return []
    rows = list((await db.scalars(select(McpTool).where(McpTool.id.in_(ids)))).all())
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=400, detail=UNKNOWN_TOOL_MESSAGE)
    return rows


async def _get(db: AsyncSession, agent_id: uuid.UUID) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=AGENT_NOT_FOUND_MESSAGE)
    return agent


@router.get("/selectable", response_model=list[AgentOption])
async def list_selectable_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """What the composer's agent picker lists: ENABLED agents only.

    Any authenticated user, unlike every other route in this module. Picking an
    agent is not an administrative act - it is the same kind of choice as
    picking a model - and this returns exactly what POST /api/chat will accept,
    so a disabled agent is not merely un-runnable, it is unlistable and
    unnameable. The refusal at the other end (409) exists for the race, not for
    the UI.

    Declared BEFORE /{agent_id}: FastAPI matches routes in order, and
    "selectable" would otherwise be parsed as a uuid path parameter and 422.
    """
    rows = (await db.scalars(select(Agent).where(Agent.enabled.is_(True)).order_by(Agent.name))).all()
    return [
        AgentOption(
            id=agent.id,
            name=agent.name,
            description=agent.description,
            answer_model=agent.answer_model,
            orchestrator=agent.orchestrator,
        )
        for agent in rows
    ]


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    agents = (await db.scalars(select(Agent).order_by(Agent.name))).all()
    emails = dict((await db.execute(select(User.id, User.email))).all())
    servers = await _server_names(db)
    return [_response(agent, emails.get(agent.created_by), servers) for agent in agents]


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    payload: AgentCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """Admin only, because an agent is configuration every user then answers
    through: its prompt, its corpus scope and its tool list are exactly the three
    things Slice 1 put behind `require_admin` in the first place."""
    await _validate_prompt(db, payload.prompt_name)
    _validate_model(payload.answer_model, settings)
    collections = await _load_collections(db, payload.collection_ids)
    tools = await _load_tools(db, payload.tool_ids)

    agent = Agent(
        name=payload.name,
        description=payload.description,
        prompt_name=payload.prompt_name,
        answer_model=payload.answer_model,
        orchestrator=payload.orchestrator,
        enabled=payload.enabled,
        created_by=admin.id,
    )
    agent.collections = collections
    agent.tools = tools
    db.add(agent)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc

    log_event(
        logger,
        "agent_created",
        agent_id=str(agent.id),
        agent=agent.name,
        prompt_name=agent.prompt_name,
        collections=len(collections),
        tools=len(tools),
        admin_id=str(admin.id),
    )
    return _response(agent, admin.email, await _server_names(db))


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    payload: AgentUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    agent = await _get(db, agent_id)
    # OMITTED and NULL are different here, and telling them apart needs
    # `model_fields_set` rather than an `is not None` check. `description` and
    # `answer_model` are both nullable, so "clear it" is a state an admin has to
    # be able to reach - and the row's own 중지/사용 button sends nothing but
    # `enabled`, which under an is-not-None reading of a nullable field would
    # have silently wiped the model every time somebody paused an agent. Found
    # by driving it, not by reading it.
    fields = payload.model_fields_set
    if "prompt_name" in fields and payload.prompt_name is not None:
        await _validate_prompt(db, payload.prompt_name)
        agent.prompt_name = payload.prompt_name
    if "name" in fields and payload.name is not None:
        agent.name = payload.name
    if "description" in fields:
        agent.description = payload.description
    if "answer_model" in fields:
        # NULL is "use the deployment default", which is always allowed; only a
        # named model is checked against the allowlist.
        _validate_model(payload.answer_model, settings)
        agent.answer_model = payload.answer_model
    if payload.orchestrator is not None:
        agent.orchestrator = payload.orchestrator
    if payload.enabled is not None:
        agent.enabled = payload.enabled
    if payload.collection_ids is not None:
        agent.collections = await _load_collections(db, payload.collection_ids)
    if payload.tool_ids is not None:
        agent.tools = await _load_tools(db, payload.tool_ids)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc
    await db.refresh(agent)
    log_event(logger, "agent_updated", agent_id=str(agent.id), admin_id=str(admin.id))
    email = await db.scalar(select(User.email).where(User.id == agent.created_by))
    return _response(agent, email, await _server_names(db))


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """The join rows cascade; the MESSAGES DO NOT.

    `messages.agent_name` is a string, not a foreign key, precisely so this
    statement cannot reach a transcript. An admin retiring an agent must not be
    able to delete - or orphan - answers other people are still reading, and
    "which agent said this" has to stay answerable afterwards.
    """
    agent = await _get(db, agent_id)
    await db.delete(agent)
    await db.commit()
    log_event(logger, "agent_deleted", agent_id=str(agent_id), admin_id=str(admin.id))
```

- [ ] **Step 3: Modify `backend/app/schemas/prompt.py`** — a body for a new prompt NAME

```python
class PromptCreate(BaseModel):
    """Body of POST /api/prompts - a NEW prompt name, at version 1.

    Slice 4's agents are what made this necessary. An agent picks a prompt from
    the store, and until now the store had exactly the two names the migrations
    seeded and no way to add a third: an agent could only ever answer with the
    deployment's own system prompt, which is the field the whole feature is
    about. `POST /api/prompts/{name}/versions` deliberately 404s on an unknown
    name - a typo must not silently fork the answer prompt - so creating one is
    its own endpoint rather than a relaxation of that rule.

    The name is a KEY, not a label: `messages.prompt_name` records it, the agents
    table references it, and `get_prompt` looks it up. So it is constrained to
    the shape the two built-in names already have rather than left free-form;
    the human-readable part is the agent's own name.
    """

    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
```

- [ ] **Step 4: Modify `backend/app/prompts/router.py`** — `POST /api/prompts`

```python
@router.post("/prompts", response_model=PromptVersionResponse, status_code=201)
async def create_prompt(
    payload: PromptCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """A NEW prompt name at version 1, active immediately.

    Separate from POST /prompts/{name}/versions on purpose: that route 404s on an
    unknown name so a typo cannot silently fork the answer prompt, and this one
    409s on a name that already exists so it cannot silently overwrite one.
    Between them there is no way to create a prompt by accident.
    """
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail=EMPTY_PROMPT_MESSAGE)
    existing = await db.scalar(select(Prompt.id).where(Prompt.name == payload.name).limit(1))
    if existing is not None:
        raise HTTPException(status_code=409, detail=DUPLICATE_PROMPT_MESSAGE)

    prompt = Prompt(
        name=payload.name, version="1", text=payload.text, is_active=True, created_by=admin.id
    )
    db.add(prompt)
    try:
        await db.commit()
    except IntegrityError as exc:
        # The uniqueness check above loses a race; uq_prompts_name_active is the
        # rule, so the loser gets the same 409 rather than a 500.
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_PROMPT_MESSAGE) from exc
    log_event(
        logger,
        "prompt_created",
        prompt_name=prompt.name,
        admin_id=str(admin.id),
        chars=len(payload.text),
    )
    return _to_version_response(prompt, admin.email)
```

- [ ] **Step 5: Modify `backend/app/schemas/chat.py`** — the request field and the transcript field

```python
    # Slice 4's agent, chosen per question the way `model` is. None is the
    # DEFAULT AGENT - no prompt override, no restriction, orchestrator off -
    # which is this app exactly as it behaved before agents existed, so an empty
    # `agents` table changes nothing about this request.
    #
    # It does not merely supply defaults for the fields above it: the agent's
    # collection and tool lists are a BOUNDARY, so `collection_ids` and
    # `tool_calls` are narrowed and refused against it server-side
    # (app/agents/service.py). Resolved in the router before the response starts,
    # like the model and the attachment ids, for the reason they are: a refusal
    # after a StreamingResponse has begun is an error frame inside a 200.
    agent_id: uuid.UUID | None = None
```


```python
    # WHICH AGENT ANSWERED. Null on every user turn, on every answer written
    # before agents existed, and on every answer the default agent gave - all
    # three of which the transcript renders the same way, because they are the
    # same thing: the app answering as it always did.
    agent_name: str | None = None
```

- [ ] **Step 6: Modify `backend/app/schemas/observability.py` and `backend/app/observability/router.py`** — the trace carries it

```python
    # WHICH AGENT ANSWERED. Null for the default agent and for every answer
    # written before agents existed, which the screen renders as 기본 - the same
    # sentence, because they are the same fact.
    agent_name: str | None = None
```


```python
        agent_name=message.agent_name,
```

- [ ] **Step 7: Modify `backend/app/main.py`** — register the router

```python
    from app.agents.router import router as agents_router
    from app.attachments.router import router as attachments_router
```

#### The chat router — resolve first, then everything else against it

- [ ] **Step 8: Modify `backend/app/chat/router.py`** — the pre-flight block

The agent FIRST, because the model, the collection scope, the tool ids and the catalogue are all resolved against it, and all of it before the conversation row exists.

```python
    # THE AGENT FIRST, because everything below is resolved against it. A missing
    # id is a 404 and a disabled one a 409, both before the conversation exists -
    # the rule every other pre-flight check in this function follows.
    agent = await load_agent(db, payload.agent_id)

    # The agent supplies the DEFAULT, never the ceiling: the allowlist below is
    # still the only thing that decides what reaches the provider, so a row whose
    # model an operator later dropped from ANSWER_MODELS is refused here exactly
    # as a forged body would be. An explicit `model` in the request still wins,
    # which is what keeps the composer's own picker meaningful when an agent is
    # selected.
    model = payload.model or agent.answer_model or settings.answer_model
    if model not in settings.selectable_models:
        raise HTTPException(status_code=400, detail=f"사용할 수 없는 답변 모델입니다: {model}")

    # THE COLLECTION BOUNDARY, resolved before anything is written. `retrieve`
    # and `load_available` both narrow again on their own - this is not the
    # enforcement, it is the refusal: a question scoped to a collection this
    # agent cannot reach gets a Korean 400 rather than an answer built from
    # nothing, which would read as "the corpus does not say".
    try:
        collection_ids = agent.scope_collections(payload.collection_ids)
    except AgentScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # An agent that carries the orchestrator turns it on; the per-question toggle
    # can still turn it on for an agent that does not. There is deliberately no
    # way to turn it OFF for an agent configured with it - that is the agent's
    # configuration, and the composer shows the toggle forced on and says so.
    use_orchestrator = payload.orchestrator or agent.orchestrator
```

- [ ] **Step 9: Modify `backend/app/chat/router.py`** — the catalogue, narrowed

```python
    # `agent` goes in with it: a tool the agent does not carry never enters the
    # catalogue, so a plan naming it cannot be validated and is refused WHOLE
    # rather than filtered - the same treatment a hallucinated name gets.
    resources = await load_available(db, collection_ids, agent) if use_orchestrator else None
```

- [ ] **Step 10: Modify `backend/app/chat/router.py`** — the fallback path is inside the boundary too

A refused or empty plan lands on the direct RAG path, and an agent restricted to one collection must not answer from the whole corpus because its plan was thrown away.

```python
                # THE FALLBACK IS INSIDE THE BOUNDARY TOO. This is the path a
                # refused or empty plan lands on, and an agent restricted to one
                # collection whose plan was thrown away must not answer from the
                # whole corpus instead. `retrieve` narrows again itself; passing
                # the agent here is what makes that narrowing reachable.
                agent=agent,
```

- [ ] **Step 11: Modify `backend/app/chat/router.py`** — the prompt, the row and the frame

```python
            agent_name=agent.name,
        )

    yield _sse({"type": "citations", "citations": chat_answer.citations})
    yield _sse(
        {
            "type": "done",
            "conversation_id": str(conversation.id),
            "message_id": str(assistant_message_id),
            "content": chat_answer.content,
            "citations": chat_answer.citations,
            "model": chat_answer.model,
            # Null for the default agent. Carried on the frame so the answer on
            # screen says what produced it without waiting for a reload, exactly
            # as `model` is.
            "agent_name": agent.name,
```

- [ ] **Step 12: Modify `backend/app/chat/router.py`** — the resume re-loads the agent

Same rule the stored plan already followed: what is stored is a name, and the resume re-resolves it. An agent an admin disabled while the user was deciding refuses the resumed plan exactly as it would refuse a fresh one.

```python
    stored_agent_id = stored.get("agent_id")
    agent = await load_agent(db, uuid.UUID(stored_agent_id) if stored_agent_id else None)
    collection_ids = [uuid.UUID(c) for c in stored.get("collection_ids") or []] or None
    attachment_ids = [uuid.UUID(a) for a in stored.get("attachment_ids") or []]
    attachments = await load_claimable(db, attachment_ids, user)
    images = await to_image_urls(attachments)
    attachment_evidence = to_evidence(attachments)

    # RE-VALIDATED, not trusted across the pause. An admin may have disabled the
    # tool or the whole server while the user was deciding, and a plan that names
    # it must then be refused the way a fresh one would be - which is exactly what
    # load_available + validate_plan already do, with no second rule to keep in
    # step.
    try:
        resources = await load_available(db, collection_ids, agent)
    except AgentScopeError as exc:
        # The agent's collections were trimmed under the pause and no longer
        # cover the scope this question was asked with. Same 409 the refused plan
        # gets below, and for the same reason: the request was fine, the world
        # changed.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        execution_plan = validate_plan(stored.get("plan"), resources, settings=settings)
```

---

### Task 4: The screens

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/api.ts`
- Create: `frontend/components/chat/AgentPicker.tsx`
- Modify: `frontend/components/chat/Composer.tsx`
- Modify: `frontend/components/chat/ChatWindow.tsx`
- Modify: `frontend/components/chat/MessageBubble.tsx`
- Modify: `frontend/components/chat/TraceDialog.tsx`
- Modify: `frontend/components/layout/Sidebar.tsx`
- Create: `frontend/app/(app)/agents/page.tsx`
- Modify: `frontend/app/(app)/prompts/page.tsx`

**Interfaces:**
- Consumes: `GET /api/agents`, `GET /api/agents/selectable`, `POST /api/prompts`, `ChatRequest.agent_id`.

- [ ] **Step 1: Modify `frontend/lib/types.ts`** — the two response shapes

```typescript
/** GET /api/agents - the admin screen's row. Admin only, because the two lists
 * ARE the boundary and enumerating a boundary tells somebody what to try. */
export interface Agent {
  id: string;
  name: string;
  description: string | null;
  /** A name from the prompt store, never the text: the store owns versioning
   * and attribution, and an agent carrying its own copy would fork it out. */
  prompt_name: string;
  /** Null means the deployment's own ANSWER_MODEL. */
  answer_model: string | null;
  orchestrator: boolean;
  enabled: boolean;
  /** EMPTY MEANS UNRESTRICTED, for both lists. The screen prints 전체 허용
   * rather than 없음 beside an empty selection - that is the one place this
   * rule could mislead an admin, so it is the one place it is spelled out. */
  collections: { id: string; name: string }[];
  tools: { id: string; server_name: string; name: string; risk_level: McpRiskLevel }[];
  created_by_email: string | null;
  created_at: string;
  updated_at: string;
}

/** GET /api/agents/selectable - what the composer's picker lists. ENABLED
 * agents only, readable by any authenticated user, and deliberately carrying
 * neither list: the boundary is not an inventory to publish. */
export interface AgentOption {
  id: string;
  name: string;
  description: string | null;
  answer_model: string | null;
```

- [ ] **Step 2: Modify `frontend/lib/api.ts`** — the request field

```typescript
    /** One of GET /api/agents/selectable. Omitted is the DEFAULT AGENT - no
     * prompt override, no restriction, orchestrator off - which is this app
     * exactly as it behaved before agents existed.
     *
     * It does not merely supply defaults for the fields above: the agent's
     * collection and tool lists are a permission boundary, so `collection_ids`
     * and `tool_calls` are narrowed and refused against it server-side. An
     * unknown id (404), a disabled agent (409), a collection the agent cannot
     * reach (400) and a tool it does not carry (403) are all Korean refusals
     * BEFORE the conversation is created, so they arrive here as a rejected
     * fetch rather than an error frame inside a 200. */
    agent_id?: string;
```

- [ ] **Step 3: Write `frontend/components/chat/AgentPicker.tsx`**

The same native `<dialog>` `ModelPicker.tsx` is, and deliberately not a shared abstraction of it: the two differ in their list, and an options-and-slots component covering both would be longer than the second copy.

```tsx
"use client";

import { useRef, useState } from "react";
import type { AgentOption } from "@/lib/types";

/** Which AGENT answers the next question - a saved configuration of prompt,
 * corpus scope, tool list, model and orchestrator.
 *
 * The same native <dialog> ModelPicker.tsx is, for the same reasons, and
 * deliberately not a shared abstraction of it: the two differ in their list
 * (this one carries a null "기본" row and a description line), and an
 * options-and-slots component covering both would be longer than the second
 * copy. If a third picker appears, that is the moment to extract one.
 *
 * The one behaviour worth reading twice is the same one ModelPicker documents
 * at length: `change` commits the choice so arrow keys can browse, and `click`
 * with `detail > 0` - a real pointer press - is what closes. */

// Must equal `sm:w-72` below; the anchoring maths needs the number. Wider than
// the model picker's 240 because a row here carries a description line.
const MENU_WIDTH = 288;
const EDGE = 8;

// The default agent has no row in the database, so it has no id. `null` is that
// agent everywhere in this client, and it is what makes "no agents configured"
// and "the 기본 row is selected" the same state rather than two.
export const DEFAULT_AGENT_LABEL = "기본";

export default function AgentPicker({
  agents,
  value,
  onChange,
}: {
  agents: AgentOption[];
  value: string | null;
  onChange: (id: string | null) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  const current = agents.find((a) => a.id === value) ?? null;

  function openPicker() {
    const dialog = dialogRef.current;
    const trigger = triggerRef.current;
    if (!dialog || !trigger) return;
    if (window.matchMedia("(min-width: 640px)").matches) {
      const rect = trigger.getBoundingClientRect();
      const left = Math.min(
        Math.max(EDGE, rect.right - MENU_WIDTH),
        window.innerWidth - MENU_WIDTH - EDGE,
      );
      dialog.style.left = `${left}px`;
      dialog.style.right = "auto";
      dialog.style.top = "auto";
      dialog.style.bottom = `${window.innerHeight - rect.top + EDGE}px`;
    } else {
      dialog.style.cssText = "";
    }
    dialog.showModal();
    setOpen(true);
  }

  // Nothing to choose between when no agent is configured: the 기본 row alone is
  // not a choice, and an empty agents table has to leave the composer exactly as
  // it was before this control existed.
  if (agents.length === 0) return null;

  const rows: (AgentOption | null)[] = [null, ...agents];

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        // A pointer press that moves focus off the textarea dismisses the phone
        // keyboard under the user; the same rule every control in this row keeps.
        onMouseDown={(event) => event.preventDefault()}
        onClick={openPicker}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`에이전트: ${current?.name ?? DEFAULT_AGENT_LABEL}`}
        className={`inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full px-2 text-label transition-colors duration-150 sm:px-3 ${
          current
            ? "bg-primary-container text-on-primary-container"
            : "text-on-surface-variant hover:bg-surface-container-high"
        }`}
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-4 w-4 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <path d="M12 3 4 7v5c0 4.4 3.2 8.2 8 9 4.8-.8 8-4.6 8-9V7l-8-4Z" />
          <path d="m9 12 2 2 4-4" />
        </svg>
        <span aria-hidden="true" className="hidden max-w-[8rem] truncate sm:inline">
          {current?.name ?? DEFAULT_AGENT_LABEL}
        </span>
      </button>

      <dialog
        ref={dialogRef}
        aria-labelledby="agent-picker-title"
        onClose={() => {
          setOpen(false);
          triggerRef.current?.focus();
        }}
        onClick={(event) => {
          if (event.target === dialogRef.current) dialogRef.current.close();
        }}
        className="fixed inset-x-0 bottom-0 top-auto m-0 w-full max-w-none rounded-t-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim sm:w-72 sm:rounded-md sm:shadow-menu sm:backdrop:bg-transparent"
      >
        <fieldset className="border-0 p-2 pb-6 sm:pb-2">
          <legend
            id="agent-picker-title"
            className="px-3 py-2 text-label font-medium text-on-surface-variant"
          >
            에이전트
          </legend>
          {rows.map((agent) => (
            <label
              key={agent?.id ?? "default"}
              className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-3 transition-colors duration-150 hover:bg-surface-container-high has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary sm:py-2"
            >
              <input
                type="radio"
                name="chat-agent"
                value={agent?.id ?? ""}
                checked={(agent?.id ?? null) === value}
                onChange={() => onChange(agent?.id ?? null)}
                onClick={(event) => {
                  if (event.detail > 0) dialogRef.current?.close();
                }}
                onKeyDown={(event) => {
                  if (event.key === " " || event.key === "Enter") dialogRef.current?.close();
                }}
                className="sr-only"
              />
              <span aria-hidden="true" className="h-4 w-4 shrink-0 text-primary">
                {(agent?.id ?? null) === value && (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="m5 13 4 4L19 7" />
                  </svg>
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body">
                  {agent?.name ?? DEFAULT_AGENT_LABEL}
                </span>
                <span className="block truncate text-caption text-on-surface-variant">
                  {agent
                    ? agent.description || "설명 없음"
                    : "이 배포의 기본 설정으로 답변합니다."}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
      </dialog>
    </>
  );
}
```

- [ ] **Step 4: Modify `frontend/components/chat/Composer.tsx`** — the picker, and the forced toggle

```tsx
  /** GET /api/agents/selectable. Empty for a deployment with no agent
   * configured, which is what makes the picker disappear rather than offer a
   * single 기본 row that is not a choice. */
  agents: AgentOption[];
  /** null is the DEFAULT AGENT - this app exactly as it behaved before agents
   * existed. It is a real selection, not "nothing chosen yet". */
  agentId: string | null;
  onAgentChange: (id: string | null) => void;
}) {
  // The agent's own setting, which the server ORs with the per-question toggle.
  const forcedOrchestrator = agents.some((a) => a.id === agentId && a.orchestrator);
```


```tsx
        {/* An agent that carries the orchestrator turns it on server-side, and
            there is deliberately no way to turn it off for one - that is the
            agent's configuration, not a per-question default. So the button is
            shown pressed and DISABLED rather than left clickable: a control
            that silently ignores a click is a bug report, and the title says
            what is deciding instead. */}
        <button
          type="button"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => onOrchestratorChange(!orchestrator)}
          disabled={forcedOrchestrator}
          aria-pressed={orchestrator || forcedOrchestrator}
          aria-label="슈퍼 에이전트"
          title={
            forcedOrchestrator
              ? "선택한 에이전트가 항상 슈퍼 에이전트로 답변합니다."
              : "질문에 맞춰 여러 단계의 검색과 도구 호출을 계획해서 실행합니다."
          }
          className={`inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full px-2 text-label transition-colors duration-150 disabled:cursor-default sm:px-3 ${
            orchestrator || forcedOrchestrator
              ? "bg-primary-container text-on-primary-container"
              : "text-on-surface-variant hover:bg-surface-container-high"
          }`}
        >
          {/* The four-point spark. Plain currentColor, NOT the brand gradient:
              §2 reserves that for the wordmark, the assistant sparkle and the
              streaming indicator, and a button is none of those. */}
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            className="h-4 w-4 shrink-0"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
            <path d="M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z" />
          </svg>
          <span aria-hidden="true" className="hidden sm:inline">
            슈퍼 에이전트
          </span>
        </button>

        <AgentPicker agents={agents} value={agentId} onChange={onAgentChange} />
```

- [ ] **Step 5: Modify `frontend/components/chat/ChatWindow.tsx`** — load, validate, remember, send

Validated against the list rather than trusted, exactly as the stored model is: an agent an admin disabled is absent from `/selectable`, and a stale id would be a 409 on every send.

```tsx
    apiFetch<AgentOption[]>("/api/agents/selectable")
      .then((list) => {
        setAgents(list);
        let stored: string | null = null;
        try {
          stored = localStorage.getItem(AGENT_STORAGE_KEY);
        } catch {
          // Private mode, or site data blocked. Fall through to the default.
        }
        // Validated against the list, never trusted: an agent an admin disabled
        // is absent from it, and a stale id would be a 409 on every send.
        setAgentId(list.some((a) => a.id === stored) ? stored : null);
      })
      .catch(() => setAgents([]));
```


```tsx
  /** Picking an agent also moves the MODEL picker to the agent's model.
   *
   * The server treats the agent's model as a default an explicit `model` still
   * overrides, so leaving the picker where it was would send the old model and
   * silently ignore the agent's - the user would have configured a model on the
   * agent and never seen it used. Moving the visible control is what makes the
   * two agree, and the user can still change it afterwards. */
  function chooseAgent(id: string | null) {
    setAgentId(id);
    const agent = agents.find((a) => a.id === id) ?? null;
    setNotice(`${agent?.name ?? "기본"} 에이전트로 답변합니다.`);
    if (agent?.answer_model && models.some((m) => m.id === agent.answer_model)) {
      setModel(agent.answer_model);
    }
    try {
      if (id) localStorage.setItem(AGENT_STORAGE_KEY, id);
      else localStorage.removeItem(AGENT_STORAGE_KEY);
    } catch {
      // The choice still applies to this session; it just will not survive a
      // reload. Nothing to tell the user about.
    }
  }
```

- [ ] **Step 6: Modify `frontend/components/chat/TraceDialog.tsx`** — nine facts, three columns

```tsx
            {/* THREE columns, not four: this grid holds nine facts now that the
                agent is one of them, and 3 divides 9 while 4 leaves a single
                orphan on the last row. */}
            <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {/* First, because it is the fact the other eight follow from: an
                  agent decides which prompt answered, which corpus was in
                  scope and which model ran. 기본 - not an em dash - because
                  "the default agent answered" is a real answer, not a gap. */}
              <Fact label="답변 에이전트" value={trace.agent_name ?? "기본"} />
              <Fact label="답변 모델" value={trace.model ?? "—"} />
```

- [ ] **Step 7: Modify `frontend/components/chat/MessageBubble.tsx`** — the label on the answer itself

```tsx
          {/* Before the model, because it is the coarser fact: an agent decides
              the prompt, the corpus scope and the tool list, and the model is
              one of the things it decides. Absent for the default agent, which
              is the app answering as it always did and needs no label. */}
          {message.agent_name && (
            <span className="min-w-0 truncate text-caption text-on-surface-variant">
              <span className="sr-only">답변 에이전트 </span>
              {message.agent_name}
            </span>
          )}
```

- [ ] **Step 8: Modify `frontend/components/layout/Sidebar.tsx`** — the link

```tsx
  { href: "/prompts", label: "프롬프트 관리" },
  { href: "/agents", label: "에이전트 관리" },
```

- [ ] **Step 9: Write `frontend/app/(app)/agents/page.tsx`**

The one thing this screen must not get wrong is the empty selection: 전체 허용 beside it, 전체 in the table, never 없음.

```tsx
"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type {
  Agent,
  AnswerModel,
  Collection,
  McpToolOption,
  PromptSummary,
} from "@/lib/types";

/** 에이전트 관리.
 *
 * An agent is a SAVED CONFIGURATION and this screen is the whole of it: a name,
 * a prompt from the store, the collections it may search, the tools it may
 * call, the model that answers, and whether the orchestrator runs. There is no
 * field here that runs code, and there is not meant to be one - the moment an
 * agent needs custom logic it stops being a row and becomes a deployment.
 *
 * THE ONE THING THIS SCREEN MUST NOT GET WRONG is the empty selection. An empty
 * list means UNRESTRICTED, both for collections and for tools, so every empty
 * selection here prints 전체 허용 rather than 없음. An admin who ticks nothing
 * and reads "없음" would believe they had locked an agent down; the server would
 * disagree, and that is precisely the misleading this feature exists not to do.
 *
 * The row shape follows /mcp: an inline expanded editor under the row rather
 * than a modal, because that is what every other admin screen in this app does.
 */

const RISK_LABEL: Record<string, string> = {
  read: "읽기",
  write: "쓰기",
  destructive: "파괴적",
};

function formatDate(value: string): string {
  return new Date(value).toLocaleString();
}

/** The editable half of an agent, as the form holds it. Separate from `Agent`
 * because the form works in id lists while the response carries objects. */
type Draft = {
  name: string;
  description: string;
  prompt_name: string;
  answer_model: string;
  orchestrator: boolean;
  enabled: boolean;
  collection_ids: string[];
  tool_ids: string[];
};

const EMPTY: Draft = {
  name: "",
  description: "",
  prompt_name: "answer_agent",
  answer_model: "",
  orchestrator: false,
  enabled: true,
  collection_ids: [],
  tool_ids: [],
};

function draftOf(agent: Agent): Draft {
  return {
    name: agent.name,
    description: agent.description ?? "",
    prompt_name: agent.prompt_name,
    answer_model: agent.answer_model ?? "",
    orchestrator: agent.orchestrator,
    enabled: agent.enabled,
    collection_ids: agent.collections.map((c) => c.id),
    tool_ids: agent.tools.map((t) => t.id),
  };
}

/** The wire body. The two lists are ALWAYS sent, even empty: an empty list is a
 * real state (unrestricted), so omitting it - which PATCH reads as "leave
 * alone" - would make clearing a restriction impossible. */
function bodyOf(draft: Draft) {
  return {
    name: draft.name,
    description: draft.description.trim() || null,
    prompt_name: draft.prompt_name,
    answer_model: draft.answer_model || null,
    orchestrator: draft.orchestrator,
    enabled: draft.enabled,
    collection_ids: draft.collection_ids,
    tool_ids: draft.tool_ids,
  };
}

function toggled(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

/** One checkbox group. Its empty state is the sentence that keeps this screen
 * honest, which is why it is a parameter and not a shrug. */
function Choices({
  legend,
  help,
  emptyMeans,
  options,
  selected,
  onToggle,
}: {
  legend: string;
  help: string;
  emptyMeans: string;
  options: { id: string; label: string; hint?: string }[];
  selected: string[];
  onToggle: (id: string) => void;
}) {
  return (
    <fieldset className="rounded-sm bg-surface-container p-4">
      <legend className="px-1 text-label font-medium text-on-surface-variant">{legend}</legend>
      <p className="text-caption text-on-surface-variant">{help}</p>
      {options.length === 0 ? (
        <p className="mt-2 text-body text-on-surface-variant">선택할 항목이 없습니다.</p>
      ) : (
        <div className="mt-2 grid gap-1 sm:grid-cols-2">
          {options.map((option) => (
            <label key={option.id} className="flex items-start gap-2 rounded-sm px-1 py-1 text-body">
              <input
                type="checkbox"
                checked={selected.includes(option.id)}
                onChange={() => onToggle(option.id)}
                className="mt-1 h-4 w-4 shrink-0 accent-primary"
              />
              <span className="min-w-0">
                <span className="block truncate text-on-surface">{option.label}</span>
                {option.hint && (
                  <span className="block truncate text-caption text-on-surface-variant">
                    {option.hint}
                  </span>
                )}
              </span>
            </label>
          ))}
        </div>
      )}
      {/* THE SENTENCE. Nothing ticked is "everything allowed", and this is the
          one place an admin can find that out before they rely on it. */}
      <p className="mt-2 text-caption text-primary">
        {selected.length === 0 ? emptyMeans : `${selected.length}개만 허용합니다.`}
      </p>
    </fieldset>
  );
}

export default function AgentsPage() {
  // null is "not loaded yet", not an empty list - the distinction every admin
  // screen here draws so the empty state never flashes. Every endpoint behind
  // this page answers a non-admin with 403 관리자 권한이 필요합니다., which lands
  // in loadError, so there is no client-side role branch.
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [tools, setTools] = useState<McpToolOption[]>([]);
  const [prompts, setPrompts] = useState<PromptSummary[]>([]);
  const [models, setModels] = useState<AnswerModel[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(EMPTY);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Agent | null>(null);

  const load = useCallback(async () => {
    try {
      setAgents(await apiFetch<Agent[]>("/api/agents"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
    // Each of the four is what a field on this form offers, and each failure is
    // survivable on its own - a deployment with no MCP server has no tools to
    // list, and that is a normal state, not an error over the whole page.
    void apiFetch<Collection[]>("/api/collections").then(setCollections).catch(() => setCollections([]));
    void apiFetch<McpToolOption[]>("/api/mcp/tools").then(setTools).catch(() => setTools([]));
    void apiFetch<PromptSummary[]>("/api/prompts").then(setPrompts).catch(() => setPrompts([]));
    void apiFetch<AnswerModel[]>("/api/models").then(setModels).catch(() => setModels([]));
  }, [load]);

  const collectionOptions = collections.map((c) => ({
    id: c.id,
    label: c.name,
    hint: c.description ?? undefined,
  }));
  const toolOptions = tools.map((t) => ({
    id: t.id,
    label: `${t.server_name}/${t.name}`,
    hint: `위험도 ${RISK_LABEL[t.risk_level] ?? t.risk_level}`,
  }));

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await apiFetch<Agent>("/api/agents", { method: "POST", body: JSON.stringify(bodyOf(draft)) });
      setDraft(EMPTY);
      await load();
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  /** Per-row mutation: busy id, row-scoped error, refetch. Errors render beside
   * the control that caused them and are never hoisted to the page banner. */
  async function act(id: string, run: () => Promise<unknown>) {
    setBusyId(id);
    setRowError(null);
    try {
      await run();
      await load();
      return true;
    } catch (err) {
      setRowError({ id, message: errorMessage(err) });
      return false;
    } finally {
      setBusyId(null);
    }
  }

  function editor(value: Draft, onChange: (next: Draft) => void, idPrefix: string) {
    return (
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor={`${idPrefix}-name`} className="text-label font-medium text-on-surface-variant">
              이름
            </label>
            <input
              id={`${idPrefix}-name`}
              value={value.name}
              onChange={(e) => onChange({ ...value, name: e.target.value })}
              required
              maxLength={200}
              placeholder="현장 안전 담당"
              className="field mt-1 w-full"
            />
          </div>
          <div>
            <label
              htmlFor={`${idPrefix}-description`}
              className="text-label font-medium text-on-surface-variant"
            >
              설명
            </label>
            <input
              id={`${idPrefix}-description`}
              value={value.description}
              onChange={(e) => onChange({ ...value, description: e.target.value })}
              maxLength={2000}
              placeholder="사용자가 채팅에서 고를 때 보이는 한 줄 설명"
              className="field mt-1 w-full"
            />
          </div>
          <div>
            <label
              htmlFor={`${idPrefix}-prompt`}
              className="text-label font-medium text-on-surface-variant"
            >
              답변 지침
            </label>
            <select
              id={`${idPrefix}-prompt`}
              value={value.prompt_name}
              onChange={(e) => onChange({ ...value, prompt_name: e.target.value })}
              className="field mt-1 w-full"
            >
              {/* The current value is always an option even if GET /api/prompts
                  failed, or the row would silently reset to the first entry on
                  the next save. */}
              {(prompts.some((p) => p.name === value.prompt_name)
                ? prompts.map((p) => p.name)
                : [value.prompt_name, ...prompts.map((p) => p.name)]
              ).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <p className="mt-1 text-caption text-on-surface-variant">
              프롬프트 관리에서 만든 이름입니다. 내용을 고치면 이 에이전트의 답변도 바로 바뀝니다.
            </p>
          </div>
          <div>
            <label
              htmlFor={`${idPrefix}-model`}
              className="text-label font-medium text-on-surface-variant"
            >
              답변 모델
            </label>
            <select
              id={`${idPrefix}-model`}
              value={value.answer_model}
              onChange={(e) => onChange({ ...value, answer_model: e.target.value })}
              className="field mt-1 w-full"
            >
              <option value="">기본값 사용</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
            <p className="mt-1 text-caption text-on-surface-variant">
              채팅에서 모델을 직접 고르면 그쪽이 우선합니다.
            </p>
          </div>
        </div>

        <Choices
          legend="사용할 분류"
          help="이 에이전트가 검색할 수 있는 문서 분류입니다. 계획을 세울 때도 여기 없는 분류는 쓸 수 없습니다."
          emptyMeans="선택하지 않았으므로 전체 분류를 허용합니다."
          options={collectionOptions}
          selected={value.collection_ids}
          onToggle={(id) => onChange({ ...value, collection_ids: toggled(value.collection_ids, id) })}
        />

        <Choices
          legend="사용할 도구"
          help="이 에이전트가 호출할 수 있는 MCP 도구입니다. 목록에 없는 도구를 지정한 실행 계획은 통째로 거부됩니다."
          emptyMeans="선택하지 않았으므로 전체 도구를 허용합니다."
          options={toolOptions}
          selected={value.tool_ids}
          onToggle={(id) => onChange({ ...value, tool_ids: toggled(value.tool_ids, id) })}
        />

        <div className="flex flex-wrap gap-4">
          <label className="flex items-center gap-2 text-body">
            <input
              type="checkbox"
              checked={value.orchestrator}
              onChange={(e) => onChange({ ...value, orchestrator: e.target.checked })}
              className="h-4 w-4 accent-primary"
            />
            슈퍼 에이전트로 답변
          </label>
          <label className="flex items-center gap-2 text-body">
            <input
              type="checkbox"
              checked={value.enabled}
              onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
              className="h-4 w-4 accent-primary"
            />
            사용
          </label>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">에이전트 관리</h1>
      <ErrorBanner message={loadError} />

      <form onSubmit={handleCreate} className="space-y-3 rounded-md bg-surface-container-low p-6">
        <h2 className="text-title font-medium">에이전트 등록</h2>
        {/* Nothing has gone wrong, so this is tone rather than a rule: a
            surface-container-high block, never an ErrorBanner. */}
        <div className="rounded-sm bg-surface-container-high p-4 text-body text-on-surface">
          <p className="font-medium">에이전트는 저장된 설정입니다. 코드가 아닙니다.</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-on-surface-variant">
            <li>
              고른 분류와 도구는 권한 경계입니다. 목록 밖의 도구를 지정한 실행 계획은 일부만 걸러
              내는 것이 아니라 통째로 거부되고, 검색은 목록 밖의 분류에 닿지 않습니다.
            </li>
            <li>아무것도 고르지 않으면 전체를 허용한다는 뜻입니다. 제한은 직접 골라야 걸립니다.</li>
            <li>
              에이전트를 하나도 만들지 않으면 지금까지와 똑같이 동작합니다. 채팅에서 고르지 않은
              경우도 마찬가지입니다.
            </li>
          </ul>
        </div>

        {editor(draft, setDraft, "agent-new")}

        <ErrorBanner message={createError} />
        <div className="flex justify-end">
          <button type="submit" disabled={creating} className="btn-filled">
            {creating ? "등록 중..." : "등록"}
          </button>
        </div>
      </form>

      {agents === null ? (
        !loadError && (
          <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
        )
      ) : agents.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">
          등록된 에이전트가 없습니다. 채팅은 기본 설정으로 동작합니다.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-sm">
          <table className="w-full text-left text-body">
            <caption className="sr-only">등록된 에이전트 목록</caption>
            <thead>
              <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                <th scope="col" className="px-3 py-3">에이전트</th>
                <th scope="col" className="px-3 py-3">분류</th>
                <th scope="col" className="px-3 py-3">도구</th>
                <th scope="col" className="px-3 py-3">모델</th>
                <th scope="col" className="px-3 py-3">상태</th>
                <th scope="col" className="px-3 py-3">관리</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <Fragment key={agent.id}>
                  <tr className="border-b border-outline-variant align-top">
                    <td className="px-3 py-3">
                      <div className="font-medium">{agent.name}</div>
                      <div className="text-caption text-on-surface-variant">
                        {agent.description || "설명 없음"}
                      </div>
                      <div className="text-caption text-on-surface-variant">
                        {agent.prompt_name}
                        {agent.orchestrator && " · 슈퍼 에이전트"}
                      </div>
                    </td>
                    {/* 전체, not 없음. The list is empty because nothing was
                        restricted, and saying 없음 would state the opposite of
                        what the server does. */}
                    <td className="px-3 py-3">
                      {agent.collections.length === 0
                        ? "전체"
                        : agent.collections.map((c) => c.name).join(", ")}
                    </td>
                    <td className="px-3 py-3">
                      {agent.tools.length === 0
                        ? "전체"
                        : agent.tools.map((t) => `${t.server_name}/${t.name}`).join(", ")}
                    </td>
                    <td className="px-3 py-3">{agent.answer_model ?? "기본값"}</td>
                    <td className="px-3 py-3">
                      {agent.enabled ? (
                        <span className="text-primary">사용 중</span>
                      ) : (
                        <span className="text-on-surface-variant">중지</span>
                      )}
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => {
                            const open = editing === agent.id;
                            setEditing(open ? null : agent.id);
                            setRowError(null);
                            if (!open) setEditDraft(draftOf(agent));
                          }}
                          aria-expanded={editing === agent.id}
                          className="btn-tonal btn-compact"
                        >
                          {editing === agent.id ? "닫기" : "편집"}
                        </button>
                        <button
                          type="button"
                          disabled={busyId === agent.id}
                          onClick={() =>
                            void act(agent.id, () =>
                              apiFetch(`/api/agents/${agent.id}`, {
                                method: "PATCH",
                                body: JSON.stringify({ enabled: !agent.enabled }),
                              }),
                            )
                          }
                          className="btn-tonal btn-compact"
                        >
                          {agent.enabled ? "중지" : "사용"}
                        </button>
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(agent)}
                          className="btn-danger btn-compact"
                        >
                          삭제
                        </button>
                      </div>
                      {rowError?.id === agent.id && <ErrorBanner message={rowError.message} />}
                      <div className="mt-1 text-caption text-on-surface-variant">
                        {agent.created_by_email ?? "시스템"} · {formatDate(agent.updated_at)}
                      </div>
                    </td>
                  </tr>
                  {editing === agent.id && (
                    <tr className="border-b border-outline-variant">
                      <td colSpan={6} className="bg-surface-container-low px-3 py-4">
                        <form
                          className="space-y-3"
                          onSubmit={async (event) => {
                            event.preventDefault();
                            const ok = await act(agent.id, () =>
                              apiFetch(`/api/agents/${agent.id}`, {
                                method: "PATCH",
                                body: JSON.stringify(bodyOf(editDraft)),
                              }),
                            );
                            // Closed only on success: a refused save has to leave
                            // the admin's typing where they can fix it.
                            if (ok) setEditing(null);
                          }}
                        >
                          {editor(editDraft, setEditDraft, `agent-${agent.id}`)}
                          <div className="flex justify-end gap-2">
                            <button
                              type="button"
                              onClick={() => setEditing(null)}
                              className="btn-text"
                            >
                              취소
                            </button>
                            <button
                              type="submit"
                              disabled={busyId === agent.id}
                              className="btn-filled"
                            >
                              {busyId === agent.id ? "저장 중..." : "저장"}
                            </button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="에이전트 삭제"
          message={`"${deleteTarget.name}" 에이전트를 삭제합니다. 이 에이전트로 만들어진 지난 답변은 그대로 남고 추적 화면에도 계속 이름이 표시됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={async () => {
            await apiFetch(`/api/agents/${deleteTarget.id}`, { method: "DELETE" });
            await load();
          }}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 10: Modify `frontend/app/(app)/prompts/page.tsx`** — the 새 프롬프트 form

```tsx
  /** A NEW prompt name, at version 1.
   *
   * Added for agents: an agent picks a prompt from this store, and until this
   * existed the store had only the names the migrations seeded - so an agent
   * could never answer with anything but the deployment's own system prompt,
   * which is the field the whole feature is about. */
  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await apiFetch<PromptVersion>("/api/prompts", {
        method: "POST",
        body: JSON.stringify({ name: newName, text: newText }),
      });
      setNewName("");
      setNewText("");
      const current = await load(newName);
      if (current) setDraft(current.text);
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  }
```

---

### Task 5: The tests

**Files:**
- Create: `backend/tests/test_agents.py`
- Modify: `backend/tests/test_chat_service.py`

**Interfaces:**
- Consumes: everything above.

Every guard was broken, run, watched fail, and restored. The list, with what caught each:

| guard | test that fails without it |
|---|---|
| `retrieve()` narrows to the agent's collections | `test_retrieve_restricted_to_one_collection_cannot_reach_another` |
| the router narrows and refuses a disjoint scope | `test_a_question_scoped_outside_the_agent_is_refused_rather_than_emptied` |
| both layers together | `test_an_answer_from_a_restricted_agent_cites_no_evidence_from_outside` |
| `load_available()` hides tools the agent does not carry | `test_a_plan_naming_a_tool_outside_the_agents_list_is_refused_whole` |
| `validate_plan()` writes the catalogue into an unnamed step | `test_a_plan_step_naming_no_collections_cannot_search_outside_the_agent` |
| `load_tool_calls()` refuses a hand-picked forbidden tool | `test_a_hand_picked_tool_outside_the_agents_list_is_refused_too` |
| `require_admin` on create/edit | `test_a_non_admin_cannot_create_or_edit_an_agent` |
| `persist_turn` writes `agent_name` | `test_the_agent_that_answered_survives_a_reload_and_appears_in_the_trace` |
| PATCH tells omitted from null | `test_patching_one_field_leaves_every_other_field_alone` |
| the conversation row is written AFTER every refusal (guard MOVED, not deleted) | `test_an_unknown_agent_id_is_a_404_before_anything_is_written` |

- [ ] **Step 1: Write `backend/tests/test_agents.py`**

```python
"""Slice 4 - agent management.

An agent is a SAVED CONFIGURATION: a name, a prompt from the prompt store, a set
of collections it may search, a set of MCP tools it may call, an answer model,
and whether the orchestrator runs. It is deliberately not code.

The property this whole file is arranged around:

    THE TWO LISTS ARE PERMISSION BOUNDARIES, NOT HINTS. A plan step naming a tool
    the agent does not carry is refused WHOLE - not filtered - and a retrieval
    restricted to the agent's collections cannot reach outside them even when the
    only answer is out there. Enforced in `load_available` and in `retrieve`,
    which is to say in the two functions that decide what a question may reach,
    not in the UI and not in the planner's prompt.

And the property that makes it deployable at all: an EMPTY `agents` table changes
nothing. Every "when there are no agents" test truncates the table in its own
body, because the database is session-scoped and a leftover row from another
module would let such a test pass with its guard removed.

NO TEST HERE MAKES A NETWORK CALL. The LLM is an AsyncMock; there is no MCP
server, only rows describing one.
"""

import json
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.agents.service import DEFAULT_AGENT, AgentScopeError, ResolvedAgent
from app.chat.prompt import ANSWER_SYSTEM_PROMPT
from app.chat.service import retrieve
from app.core.config import Settings
from app.llm.base import ChatResult
from app.models.agent import Agent
from app.models.chunk import EMBEDDING_DIM, Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.mcp import McpServer, McpTool
from app.models.message import Message
from app.models.prompt import Prompt
from app.models.user import User
from app.orchestrator.plan import PlanError, load_available, validate_plan
from app.retrieval.reranker import NoneReranker
from app.retrieval.vector_store import PgVectorStore

PUBLIC_URL = "http://93.184.216.34/mcp"

# The A chunk is about nitrogen fertiliser, the B chunk about a pesticide. They
# share no word, so "다이아지논" reaches B through BOTH the vector ranking (the
# stub embeds every query as B's vector) and the keyword ranking. A restriction
# that only happened to hide one of the two would still show the other.
A_TEXT = "질소 비료는 생육 초기에 나누어 준다"
B_TEXT = "다이아지논 유제는 수확 14일 전까지 살포한다"


def vec(*leading: float) -> list[float]:
    return list(leading) + [0.0] * (EMBEDDING_DIM - len(leading))


def settings_with(**overrides) -> Settings:
    return Settings().model_copy(update=overrides)


def parse_sse(body: str) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")]


def make_fake_llm() -> AsyncMock:
    provider = AsyncMock()
    # Every query embeds to B's vector, so the vector ranking always prefers the
    # collection the agent is NOT allowed to reach. That is the point.
    provider.embed = AsyncMock(return_value=[vec(0.0, 1.0)])
    provider.chat = AsyncMock(
        return_value=ChatResult(content="답변입니다 [1].", usage={"total_tokens": 11}, model="gpt-4o")
    )
    return provider


@pytest.fixture
def fake_llm(app):
    provider = make_fake_llm()
    app.state.llm_provider = provider
    return provider


@pytest_asyncio.fixture
async def seeded_prompt(db):
    """0004's row, re-seeded. Every DB-touching test truncates `users` CASCADE,
    which takes `prompts` with it, so whether answer_agent exists depends on what
    ran before - exactly the note tests/test_prompts_admin.py already carries."""
    await db.execute(text("DELETE FROM prompts"))
    db.add(
        Prompt(
            name="answer_agent", version="1", text=ANSWER_SYSTEM_PROMPT, is_active=True, created_by=None
        )
    )
    await db.commit()


@pytest_asyncio.fixture
async def admin_client(client, fake_llm, seeded_prompt):
    """The first account to register is the bootstrap admin."""
    await client.post("/api/auth/register", json={"email": "agent-admin@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "agent-admin@example.com", "password": "pw123456"})
    return client


@pytest_asyncio.fixture
async def member_client(admin_client, app):
    """A second, non-admin account on its own cookie jar."""
    await admin_client.post(
        "/api/auth/register", json={"email": "agent-member@example.com", "password": "pw123456"}
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post(
            "/api/auth/login", json={"email": "agent-member@example.com", "password": "pw123456"}
        )
        yield ac


@pytest_asyncio.fixture
async def corpus(db, admin_client):
    """Two collections whose contents do not overlap, and one MCP server with a
    read tool and a write tool. Returned as plain ids, detached from the session
    the API's own requests use.

    Depends on `admin_client` rather than creating its own owner, and that is not
    incidental: the first account to REGISTER is the bootstrap admin, so a user
    row inserted here first would silently demote the admin fixture to a member
    in whichever tests happened to resolve this one earlier. Stating the
    dependency makes the order a fact instead of a signature convention."""
    await db.execute(text("TRUNCATE TABLE agents, agent_collections, agent_tools CASCADE"))
    user = await db.scalar(select(User).where(User.role == "admin"))
    collection_a = Collection(name="비료", created_by=user.id)
    collection_b = Collection(name="농약", created_by=user.id)
    db.add_all([collection_a, collection_b])
    await db.flush()

    def _doc(collection, name):
        return Document(
            collection_id=collection.id,
            filename=name,
            file_type="txt",
            size_bytes=1,
            storage_path="x",
            status="indexed",
            uploaded_by=user.id,
        )

    doc_a, doc_b = _doc(collection_a, "비료.pdf"), _doc(collection_b, "농약.pdf")
    db.add_all([doc_a, doc_b])
    await db.flush()
    db.add_all(
        [
            Chunk(
                document_id=doc_a.id,
                chunk_index=0,
                content=A_TEXT,
                token_count=8,
                char_count=len(A_TEXT),
                page=1,
                section=None,
                chunk_metadata={},
                embedding=vec(1.0, 0.0),
            ),
            Chunk(
                document_id=doc_b.id,
                chunk_index=0,
                content=B_TEXT,
                token_count=8,
                char_count=len(B_TEXT),
                page=1,
                section=None,
                chunk_metadata={},
                embedding=vec(0.0, 1.0),
            ),
        ]
    )
    server = McpServer(name="현장", base_url=PUBLIC_URL, created_by=user.id)
    db.add(server)
    await db.flush()
    read_tool = McpTool(server_id=server.id, name="lookup", input_schema={}, risk_level="read")
    write_tool = McpTool(server_id=server.id, name="record", input_schema={}, risk_level="write")
    db.add_all([read_tool, write_tool])
    await db.commit()
    return {
        "collection_a": collection_a.id,
        "collection_b": collection_b.id,
        "read_tool": read_tool.id,
        "write_tool": write_tool.id,
    }


async def create_agent(client, **overrides) -> dict:
    body = {"name": "테스트 에이전트"} | overrides
    response = await client.post("/api/agents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Admin only. Picking one is not.
# ---------------------------------------------------------------------------


async def test_a_non_admin_cannot_create_or_edit_an_agent(admin_client, member_client, corpus):
    """An agent decides which prompt answers and which corpus and tools a question
    may reach. That is the same authority Slice 1 put behind require_admin for
    documents, and for the same reason: it changes every other user's answers."""
    created = await create_agent(admin_client, name="관리자만")

    refused_create = await member_client.post("/api/agents", json={"name": "몰래"})
    assert refused_create.status_code == 403
    assert "관리자" in refused_create.json()["detail"]

    refused_edit = await member_client.patch(
        f"/api/agents/{created['id']}", json={"name": "바꿔치기"}
    )
    assert refused_edit.status_code == 403

    refused_delete = await member_client.delete(f"/api/agents/{created['id']}")
    assert refused_delete.status_code == 403

    refused_list = await member_client.get("/api/agents")
    assert refused_list.status_code == 403

    # And nothing actually changed.
    unchanged = (await admin_client.get("/api/agents")).json()
    assert [a["name"] for a in unchanged] == ["관리자만"]


async def test_any_authenticated_user_may_list_and_pick_a_selectable_agent(
    admin_client, member_client, corpus
):
    """The same argument GET /api/models makes: it returns exactly what
    POST /api/chat accepts, so it discloses nothing a user could not learn by
    picking one and being answered. It carries no collection or tool list -
    enumerating a boundary is how you tell somebody what to try next."""
    await create_agent(admin_client, name="현장 도우미", description="현장 질문용")

    listed = await member_client.get("/api/agents/selectable")
    assert listed.status_code == 200
    assert [a["name"] for a in listed.json()] == ["현장 도우미"]
    assert set(listed.json()[0]) == {"id", "name", "description", "answer_model", "orchestrator"}

    answered = await member_client.post(
        "/api/chat", json={"message": "질문", "agent_id": listed.json()[0]["id"]}
    )
    assert answered.status_code == 200


async def test_a_disabled_agent_can_neither_be_listed_nor_selected(admin_client, corpus):
    """Unlistable AND unnameable, the rule a disabled MCP tool already follows.
    The 409 is for the race - an admin turning it off while somebody is typing -
    not for the UI."""
    created = await create_agent(admin_client, name="중지된 에이전트")
    await admin_client.patch(f"/api/agents/{created['id']}", json={"enabled": False})

    assert (await admin_client.get("/api/agents/selectable")).json() == []
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": created["id"]}
    )
    assert refused.status_code == 409
    assert "중지" in refused.json()["detail"]


async def test_an_unknown_agent_id_is_a_404_before_anything_is_written(admin_client, corpus, db):
    """Resolved before the conversation exists, like the model and the attachment
    ids: a refusal after the StreamingResponse has begun would be an error frame
    inside a 200, and a titled empty conversation would be left in the sidebar."""
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": str(uuid.uuid4())}
    )
    assert refused.status_code == 404
    assert "에이전트" in refused.json()["detail"]
    assert (await db.scalar(text("SELECT count(*) FROM conversations"))) == 0


async def test_the_admin_form_refuses_what_the_chat_would_refuse(admin_client, corpus):
    """Every one of these is a Korean 400 on the form the admin is filling in
    rather than a refusal on somebody else's question three days later."""
    unknown_prompt = await admin_client.post(
        "/api/agents", json={"name": "a", "prompt_name": "없는_프롬프트"}
    )
    assert unknown_prompt.status_code == 400
    assert "프롬프트" in unknown_prompt.json()["detail"]

    unknown_model = await admin_client.post(
        "/api/agents", json={"name": "b", "answer_model": "gpt-9-ultra"}
    )
    assert unknown_model.status_code == 400
    assert "답변 모델" in unknown_model.json()["detail"]

    unknown_collection = await admin_client.post(
        "/api/agents", json={"name": "c", "collection_ids": [str(uuid.uuid4())]}
    )
    assert unknown_collection.status_code == 400

    unknown_tool = await admin_client.post(
        "/api/agents", json={"name": "d", "tool_ids": [str(uuid.uuid4())]}
    )
    assert unknown_tool.status_code == 400

    await create_agent(admin_client, name="중복")
    duplicate = await admin_client.post("/api/agents", json={"name": "중복"})
    assert duplicate.status_code == 409


# ---------------------------------------------------------------------------
# The collection boundary
# ---------------------------------------------------------------------------


async def test_retrieve_restricted_to_one_collection_cannot_reach_another(db, fake_llm, corpus):
    """THE test of this slice, at the level that cannot be bypassed.

    The question is answerable ONLY from 농약 - the stub embeds it as 농약's own
    vector and the words appear nowhere else - and the agent may only see 비료.
    It comes back with 비료's chunk or with nothing, never with 농약's.

    Against `retrieve` directly rather than only through the API, because
    `retrieve` is where the narrowing lives: a caller that forgets to pass a
    scope still gets one.
    """
    restricted = ResolvedAgent(
        id=uuid.uuid4(), name="비료 전용", collection_ids=frozenset({corpus["collection_a"]})
    )
    settings = settings_with(retrieval_top_n=5)

    unrestricted_hits = await retrieve(
        db,
        PgVectorStore(db),
        fake_llm,
        NoneReranker(),
        "다이아지논 살포 기준",
        settings=settings,
        agent=DEFAULT_AGENT,
    )
    # The premise: without the agent the answer IS reachable, so a restricted run
    # that finds nothing is the restriction and not an empty corpus.
    assert any(B_TEXT in hit.content for hit in unrestricted_hits)

    hits = await retrieve(
        db,
        PgVectorStore(db),
        fake_llm,
        NoneReranker(),
        "다이아지논 살포 기준",
        settings=settings,
        agent=restricted,
    )
    assert all(B_TEXT not in hit.content for hit in hits)


async def test_an_answer_from_a_restricted_agent_cites_no_evidence_from_outside(
    admin_client, corpus, db
):
    """The same property end to end, through the SSE path an actual question
    takes, asserted on the TRACE - which records every retrieved item including
    the ones the token budget cut, so a leak cannot hide in the gap between
    "retrieved" and "cited"."""
    agent = await create_agent(
        admin_client, name="비료 담당", collection_ids=[str(corpus["collection_a"])]
    )
    response = await admin_client.post(
        "/api/chat", json={"message": "다이아지논 살포 기준", "agent_id": agent["id"]}
    )
    assert response.status_code == 200
    message_id = parse_sse(response.text)[-1]["message_id"]

    trace = (await admin_client.get(f"/api/messages/{message_id}/trace")).json()
    assert trace["evidence"], "nothing was retrieved at all; the assertion below would be vacuous"
    assert {item["filename"] for item in trace["evidence"]} == {"비료.pdf"}


async def test_a_question_scoped_outside_the_agent_is_refused_rather_than_emptied(
    admin_client, corpus, db
):
    """Refused, not silently narrowed to nothing. An answer built from no evidence
    reads as "the corpus does not say", which is a different and false claim.

    And refused BEFORE the conversation is written, which is the second half of
    the assertion and the half a status code alone would not catch: every check
    in this router runs before the row, so a refusal never leaves a titled empty
    conversation in the sidebar for the user to clean up."""
    agent = await create_agent(
        admin_client, name="비료만", collection_ids=[str(corpus["collection_a"])]
    )
    refused = await admin_client.post(
        "/api/chat",
        json={
            "message": "질문",
            "agent_id": agent["id"],
            "collection_ids": [str(corpus["collection_b"])],
        },
    )
    assert refused.status_code == 400
    assert "분류" in refused.json()["detail"]
    assert (await db.scalar(text("SELECT count(*) FROM conversations"))) == 0


def test_scope_collections_never_widens_and_never_silently_empties():
    """The three cases, stated once so the reasoning is not spread over the API
    tests: unrestricted passes through, unscoped narrows to the agent, and a
    disjoint request raises instead of returning []."""
    a, b = uuid.uuid4(), uuid.uuid4()
    unrestricted = ResolvedAgent()
    assert unrestricted.scope_collections(None) is None
    assert unrestricted.scope_collections([b]) == [b]

    restricted = ResolvedAgent(collection_ids=frozenset({a}))
    assert restricted.scope_collections(None) == [a]
    assert restricted.scope_collections([a, b]) == [a]
    with pytest.raises(AgentScopeError):
        restricted.scope_collections([b])


# ---------------------------------------------------------------------------
# The tool boundary
# ---------------------------------------------------------------------------


async def test_a_plan_naming_a_tool_outside_the_agents_list_is_refused_whole(db, corpus):
    """Refused WHOLE, not filtered down to the steps that were allowed.

    The plan below has a legitimate search step beside the forbidden tool step. A
    filtering executor would run the search and quietly drop the tool call, and
    the user would get a plausible answer with no sign that the plan they paid a
    planner call for was rewritten. A model that named one thing it may not touch
    has told you what its other choices are worth, so the whole plan goes and the
    direct RAG path answers instead.
    """
    limited = ResolvedAgent(id=uuid.uuid4(), name="읽기 전용", tool_ids=frozenset({corpus["read_tool"]}))

    everything = await load_available(db)
    assert {t.tool_name for t in everything.tools} == {"lookup", "record"}

    available = await load_available(db, None, limited)
    # Unnameable, not merely un-runnable: it is absent from the catalogue the
    # planner is shown, so there is no second rule to keep in step.
    assert [t.tool_name for t in available.tools] == ["lookup"]

    plan = {
        "steps": [
            {"id": "s1", "kind": "rag", "query": "비료"},
            {"id": "s2", "kind": "tool", "tool": "현장/record"},
        ]
    }
    with pytest.raises(PlanError) as refused:
        validate_plan(plan, available, settings=settings_with())
    assert "현장/record" in str(refused.value)


async def test_a_hand_picked_tool_outside_the_agents_list_is_refused_too(admin_client, corpus):
    """The manual half of the same boundary. Fencing the planner while leaving the
    composer's own tool picker open would fence the machine and not the human,
    which is the wrong way round."""
    agent = await create_agent(
        admin_client, name="읽기만", tool_ids=[str(corpus["read_tool"])]
    )
    refused = await admin_client.post(
        "/api/chat",
        json={
            "message": "질문",
            "agent_id": agent["id"],
            "tool_calls": [{"tool_id": str(corpus["write_tool"]), "arguments": {}}],
        },
    )
    assert refused.status_code == 403
    assert "도구" in refused.json()["detail"]


async def test_a_plan_step_naming_no_collections_cannot_search_outside_the_agent(db, corpus):
    """The hole that was actually there: `collections: []` meant "everything".

    The planner is entitled to omit the field - "search the whole catalogue" is a
    normal plan - and the executor used to turn the resulting empty tuple back
    into `collection_ids=None`, which is every collection in the DATABASE rather
    than every collection in the catalogue. So an agent's restriction was one
    omitted JSON key away from gone. validate_plan now writes the catalogue out.
    """
    restricted = ResolvedAgent(
        id=uuid.uuid4(), name="비료 전용", collection_ids=frozenset({corpus["collection_a"]})
    )
    available = await load_available(db, None, restricted)
    plan = validate_plan(
        {"steps": [{"id": "s1", "kind": "rag", "query": "다이아지논"}]},
        available,
        settings=settings_with(),
    )
    assert plan.steps[0].collection_ids == (corpus["collection_a"],)
    assert corpus["collection_b"] not in plan.steps[0].collection_ids


# ---------------------------------------------------------------------------
# What answered, and what happens when it is gone
# ---------------------------------------------------------------------------


async def test_the_agent_that_answered_survives_a_reload_and_appears_in_the_trace(
    admin_client, corpus
):
    agent = await create_agent(admin_client, name="기록 대상")
    response = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"]}
    )
    done = parse_sse(response.text)[-1]
    assert done["agent_name"] == "기록 대상"

    conversation_id = done["conversation_id"]
    reloaded = (await admin_client.get(f"/api/conversations/{conversation_id}/messages")).json()
    assistant = [m for m in reloaded if m["role"] == "assistant"][0]
    assert assistant["agent_name"] == "기록 대상"

    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert trace["agent_name"] == "기록 대상"


async def test_deleting_an_agent_does_not_orphan_the_messages_that_name_it(
    admin_client, corpus, db
):
    """`messages.agent_name` is a string, not a foreign key, exactly so this can
    be true. An admin retiring an agent must not be able to delete - or cascade
    away - answers other people are still reading, and "which agent said this"
    has to stay answerable afterwards."""
    agent = await create_agent(admin_client, name="폐기 예정")
    done = parse_sse(
        (await admin_client.post("/api/chat", json={"message": "질문", "agent_id": agent["id"]})).text
    )[-1]

    assert (await admin_client.delete(f"/api/agents/{agent['id']}")).status_code == 204

    reloaded = (
        await admin_client.get(f"/api/conversations/{done['conversation_id']}/messages")
    ).json()
    assistant = [m for m in reloaded if m["role"] == "assistant"][0]
    assert assistant["agent_name"] == "폐기 예정"
    assert assistant["content"]
    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert trace["agent_name"] == "폐기 예정"
    assert (await db.scalar(text("SELECT count(*) FROM agents"))) == 0


async def test_deleting_an_agent_removes_its_join_rows_but_not_the_collection(
    admin_client, corpus, db
):
    agent = await create_agent(
        admin_client,
        name="연결 확인",
        collection_ids=[str(corpus["collection_a"])],
        tool_ids=[str(corpus["read_tool"])],
    )
    await admin_client.delete(f"/api/agents/{agent['id']}")
    assert (await db.scalar(text("SELECT count(*) FROM agent_collections"))) == 0
    assert (await db.scalar(text("SELECT count(*) FROM agent_tools"))) == 0
    # The CASCADE runs from `agents` towards the join rows and stops there. The
    # 비료 collection and the lookup tool are shared resources that other agents,
    # other answers and the documents screen all still point at.
    remaining = set((await db.scalars(select(Collection.name))).all())
    assert {"비료", "농약"} <= remaining
    assert (await db.scalar(text("SELECT count(*) FROM mcp_tools"))) == 2


# ---------------------------------------------------------------------------
# The default agent: an empty table changes nothing
# ---------------------------------------------------------------------------


async def test_an_empty_agents_table_behaves_exactly_as_before(admin_client, corpus, db):
    """The deployment claim, checked rather than asserted.

    TRUNCATED IN THE BODY. The database is session-scoped and `corpus` seeds
    rows, so a version of this test that trusted the fixture ordering would pass
    with every guard in this module removed - the trap tests/conftest.py already
    documents for app_settings.
    """
    await db.execute(text("TRUNCATE TABLE agents CASCADE"))
    # COMMITTED, not merely executed. TRUNCATE takes an ACCESS EXCLUSIVE lock, and
    # this session is not the one the API requests below use: leaving the
    # transaction open makes the very first `select(Agent)` inside the app block
    # on it until the test times out.
    await db.commit()
    assert (await db.scalar(text("SELECT count(*) FROM agents"))) == 0
    assert (await admin_client.get("/api/agents/selectable")).json() == []

    response = await admin_client.post("/api/chat", json={"message": "다이아지논 살포 기준"})
    assert response.status_code == 200
    done = parse_sse(response.text)[-1]
    assert done["agent_name"] is None

    row = await db.scalar(select(Message).where(Message.id == uuid.UUID(done["message_id"])))
    assert row.agent_name is None
    assert row.prompt_name == "answer_agent"
    assert row.model == "gpt-4o"
    # Unrestricted: the whole corpus is still reachable, both collections included.
    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert {item["filename"] for item in trace["evidence"]} == {"비료.pdf", "농약.pdf"}


async def test_the_default_agent_narrows_nothing(db, corpus):
    """DEFAULT_AGENT is not a special case handled somewhere - it is a
    ResolvedAgent whose two sets are empty, and empty means unrestricted."""
    available = await load_available(db, None, DEFAULT_AGENT)
    # A superset: registration creates the 일반 collection, and "unrestricted"
    # means every collection there is, whoever made it.
    assert {"비료", "농약"} <= {c.name for c in available.collections}
    assert {t.tool_name for t in available.tools} == {"lookup", "record"}


# ---------------------------------------------------------------------------
# The configuration an agent actually carries
# ---------------------------------------------------------------------------


async def test_the_agents_model_is_the_default_and_an_explicit_model_still_wins(
    admin_client, corpus, fake_llm, app
):
    """The agent supplies the default, never the ceiling. The composer's own
    picker keeps working when an agent is selected, and the allowlist is still
    the only thing deciding what reaches the provider.

    The conftest pins `answer_models` to [] so a deployment cannot change what the
    suite asserts; a second selectable model is added here because that is exactly
    what this test is about."""
    app.state.settings = app.state.settings.model_copy(update={"answer_models": ["gpt-4o-mini"]})
    agent = await create_agent(admin_client, name="모델 지정", answer_model="gpt-4o-mini")

    await admin_client.post("/api/chat", json={"message": "질문", "agent_id": agent["id"]})
    assert fake_llm.chat.await_args.kwargs["model"] == "gpt-4o-mini"

    await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"], "model": "gpt-4o"}
    )
    assert fake_llm.chat.await_args.kwargs["model"] == "gpt-4o"


async def test_an_agents_model_is_still_checked_against_the_allowlist(admin_client, corpus, app, db):
    """An operator can drop a model from ANSWER_MODELS long after an admin picked
    it. The row must not be able to smuggle it past the gate, so the check on the
    admin form is not the only one."""
    agent = await create_agent(admin_client, name="사라진 모델", answer_model="gpt-4o")
    await db.execute(
        text("UPDATE agents SET answer_model = 'gpt-4o-mini' WHERE id = :id"), {"id": agent["id"]}
    )
    await db.commit()

    app.state.settings = app.state.settings.model_copy(
        update={"answer_model": "gpt-4o", "answer_models": []}
    )
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"]}
    )
    assert refused.status_code == 400
    assert "gpt-4o-mini" in refused.json()["detail"]


async def test_an_agent_can_carry_its_own_prompt_from_the_store(admin_client, corpus, fake_llm, db):
    """"prompt from the prompt store", which is why POST /api/prompts exists: an
    agent that could only ever name the deployment's own system prompt would be
    missing the field the feature is about."""
    created = await admin_client.post(
        "/api/prompts", json={"name": "field_agent", "text": "너는 현장 담당자다. 짧게 답한다."}
    )
    assert created.status_code == 201
    agent = await create_agent(admin_client, name="현장", prompt_name="field_agent")

    response = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"]}
    )
    done = parse_sse(response.text)[-1]
    system_message = fake_llm.chat.await_args.args[0][0]
    assert system_message.role == "system"
    assert "현장 담당자" in system_message.content

    row = await db.scalar(select(Message).where(Message.id == uuid.UUID(done["message_id"])))
    assert row.prompt_name == "field_agent"
    assert row.prompt_version == "1"


async def test_creating_a_prompt_is_admin_only_and_cannot_overwrite_one(
    admin_client, member_client, corpus
):
    refused = await member_client.post("/api/prompts", json={"name": "sneaky", "text": "x"})
    assert refused.status_code == 403

    duplicate = await admin_client.post("/api/prompts", json={"name": "answer_agent", "text": "x"})
    assert duplicate.status_code == 409
    assert "이미" in duplicate.json()["detail"]

    blank = await admin_client.post("/api/prompts", json={"name": "blank_agent", "text": "   "})
    assert blank.status_code == 400


async def test_an_agent_that_carries_the_orchestrator_turns_it_on(admin_client, corpus, fake_llm):
    """The agent's configuration, not a per-question default it can be talked out
    of. The composer shows the toggle forced on and says why."""
    agent = await create_agent(admin_client, name="계획형", orchestrator=True)
    fake_llm.chat = AsyncMock(
        side_effect=[
            ChatResult(content='{"steps": []}', usage={}, model="gpt-4o"),
            ChatResult(content="답변입니다.", usage={"total_tokens": 5}, model="gpt-4o"),
        ]
    )
    response = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"], "orchestrator": False}
    )

    assert response.status_code == 200
    assert "planning" in [e.get("status") for e in parse_sse(response.text)]


async def test_updating_an_agent_replaces_its_lists_and_can_clear_them(admin_client, corpus):
    """An empty list is a real state - it is what "unrestricted" is - so the two
    lists are replaced wholesale when present rather than merged."""
    agent = await create_agent(
        admin_client,
        name="편집 대상",
        collection_ids=[str(corpus["collection_a"])],
        tool_ids=[str(corpus["read_tool"])],
    )
    assert [c["name"] for c in agent["collections"]] == ["비료"]

    swapped = await admin_client.patch(
        f"/api/agents/{agent['id']}", json={"collection_ids": [str(corpus["collection_b"])]}
    )
    assert [c["name"] for c in swapped.json()["collections"]] == ["농약"]
    # Omitted, so untouched.
    assert [t["name"] for t in swapped.json()["tools"]] == ["lookup"]

    cleared = await admin_client.patch(f"/api/agents/{agent['id']}", json={"tool_ids": []})
    assert cleared.json()["tools"] == []


async def test_patching_one_field_leaves_every_other_field_alone(admin_client, corpus, app):
    """FOUND BY DRIVING IT. The row's 중지 button sends `{"enabled": false}` and
    nothing else, and an `is not None` read of a NULLABLE field cannot tell that
    from `{"answer_model": null}` - so pausing an agent silently cleared the
    model an admin had chosen for it. `model_fields_set` is what tells omitted
    from null, and an explicit null still clears, because "back to the
    deployment default" has to be reachable."""
    app.state.settings = app.state.settings.model_copy(update={"answer_models": ["gpt-4o-mini"]})
    agent = await create_agent(
        admin_client,
        name="부분 수정",
        description="설명",
        answer_model="gpt-4o-mini",
        collection_ids=[str(corpus["collection_a"])],
    )

    paused = (await admin_client.patch(f"/api/agents/{agent['id']}", json={"enabled": False})).json()
    assert paused["enabled"] is False
    assert paused["answer_model"] == "gpt-4o-mini"
    assert paused["description"] == "설명"
    assert [c["name"] for c in paused["collections"]] == ["비료"]

    # An EXPLICIT null still clears, or an admin could never get back to the
    # deployment default.
    cleared = (
        await admin_client.patch(f"/api/agents/{agent['id']}", json={"answer_model": None})
    ).json()
    assert cleared["answer_model"] is None


async def test_an_agent_row_names_its_creator_and_its_tools_by_server(admin_client, corpus, db):
    agent = await create_agent(admin_client, name="표시", tool_ids=[str(corpus["read_tool"])])
    assert agent["created_by_email"] == "agent-admin@example.com"
    assert agent["tools"][0]["server_name"] == "현장"
    assert agent["tools"][0]["risk_level"] == "read"
    stored = await db.scalar(select(Agent).where(Agent.id == uuid.UUID(agent["id"])))
    assert stored.prompt_name == "answer_agent"
    assert stored.answer_model is None
```

- [ ] **Step 2: Modify `backend/tests/test_chat_service.py`** — the Slice 3 seam, restated

`prompt_name` is the same shape `images` and `model` are: data the caller has already resolved, not a collaborator. The property this test is about — no session, no vector store, no reranker — is unchanged.

```python
    # `images` is data, like `evidence`: chat attachments of kind 'image', already
    # read off disk by the caller. `model` is the same shape - a string the caller
    # has ALREADY validated against the allowlist, not a capability. `prompt_name`
    # is Slice 4's agent and is the same shape again: it names WHICH stored prompt
    # `get_prompt` should read, so it is still one lookup through the indirection
    # this function already had. None of the four carries a session or a retrieval
    # collaborator, which is the property this test is actually about.
    assert params == [
        "llm_provider",
        "question",
        "history",
        "evidence",
        "settings",
        "images",
        "model",
        "prompt_name",
    ]
```

- [ ] **Step 3: Modify `scripts/check_all_plans.py`**

A plan missing from this list is a plan nobody checks.

```python
    "docs/superpowers/plans/2026-08-30-slice-3-orchestrator.md",
    "docs/superpowers/plans/2026-08-30-slice-4-agents.md",
```

---

## Verification

- `cd backend && python -m pytest` — 677 passed, against `mopan_test_slice4` (dropped afterwards). Baseline was 652; the 25 new ones are `tests/test_agents.py`.
- `cd backend && python -m ruff check .` — clean.
- `cd frontend && npx tsc --noEmit` — 0 errors; `npm run build` succeeds; `npm test` — 6 pass.
- `python scripts/check_all_plans.py` — exit 0.
- Raw hex and Tailwind default-palette classes in the changed frontend files — 0 and 0.

## Driven, not assumed

Against the running stack, admin `smoke-admin@example.com`, with a second collection (격리 시험) holding one document whose numbers appear nowhere else:

- The DEFAULT agent, asked for those numbers, answers with them and its trace cites `격리자료.md`.
- The agent restricted to 일반, asked the SAME question, does not, and its trace lists only `농약 안전사용 지침.md` and `연구보고서 A.pdf` — `격리자료.md` is absent.
- The identical two-step plan is accepted against the default catalogue and refused whole against the agent's: `등록되지 않은 도구를 지정한 계획입니다: 현장 대장/record_application`.
- A hand-picked forbidden tool in the composer: `이 에이전트가 사용할 수 없는 도구입니다.`
- A non-admin: no 에이전트 관리 link, `관리자 권한이 필요합니다.` on the screen, 403 on `POST /api/agents`, and 200 on `GET /api/agents/selectable` — because picking an agent is not an administrative act.
