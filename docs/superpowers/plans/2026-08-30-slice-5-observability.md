# MOPAN Slice 5 — Conversation Trace, Feedback, Advanced Settings — Implementation Plan

> **Scope:** one assistant answer becomes explainable — every retrieved item with its per-stage scores and **whether the token budget cut it** — plus 👍/👎 per answer and an admin screen for the `.env` values that are safe to change at runtime. The other plan files in this directory are frozen history and are not amended by this document. Slice 2 (MCP) is a parallel plan and owns everything MCP.

**Spec:** `docs/superpowers/specs/2026-08-30-slices-2-to-5-design.md`, the Slice 5 section. UI governed by `docs/superpowers/specs/2026-08-30-design-language.md`.

**What ships:**
- `messages.trace` (JSONB), written by `chat.service.build_trace`: every retrieved item, its `vector_rank` / `keyword_rank` / `rrf_score` / `rerank_score`, its token cost, and `included` — whether `ANSWER_CONTEXT_TOKEN_BUDGET` let it reach the prompt.
- `GET /api/messages/{id}/trace`, owner-scoped, 404 and never 403.
- `message_feedback` — one row per (message, user), changeable, with an optional comment. `PUT /api/messages/{id}/feedback`, and the caller's own rating rides the transcript.
- `app_settings` — a runtime override per `.env` key, read through `get_app_settings` with the environment value as the fallback. `GET /api/settings`, `PUT /api/settings/{key}`, `DELETE /api/settings/{key}`, admin only.
- Migration `0005`, both directions.
- A 답변 추적 dialog on every answer and a 고급 설정 screen under the sidebar's 관리 section.

## Decisions

**The data was NOT all already there, and this is the correction.** The Slice 1 design says the trace needs nothing new captured. That is true of the *message* columns — `model`, `prompt_name`, `prompt_version`, `usage`, `latency_ms`, `retrieval_ms` are all real columns and all filled. It is not true of the evidence. `Message.citations` records only the items the model actually cited, and it drops the four per-stage scores entirely: `_citations_from` copies `chunk_id`, `filename`, `page`, `section`, `snippet` and one collapsed `score`. The separate ranks live in `Evidence.metadata` in memory, reach `hybrid_search`'s `duration_ms` log line only as counts, and are persisted nowhere. **Evidence that was retrieved and then cut by the token budget left no record at all.** So one column is added, and it is the cut items that justify it.

**One JSONB column, not a `trace_evidence` table.** The trace is read whole, by one screen, for one message; it is never queried across rows, and a table would be a join and a migration for every field Slice 3 wants to add. The constraint in the brief — "an MCP step can be added later without a migration" — is satisfied structurally: an execution plan is a new key in this object, and MCP evidence already arrives in the same `list[Evidence]` with `source_type="mcp"`.

**`included` is decided by identity, not by position.** `build_prompt` returns the evidence that fitted, and today it stops at the first item that does not, so the returned list is a prefix and `index < len(used)` would agree. Nothing pins that. A later change to skip-and-continue would silently attach every per-item score to the wrong row, so the flag is `id(item) in {id(u) for u in used}`.

**Trace and feedback are owner-scoped with 404, matching `get_owned_conversation`.** One statement, joined through `conversations`, so ownership is a predicate the database applies rather than a check a caller can forget. **There is no admin bypass.** An admin reading everyone's traces is a different feature with a different argument — it is a transcript, and the corpus-versus-conversation split in the Slice 1 authorization table puts conversations firmly on the private side. If it is wanted, it belongs behind `require_admin` on a separate route with its own audit line, not as a widened predicate here.

**The `done` frame now carries `message_id`.** The client used to fabricate `assistant-${Date.now()}` for a just-streamed answer, so 👍/👎 and 추적 would have 404'd on the answer the user was looking at. `persist_turn` returns the assistant row's id and the frame carries it.

**Settings are read through `get_app_settings`, the indirection every route already uses.** Not a new dependency that a future router could forget: the existing one becomes async and applies the overrides, so "an admin's change reaches the next request" is true everywhere at once. The session comes from `app.core.db.current_sessionmaker` — set per request by `RequestContextMiddleware` — for exactly the reason `get_prompt` reads it from there: no signature grows a `db`, and no request opens a second session. **No cache**, for the same reason the prompt store has none: the point of the feature is that an edit applies to the very next question, in every worker, with no restart and no invalidation message to lose.

**An empty `app_settings` table behaves exactly like today.** Migration `0005` deliberately seeds nothing — seeding the `.env` values would freeze them into the database, so a later `.env` change would silently stop applying. Every failure path in the store returns the base settings.

**Validation is split, and deliberately.** The write path runs the real pydantic validators (`Settings(**{**base.model_dump(), **update})`), which is what catches `CHUNK_OVERLAP >= CHUNK_SIZE` — a pair where each half is in range and the combination is not. The read path must not: it runs on every request and constructing a `Settings` re-reads the `.env` file. So the read path does a per-key parse and range check against the spec table and drops what it cannot use, with the single cross-field pair restated because `model_copy` does not re-run validators.

**`OPENAI_API_KEY` is not filtered out of the settings screen — it has no entry.** `RUNTIME_SAFE_SETTINGS` is the only enumerable set, for reads and writes alike, so there is nothing for a future key to be accidentally added to. `EMBEDDING_MODEL` and `EMBEDDING_DIM` are refused too, and the screen renders the reason from `ENV_ONLY_SETTINGS` — served by the API rather than written into the page, so the argument lives beside the decision.

**The chunking knobs reach the worker.** They are editable and they would be a lie otherwise: `process_document` loads the overrides per JOB, not at worker start. The screen says out loud that they apply only to documents ingested afterwards, because an admin who raises `CHUNK_SIZE` and waits for the corpus to change will be wrong for a long time before anything tells them.

## Global Constraints

- Every user-facing `detail=` is natural Korean. `frontend/lib/api.ts:detailText` drops a `detail` with no Hangul, so an English string is invisible to the user.
- Alembic only, and both directions: `tests/conftest.py:migrated_database` runs `downgrade base` at the start of every session.
- The `compare_metadata` drift test stays green — every migration change has a matching ORM change.
- One pytest session at a time, never `-n auto`.
- Tokens only in the UI. A raw hex or a Tailwind default-palette class is a defect.
- No test makes a real network call or a real OpenAI API call.
- Slice 2 owns `app/mcp/**` and shares `app/chat/router.py`; nothing here touches MCP.

---

### Task 1: The `trace` column, the two new tables, and migration 0005

**Files:**
- Create: `backend/app/models/feedback.py`, `backend/app/models/app_setting.py`, `backend/alembic/versions/0005_observability.py`
- Modify: `backend/app/models/message.py`, `backend/app/models/__init__.py`, `backend/tests/conftest.py`

**Interfaces:**
- Produces: `Message.trace`, `MessageFeedback`, `AppSetting`, `uq_message_feedback_message_user`.
- Consumed by: `build_trace` (Task 2), the settings store (Task 3), the routes (Task 4), and `tests/test_schema.py:test_orm_matches_migrated_schema`, which fails if the ORM and the migration disagree on any of it.


- [ ] **Step 1: Write `backend/app/models/feedback.py`**

One row per (message, user), updated in place — the opposite of `prompts`, and for the opposite reason: a rating is a current opinion, not a historical record. The unique constraint IS the "one per user per message" rule, so a double click loses the second write to the database rather than to a check-then-insert race.

```python
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

FEEDBACK_RATINGS = ("up", "down")


class MessageFeedback(Base):
    """One row per (message, user). A rating is CHANGEABLE, so this is the one
    table in the project that is updated in place rather than versioned - the
    opposite of `prompts`, and for the opposite reason: a rating is a current
    opinion, not a historical record, and a user who clicks down after up means
    the second one.

    Joining it to the trace needs nothing extra: `message_id` is the assistant
    row that carries `messages.trace`, so "every down-vote since Tuesday, with
    the evidence the budget cut from each" is one join.
    """

    __tablename__ = "message_feedback"
    __table_args__ = (
        # The uniqueness IS the "one per user per message" rule. In app code it
        # would be a check-then-insert with a race between the two halves; here a
        # double click that gets two requests in flight loses the second to the
        # constraint instead of writing two rows that disagree.
        UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
        CheckConstraint("rating in ('up', 'down')", name="ck_message_feedback_rating_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rating: Mapped[str] = mapped_column(String(10), nullable=False)
    # Nullable rather than defaulted to "": the comment is optional, and an empty
    # string would be indistinguishable from "the user cleared what they wrote".
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Write `backend/app/models/app_setting.py`**

A natural key — the env var name — and no `updated_by`. That column would be the first foreign key in this schema with no good `ON DELETE`: CASCADE would revert an override because the admin who set it left, and RESTRICT would make that admin undeletable.

```python
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppSetting(Base):
    """A runtime override for ONE `.env` value, keyed by its environment-variable
    name. Absent means "use the value the process booted with", so an EMPTY TABLE
    behaves exactly as the deployment did before this table existed - the same
    fallback rule `get_prompt` follows for the answer template.

    `value` is text for every key, parsed against
    `app/core/settings_store.py:RUNTIME_SAFE_SETTINGS`. A typed column per kind
    would be three nullable columns plus a discriminator to say which one is
    real; the spec table already knows each key's type and has to parse a form
    field anyway.

    No `updated_by` column. It would be the fourth foreign key in the schema and
    the first one with no good `ON DELETE`: CASCADE would silently revert an
    override because the admin who set it left the company, and RESTRICT would
    make that admin undeletable. Who changed what is in the
    `app_setting_changed` log line instead.
    """

    __tablename__ = "app_settings"

    # The env var name, e.g. RETRIEVAL_TOP_N. A natural key, so there is no
    # surrogate id and no second uniqueness rule to keep in step with it.
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 3: Modify `backend/app/models/message.py`**

The one new column, and the argument for why the trace was not free. Note it is JSONB rather than a table: Slice 3's plan and its steps are a new key here, not a migration.

```python
    trace: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
```

- [ ] **Step 4: Modify `backend/app/models/message.py`**

The caller's own rating, loaded with the transcript. A list rather than `uselist=False`: a conversation has one reader and the unique constraint makes that one row, and modelling it as one-to-one would bake that reasoning into the mapper.

```python
    feedback: Mapped[list[MessageFeedback]] = relationship(lazy="selectin", viewonly=True)
```

- [ ] **Step 5: Write `backend/alembic/versions/0005_observability.py`**

`trace` is NOT NULL with a server_default rather than nullable: every pre-existing message gets `{}`, which the schema already has to handle for an answer whose retrieval produced nothing, and a nullable column would add a second empty case meaning the same thing. `app_settings` is seeded with nothing at all — that is what makes an empty table identical to today.

```python
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
```

- [ ] **Step 6: Modify `backend/app/models/__init__.py`**

Exported so `Base.metadata` sees them. Without this the drift test compares a migrated database against an ORM that has never heard of the new tables.

```python
from app.models.app_setting import AppSetting
```

- [ ] **Step 7: Modify `backend/tests/conftest.py`**

`app_settings` is the row that matters here: a leftover override would silently change retrieval for every later test, and a "behaves like `.env` when the table is empty" test would pass with its guard removed because the table was never empty.

```python
    # Before `messages`, which it points at. Truncated per test like every other
    # row a test writes - and `app_settings` below is the one that MATTERS: a
    # leftover override would silently change retrieval for every later test, and
    # a "behaves like .env when the table is empty" test would pass with its
    # guard removed because the table was never empty.
    "message_feedback",
    "app_settings",
```

---

### Task 2: `build_trace`, the payload shape, and the assistant message id on the `done` frame

**Files:**
- Create: `backend/app/schemas/observability.py`
- Modify: `backend/app/chat/service.py`, `backend/app/chat/router.py`

**Interfaces:**
- Produces: `ChatAnswer.trace`, `build_trace`, `TraceResponse`, `persist_turn -> uuid.UUID`, and `message_id` on the SSE `done` frame.
- Consumed by: `GET /api/messages/{id}/trace` (Task 4) and `ChatWindow` (Task 6).
- **Unchanged:** `answer()`'s signature. It takes no session, no vector store and no reranker, and `tests/test_chat_service.py` pins that as the Slice 3 seam. The trace is built from what `answer()` already holds — the evidence it was given and the subset `build_prompt` reports fitting.


- [ ] **Step 1: Write `backend/app/schemas/observability.py`**

Real shapes rather than a bare `dict`, so the JSONB is validated on the way out and the TypeScript on the other side has something to mirror. Every field of a trace item is optional-by-kind because an attachment or an MCP item carries none of the RAG keys. The feedback and settings schemas live here too — they are the same screen's vocabulary.

```python
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

# Long enough for a sentence about what went wrong, short enough that the column
# is not a free-form document store. Trimmed, and an all-whitespace comment is
# normalised to "no comment" in the router rather than stored as spaces.
FeedbackComment = Annotated[str, StringConstraints(max_length=1000)]


class FeedbackRequest(BaseModel):
    """Body of PUT /api/messages/{id}/feedback. There is no message id in it -
    the path owns that - and no user id: it is always the caller."""

    rating: Literal["up", "down"]
    comment: FeedbackComment | None = None


class FeedbackResponse(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = None
    updated_at: datetime


class TraceEvidence(BaseModel):
    """One retrieved item, as it was at answer time.

    `included` is the field this whole screen exists for: false means the item
    was retrieved and then dropped because ANSWER_CONTEXT_TOKEN_BUDGET ran out
    before it, so the model never saw it.
    """

    index: int
    source_type: str
    ref: str
    chunk_id: str | None = None
    document_id: str | None = None
    filename: str | None = None
    page: int | None = None
    section: str | None = None
    # Null means the item was absent from that ranking entirely - a chunk the
    # keyword search never returned has no keyword_rank, and that is a fact worth
    # showing rather than a gap to fill with a zero.
    vector_rank: int | None = None
    keyword_rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    score: float | None = None
    tokens: int = 0
    snippet: str = ""
    included: bool = True


class TraceRetrieval(BaseModel):
    """The knobs the answer was produced under, as they were AT THAT MOMENT.

    Recorded rather than read back from the current settings, because the whole
    point of the 고급 설정 screen is that these change - reading them live would
    make every old trace describe today's configuration.
    """

    top_n: int | None = None
    candidate_limit: int | None = None
    rrf_k: int | None = None
    sparse_weight: float | None = None
    token_budget: int | None = None
    evidence_count: int = 0
    included_count: int = 0


class TraceResponse(BaseModel):
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    created_at: datetime

    # Straight off the Message columns Slice 1 added for this.
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    latency_ms: int | None = None
    retrieval_ms: int | None = None
    usage: dict = Field(default_factory=dict)

    # From messages.trace. Empty for every answer written before migration 0005,
    # which the screen reports as "추적 정보가 없습니다" rather than as an error.
    has_trace: bool = False
    retrieval: TraceRetrieval = Field(default_factory=TraceRetrieval)
    evidence: list[TraceEvidence] = Field(default_factory=list)


class SettingResponse(BaseModel):
    """One runtime-safe setting. `value` is what the app is using right now;
    `env_value` is what it would fall back to if the override were removed, which
    is what makes 기본값으로 되돌리기 a promise the screen can keep."""

    key: str
    label: str
    help: str
    group: str
    kind: Literal["int", "float"]
    minimum: float
    maximum: float
    value: float
    env_value: float
    overridden: bool


class EnvOnlySettingResponse(BaseModel):
    """A value that is deliberately NOT editable here, with the reason. Served by
    the API rather than written into the screen so the reason lives beside the
    decision in app/core/settings_store.py."""

    key: str
    label: str
    reason: str


class SettingsResponse(BaseModel):
    settings: list[SettingResponse]
    env_only: list[EnvOnlySettingResponse]


class SettingUpdate(BaseModel):
    # A string, not a number: the form sends text, and the per-key parse in
    # SettingSpec is what turns it into an int or a float with a Korean message
    # when it will not. A `float` here would answer "6.5" for an integer setting
    # with a silent truncation, and a bad value with an English 422.
    value: str = Field(max_length=100)
```

- [ ] **Step 2: Modify `backend/app/chat/service.py`**

Everything that was retrieved and whether it reached the prompt. The cut items are the point; the identity test is what keeps them attached to the right scores.

```python
TRACE_VERSION = 1


def build_trace(
    evidence: list[Evidence],
    used: list[Evidence],
    *,
    settings: Settings,
    prompt: PromptTemplate,
) -> dict:
    """Everything that was retrieved, and whether it reached the prompt.

    THE CUT ITEMS ARE THE POINT. `citations` records only what the model cited,
    so an item that was retrieved at rank 9 and dropped by
    ANSWER_CONTEXT_TOKEN_BUDGET used to leave no record anywhere - and "why did
    it not answer from the document I uploaded" is almost always that. `used` is
    what `build_prompt` reports actually fitting, so `included` is measured, not
    inferred from the budget arithmetic a second time.

    Identity, not position: `build_prompt` currently stops at the first item that
    does not fit, so `used` is a prefix and `index < len(used)` would agree - but
    a later change to skip-and-continue would silently make that wrong, and the
    per-item scores here would then be attached to the wrong rows.

    `index` numbers ALL retrieved evidence from 1, and for the included items it
    is the same number the model saw and cited, because `build_prompt` enumerates
    the same list in the same order.

    Slice 3 adds `plan` and its steps as new keys here, and MCP items arrive in
    `evidence` with `source_type="mcp"`. Neither needs a migration - that is what
    the JSONB column is for.
    """
    used_ids = {id(item) for item in used}
    items = []
    for index, item in enumerate(evidence, start=1):
        metadata = item.metadata
        items.append(
            {
                "index": index,
                "source_type": item.source_type,
                "ref": item.ref,
                # .get throughout: an attachment or MCP item carries none of the
                # RAG keys and still has to appear in the trace.
                "chunk_id": metadata.get("chunk_id"),
                "document_id": metadata.get("document_id"),
                "filename": metadata.get("filename"),
                "page": metadata.get("page"),
                "section": metadata.get("section"),
                # The four Slice 1 kept SEPARATE rather than collapsing into one
                # score, for exactly this screen.
                "vector_rank": metadata.get("vector_rank"),
                "keyword_rank": metadata.get("keyword_rank"),
                "rrf_score": metadata.get("rrf_score"),
                "rerank_score": metadata.get("rerank_score"),
                "score": item.score,
                "tokens": count_tokens(item.content),
                "snippet": item.content[:SNIPPET_CHARS],
                "included": id(item) in used_ids,
            }
        )
    return {
        "version": TRACE_VERSION,
        "retrieval": {
            "top_n": settings.retrieval_top_n,
            "candidate_limit": settings.retrieval_candidate_limit,
            "rrf_k": settings.rrf_k,
            "sparse_weight": settings.sparse_weight,
            "token_budget": settings.answer_context_token_budget,
            "evidence_count": len(evidence),
            "included_count": len(used),
        },
        # Duplicated from the columns on purpose, and only these two: the trace
        # has to stay readable as one object when it is pulled out of the
        # database by hand, and a prompt version that was later deleted is still
        # named here.
        "prompt": {"name": prompt.name, "version": prompt.version},
        "evidence": items,
    }
```

- [ ] **Step 3: Modify `backend/app/chat/service.py`**

Written in the same transaction as the answer, and the assistant id returned so the SSE frame can carry it. The flush is now unconditional because that id is read whether or not the turn carried a file.

```python
    db.add(assistant_message)
```

- [ ] **Step 4: Modify `backend/app/chat/router.py`**

The real row id on the `done` frame. Without it the 👍/👎 and 추적 controls on a just-streamed answer point at nothing.

```python
                    # The REAL row id, so the 👍/👎 and 추적 controls work on a
                    # just-streamed answer without a reload. The client used to
                    # fabricate `assistant-${Date.now()}` here, which pointed at
                    # nothing.
                    "message_id": str(assistant_message_id),
```

---

### Task 3: The runtime settings store and the `get_app_settings` indirection

**Files:**
- Create: `backend/app/core/settings_store.py`
- Modify: `backend/app/core/config.py`, `backend/app/worker.py`

**Interfaces:**
- Produces: `RUNTIME_SAFE_SETTINGS`, `ENV_ONLY_SETTINGS`, `load_overrides`, `apply_overrides`, `validated_settings`, `effective_settings`.
- Consumed by: every route through `get_app_settings`, the admin routes (Task 4), and `worker.process_document`.


- [ ] **Step 1: Write `backend/app/core/settings_store.py`**

The spec table is the whole boundary: only these keys are readable, writable, or applicable, so a secret is not hidden from the screen — it has no entry. The read path parses and range-checks; the write path additionally runs the real validators, which is what catches a pair that is only invalid together.

```python
"""Runtime-editable settings, read through an indirection with `.env` as the
fallback - the same shape `app/chat/prompt.py:get_prompt` gives the answer
template.

Two rules hold this together and both are tested:

1.  **An empty `app_settings` table behaves exactly like today.** Every value
    falls back to the `Settings` the process booted with, which is the `.env`
    value. Nothing here seeds a row, and every failure path returns the base
    settings unchanged.
2.  **Only the keys in `RUNTIME_SAFE_SETTINGS` exist.** The admin API reads and
    writes nothing else, so a secret is not "hidden" from the screen - it has no
    entry, which is why `OPENAI_API_KEY` can be neither read nor written through
    it however the request is spelled.
"""

import logging
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings

logger = logging.getLogger("mopan.settings")

RETRIEVAL = "retrieval"
CHUNKING = "chunking"

# Applies to the WHOLE chunking group and is repeated on screen, because an admin
# who raises CHUNK_SIZE and expects the corpus to re-chunk will be wrong for a
# long time before anything tells them so.
CHUNKING_SCOPE_NOTE = (
    "이 값은 앞으로 등록되는 문서에만 적용됩니다. 이미 색인된 문서는 다시 등록해야 바뀝니다."
)


@dataclass(frozen=True)
class SettingSpec:
    """One runtime-safe `.env` value.

    `minimum`/`maximum` duplicate part of `Settings._finalise` on purpose. The
    write path runs the real pydantic validators as well (see
    `validated_settings`), but the read path must not: it runs on every request,
    and constructing a `Settings` re-reads the `.env` FILE each time. So the
    bounds are stated here in a form both paths can afford, and the write path
    adds the cross-field checks on top.
    """

    key: str
    field: str
    kind: type[int] | type[float]
    minimum: float
    maximum: float
    group: str
    label: str
    help: str

    def parse(self, raw: str) -> int | float:
        try:
            value = self.kind(raw)
        except (TypeError, ValueError) as exc:
            expected = "정수" if self.kind is int else "숫자"
            raise ValueError(f"{self.key} 값은 {expected}여야 합니다.") from exc
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"{self.key} 값은 {_number(self.minimum)}에서 {_number(self.maximum)} 사이여야 합니다."
            )
        return value


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


RUNTIME_SAFE_SETTINGS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        SettingSpec(
            key="RETRIEVAL_TOP_N",
            field="retrieval_top_n",
            kind=int,
            minimum=1,
            maximum=50,
            group=RETRIEVAL,
            label="답변에 사용할 근거 수",
            help=(
                "검색 결과 중 상위 몇 개를 모델에게 넘길지 정합니다. "
                "늘리면 근거가 많아지지만 토큰 예산을 더 빨리 소진합니다."
            ),
        ),
        SettingSpec(
            key="RETRIEVAL_CANDIDATE_LIMIT",
            field="retrieval_candidate_limit",
            kind=int,
            minimum=1,
            maximum=200,
            group=RETRIEVAL,
            label="후보 검색 개수",
            help=(
                "벡터 검색과 키워드 검색이 각각 가져오는 후보 수입니다. "
                "재순위 모델이 점수를 매기는 대상이기도 합니다."
            ),
        ),
        SettingSpec(
            key="RRF_K",
            field="rrf_k",
            kind=int,
            minimum=0,
            maximum=1000,
            group=RETRIEVAL,
            label="RRF 상수 (k)",
            help=(
                "두 검색 결과를 합칠 때 쓰는 상수입니다. 값이 작을수록 각 목록의 1위가 "
                "더 강하게 반영됩니다. 기본값은 60입니다."
            ),
        ),
        SettingSpec(
            key="SPARSE_WEIGHT",
            field="sparse_weight",
            kind=float,
            minimum=0.0,
            maximum=10.0,
            group=RETRIEVAL,
            label="키워드 검색 가중치",
            help="키워드(FTS) 검색 결과의 비중입니다. 0으로 두면 벡터 검색만 사용합니다.",
        ),
        SettingSpec(
            key="ANSWER_CONTEXT_TOKEN_BUDGET",
            field="answer_context_token_budget",
            kind=int,
            minimum=1,
            maximum=200_000,
            group=RETRIEVAL,
            label="답변 컨텍스트 토큰 예산",
            help=(
                "근거와 대화 이력에 쓸 수 있는 전체 토큰 상한입니다. 이 예산을 넘긴 근거는 "
                "모델에게 전달되지 않으며, 어떤 근거가 잘렸는지는 각 답변의 추적 화면에서 "
                "볼 수 있습니다."
            ),
        ),
        SettingSpec(
            key="CHUNK_SIZE",
            field="chunk_size",
            kind=int,
            minimum=100,
            maximum=20_000,
            group=CHUNKING,
            label="청크 크기 (문자)",
            help=f"문서를 나눌 때의 목표 길이입니다. {CHUNKING_SCOPE_NOTE}",
        ),
        SettingSpec(
            key="CHUNK_OVERLAP",
            field="chunk_overlap",
            kind=int,
            minimum=0,
            maximum=19_999,
            group=CHUNKING,
            label="청크 겹침 (문자)",
            help=f"인접한 청크가 겹치는 길이입니다. 청크 크기보다 작아야 합니다. {CHUNKING_SCOPE_NOTE}",
        ),
        SettingSpec(
            key="MAX_CHUNK_TOKENS",
            field="max_chunk_tokens",
            kind=int,
            minimum=1,
            maximum=4095,
            group=CHUNKING,
            label="청크 최대 토큰",
            help=(
                "한 청크가 넘을 수 없는 토큰 수입니다. 임베딩 모델의 입력 한도 때문에 "
                f"4095가 상한입니다. {CHUNKING_SCOPE_NOTE}"
            ),
        ),
        SettingSpec(
            key="SEMANTIC_SIMILARITY_THRESHOLD",
            field="semantic_similarity_threshold",
            kind=float,
            minimum=-1.0,
            maximum=1.0,
            group=CHUNKING,
            label="의미 병합 임계값",
            help=(
                "인접한 청크를 합칠지 판단하는 코사인 유사도 기준입니다. 낮출수록 청크가 "
                f"커집니다. {CHUNKING_SCOPE_NOTE}"
            ),
        ),
    )
}


@dataclass(frozen=True)
class EnvOnlySetting:
    """A value that looks like it belongs on this screen and does not.

    It lives beside the editable specs rather than in the frontend so that the
    reason travels with the decision: a later contributor who wants to make one
    of these editable reads why here, at the point they would have to delete it.
    """

    key: str
    label: str
    reason: str


ENV_ONLY_SETTINGS: list[EnvOnlySetting] = [
    EnvOnlySetting(
        key="EMBEDDING_MODEL",
        label="임베딩 모델",
        reason=(
            "이 값을 바꾸면 이미 저장된 모든 임베딩과 새 질문의 임베딩이 서로 다른 공간에 놓여 검색이 "
            "조용히 무의미해집니다. 전체 문서를 다시 색인해야 하므로 환경변수로만 바꿉니다."
        ),
    ),
    EnvOnlySetting(
        key="EMBEDDING_DIM",
        label="임베딩 차원",
        reason=(
            "chunks.embedding 컬럼의 실제 차원과 같아야 합니다. 바꾸려면 마이그레이션과 전체 재색인이 "
            "필요하고, 불일치하면 서버가 준비 상태 점검에서 기동을 거부합니다. 환경변수로만 바꿉니다."
        ),
    ),
]

_OVERRIDES_SQL = text("SELECT key, value FROM app_settings")

NOT_RUNTIME_SAFE_MESSAGE = "런타임에서 변경할 수 없는 설정입니다: {key}"
INVALID_COMBINATION_MESSAGE = (
    "설정값 조합이 올바르지 않습니다. 다른 설정과 함께 성립할 수 있는 값으로 다시 입력해 주세요."
)


async def load_overrides(session: AsyncSession) -> dict[str, str]:
    """Raw rows, unparsed. Unknown keys are dropped here rather than at the point
    of use: a key that was runtime-safe in an older build and is not any more
    must not keep applying just because its row survived."""
    rows = (await session.execute(_OVERRIDES_SQL)).all()
    return {row.key: row.value for row in rows if row.key in RUNTIME_SAFE_SETTINGS}


def apply_overrides(base: Settings, overrides: dict[str, str]) -> Settings:
    """The READ path. Per-key parse and range check, then one `model_copy`.

    A value that does not parse is DROPPED with a log rather than raising: this
    runs on the request path, and one bad row must not take answering down - the
    same rule `get_prompt` follows. The write path is what makes bad rows
    unreachable in the first place.
    """
    update: dict[str, int | float] = {}
    for key, raw in overrides.items():
        spec = RUNTIME_SAFE_SETTINGS[key]
        try:
            update[spec.field] = spec.parse(raw)
        except ValueError:
            logger.warning("ignoring unusable app_settings row", extra={"extra_fields": {"key": key}})
    # The one cross-field constraint among the runtime-safe keys, restated
    # because `model_copy` does NOT re-run validators - and `FixedChunking`
    # raises on overlap >= size, so a hand-edited pair of rows would break
    # ingestion rather than degrade it. Both are dropped together: keeping one
    # half of an invalid pair is not a repair.
    size = update.get("chunk_size", base.chunk_size)
    overlap = update.get("chunk_overlap", base.chunk_overlap)
    if not 0 <= overlap < size:
        logger.warning("ignoring CHUNK_SIZE/CHUNK_OVERLAP override pair: overlap must be < size")
        update.pop("chunk_size", None)
        update.pop("chunk_overlap", None)
    return base.model_copy(update=update) if update else base


def validated_settings(base: Settings, overrides: dict[str, str]) -> Settings:
    """The WRITE path. Runs the real pydantic validators, cross-field checks and
    all, so that nothing an admin saves can be a row `apply_overrides` has to
    drop later. Raises `ValueError` with a Korean message.

    Constructing a `Settings` re-reads the `.env` file, which is why this is not
    on the read path. Every field of `base` is passed explicitly, so the file
    cannot win over the values being validated.
    """
    update: dict[str, int | float] = {}
    for key, raw in overrides.items():
        spec = RUNTIME_SAFE_SETTINGS.get(key)
        if spec is None:
            raise ValueError(NOT_RUNTIME_SAFE_MESSAGE.format(key=key))
        update[spec.field] = spec.parse(raw)
    try:
        return Settings(**{**base.model_dump(), **update})
    except ValidationError as exc:
        # The pydantic message is English and would render verbatim in a Korean
        # UI, so it goes to the log and the user gets the sentence above.
        logger.warning("rejected settings combination", extra={"extra_fields": {"error": str(exc)}})
        raise ValueError(INVALID_COMBINATION_MESSAGE) from exc


async def effective_settings(session: AsyncSession, base: Settings) -> Settings:
    """`base` with whatever the database overrides. Every failure returns `base`.

    Used from the request path via `get_app_settings` and from the arq worker at
    the top of each job, which is what makes a chunking change apply to the next
    ingestion without a worker restart.
    """
    try:
        overrides = await load_overrides(session)
    except Exception:
        logger.exception("settings override lookup failed; using the environment values")
        return base
    return apply_overrides(base, overrides)
```

- [ ] **Step 2: Modify `backend/app/core/config.py`**

The single indirection every route already goes through. The session comes from the ContextVar the prompt store uses, so no signature grows a `db` and no request opens a second session. An empty table returns `app.state.settings` unchanged.

```python
async def get_app_settings(request: Request) -> Settings:
```

- [ ] **Step 3: Modify `backend/app/worker.py`**

Per JOB, not per worker start. Otherwise the chunking controls on the 고급 설정 screen would save, report themselves applied, and change nothing until the next deploy.

```python
                settings = await effective_settings(db, ctx["settings"])
```

---

### Task 4: The trace, feedback and settings routes

**Files:**
- Create: `backend/app/observability/__init__.py`, `backend/app/observability/router.py`
- Modify: `backend/app/main.py`, `backend/app/schemas/chat.py`

**Interfaces:**
- Produces: `GET /api/messages/{id}/trace`, `PUT /api/messages/{id}/feedback`, `GET /api/settings`, `PUT /api/settings/{key}`, `DELETE /api/settings/{key}`, and `MessageResponse.feedback`.
- Consumed by: the chat transcript, `TraceDialog` and the 고급 설정 screen.


- [ ] **Step 1: Write `backend/app/observability/router.py`**

One ownership helper, joined through `conversations`, used by both message routes. 404 for a missing id, someone else's id, and a user turn alike. The settings routes reach nothing outside the spec table.

```python
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.core.settings_store import (
    ENV_ONLY_SETTINGS,
    NOT_RUNTIME_SAFE_MESSAGE,
    RUNTIME_SAFE_SETTINGS,
    SettingSpec,
    load_overrides,
    validated_settings,
)
from app.models.app_setting import AppSetting
from app.models.conversation import Conversation
from app.models.feedback import MessageFeedback
from app.models.message import Message
from app.models.user import User
from app.schemas.observability import (
    EnvOnlySettingResponse,
    FeedbackRequest,
    FeedbackResponse,
    SettingResponse,
    SettingsResponse,
    SettingUpdate,
    TraceResponse,
)

logger = logging.getLogger("mopan.observability")
router = APIRouter(prefix="/api", tags=["observability"])

# The SAME message for "no such message", "someone else's message" and "a user
# turn, which has no trace" - the rule get_owned_conversation established, for
# the same reason: a 403 on the second case would confirm that an id someone
# guessed exists. There is deliberately no admin bypass; see the module note in
# the plan.
MESSAGE_NOT_FOUND_MESSAGE = "답변을 찾을 수 없습니다."


async def _owned_assistant_message(db: AsyncSession, message_id: uuid.UUID, user: User) -> Message:
    """One statement, joined through `conversations`, so ownership is a predicate
    the database applies rather than a check a caller can forget after loading
    the row by bare id."""
    message = await db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == message_id,
            Message.role == "assistant",
            Conversation.user_id == user.id,
        )
    )
    if message is None:
        raise HTTPException(status_code=404, detail=MESSAGE_NOT_FOUND_MESSAGE)
    return message


@router.get("/messages/{message_id}/trace", response_model=TraceResponse)
async def get_trace(
    message_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Why this answer looks the way it does: every retrieved item with its
    per-stage ranks and scores, WHICH OF THEM THE TOKEN BUDGET CUT, the model,
    the prompt version and the timings.

    Owner-scoped exactly like the transcript it belongs to."""
    message = await _owned_assistant_message(db, message_id, user)
    trace = message.trace or {}
    return TraceResponse(
        message_id=message.id,
        conversation_id=message.conversation_id,
        created_at=message.created_at,
        model=message.model,
        prompt_name=message.prompt_name,
        prompt_version=message.prompt_version,
        latency_ms=message.latency_ms,
        retrieval_ms=message.retrieval_ms,
        usage=message.usage or {},
        # An answer written before 0005 has {} here and is not an error: the
        # screen says so and still shows the columns, which are real.
        has_trace=bool(trace.get("evidence") is not None),
        retrieval=trace.get("retrieval") or {},
        evidence=trace.get("evidence") or [],
    )


@router.put("/messages/{message_id}/feedback", response_model=FeedbackResponse)
async def put_feedback(
    message_id: uuid.UUID,
    payload: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """One rating per user per message, changeable. PUT rather than POST because
    that is what it is: the same URL, written again, replaces what was there.

    ON CONFLICT rather than select-then-insert. The unique constraint is the rule;
    reading it first and then writing leaves a window between the two halves that
    a double click reaches, and the loser would be a 500 on a duplicate key.
    """
    message = await _owned_assistant_message(db, message_id, user)
    comment = (payload.comment or "").strip() or None
    row = (
        await db.execute(
            pg_insert(MessageFeedback)
            .values(
                id=uuid.uuid4(),
                message_id=message.id,
                user_id=user.id,
                rating=payload.rating,
                comment=comment,
            )
            .on_conflict_do_update(
                constraint="uq_message_feedback_message_user",
                # updated_at explicitly: `onupdate` is a SQLAlchemy ORM hook and
                # this is a Core statement, so without it a changed rating would
                # keep the timestamp of the original one.
                set_={"rating": payload.rating, "comment": comment, "updated_at": func.now()},
            )
            .returning(MessageFeedback.rating, MessageFeedback.comment, MessageFeedback.updated_at)
        )
    ).one()
    await db.commit()
    log_event(
        logger,
        "message_feedback_recorded",
        message_id=str(message.id),
        rating=payload.rating,
        has_comment=comment is not None,
    )
    return FeedbackResponse(rating=row.rating, comment=row.comment, updated_at=row.updated_at)


def _setting_response(spec: SettingSpec, effective: Settings, base: Settings) -> SettingResponse:
    return SettingResponse(
        key=spec.key,
        label=spec.label,
        help=spec.help,
        group=spec.group,
        kind="int" if spec.kind is int else "float",
        minimum=spec.minimum,
        maximum=spec.maximum,
        value=getattr(effective, spec.field),
        env_value=getattr(base, spec.field),
        overridden=getattr(effective, spec.field) != getattr(base, spec.field),
    )


@router.get("/settings", response_model=SettingsResponse)
async def list_settings(
    request: Request,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
):
    """Only the keys in RUNTIME_SAFE_SETTINGS are enumerable here, which is why
    no secret can leak through this endpoint: OPENAI_API_KEY has no entry, so
    there is nothing to filter out and nothing a new key can be added to by
    accident. `env_value` comes from app.state.settings - the values the process
    booted with - so the screen can show what removing an override would restore.
    """
    base: Settings = request.app.state.settings
    return SettingsResponse(
        settings=[_setting_response(spec, settings, base) for spec in RUNTIME_SAFE_SETTINGS.values()],
        env_only=[
            EnvOnlySettingResponse(key=item.key, label=item.label, reason=item.reason)
            for item in ENV_ONLY_SETTINGS
        ],
    )


def _spec_or_400(key: str) -> SettingSpec:
    spec = RUNTIME_SAFE_SETTINGS.get(key)
    if spec is None:
        # 400, not 404: the key may well exist as a `.env` value, and saying "not
        # found" would be a lie that sends an admin looking for a typo. This is
        # the refusal OPENAI_API_KEY gets, whatever case it is written in - the
        # lookup is exact, and nothing outside the spec table is reachable.
        raise HTTPException(status_code=400, detail=NOT_RUNTIME_SAFE_MESSAGE.format(key=key))
    return spec


@router.put("/settings/{key}", response_model=SettingResponse)
async def put_setting(
    key: str,
    payload: SettingUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Validated against the FULL settings object, not just this key's own range:
    CHUNK_OVERLAP has to stay under CHUNK_SIZE, and checking one at a time would
    let an admin save a pair that boots fine and then breaks every ingestion."""
    spec = _spec_or_400(key)
    base: Settings = request.app.state.settings
    overrides = await load_overrides(db)
    overrides[key] = payload.value
    try:
        effective = validated_settings(base, overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.execute(
        pg_insert(AppSetting)
        .values(key=key, value=payload.value)
        .on_conflict_do_update(
            index_elements=["key"], set_={"value": payload.value, "updated_at": func.now()}
        )
    )
    await db.commit()
    log_event(
        logger,
        "app_setting_changed",
        key=key,
        value=payload.value,
        admin_id=str(admin.id),
    )
    return _setting_response(spec, effective, base)


@router.delete("/settings/{key}", response_model=SettingResponse)
async def delete_setting(
    key: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Removes the override so the key falls back to its `.env` value. Idempotent
    - deleting a key with no override is a 200 describing the environment value,
    because the state the caller asked for is the state that now holds."""
    spec = _spec_or_400(key)
    base: Settings = request.app.state.settings
    await db.execute(delete(AppSetting).where(AppSetting.key == key))
    await db.commit()
    log_event(logger, "app_setting_reset", key=key, admin_id=str(admin.id))
    return _setting_response(spec, base, base)
```

- [ ] **Step 2: Modify `backend/app/schemas/chat.py`**

The caller's rating rides the transcript, so opening a conversation is one request rather than one per assistant message. The validator is what turns the relationship LIST into the single rating the client renders.

```python
    @field_validator("feedback", mode="before")
    @classmethod
    def _first_feedback(cls, value: object) -> object:
```

- [ ] **Step 3: Modify `backend/app/main.py`**

Wired in beside the other routers.

```python
    app.include_router(observability_router)
```

---

### Task 5: Tests

**Files:**
- Create: `backend/tests/test_observability.py`

Every guard here was made to fail before it was kept. The three easiest useless tests are called out in the file where they appear: an "empty table" test that relies on a fixture to empty the table, a "404 not 403" test that never checks the owner still gets a 200, and an "override applied" test that reads back the value it just wrote instead of the behaviour it changed.


- [ ] **Step 1: Write `backend/tests/test_observability.py`**

The cut-evidence test is self-calibrating: the first answer reports what each item cost and the budget is then set to fit exactly one of them, so it cannot quietly stop cutting anything the day the system prompt is edited.

```python
"""Slice 5: conversation trace, feedback, and runtime settings.

Every test here is a guard that was made to fail before it was kept. The three
that are easiest to write and useless are called out where they appear: an
"empty table" test that runs against a table somebody else seeded, a "404 not
403" test that never checks the code, and a "the override applied" test that
reads back the value it just wrote instead of the behaviour it changed.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from test_chat import make_fake_llm, parse_sse, vec

from app.chat.prompt import ANSWER_SYSTEM_PROMPT
from app.chat.service import answer, build_trace
from app.core.settings_store import (
    RUNTIME_SAFE_SETTINGS,
    SettingSpec,
    apply_overrides,
    effective_settings,
    validated_settings,
)
from app.core.tokens import count_tokens
from app.models.app_setting import AppSetting
from app.models.chunk import Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.feedback import MessageFeedback
from app.models.message import Message
from app.models.user import User
from app.retrieval.evidence import Evidence

pytestmark = pytest.mark.integration


# The words are in every chunk, so BOTH halves of hybrid retrieval find all three
# and every trace row carries a vector_rank AND a keyword_rank. A question whose
# words are absent from the corpus leaves keyword_rank null, which is legitimate
# and would make the "every stage is recorded" assertion vacuous.
QUESTION = "tomato blight"
CHUNK_TEXTS = [
    "tomato blight spreads through infected soil " * 12,
    "tomato blight is controlled by crop rotation " * 12,
    "tomato blight leaves brown lesions on fruit " * 12,
]


@pytest.fixture
def fake_llm(app):
    provider = make_fake_llm()
    app.state.llm_provider = provider
    return provider


@pytest_asyncio.fixture
async def owner(client, fake_llm, db):
    """The first account bootstraps admin, which is what the 고급 설정 tests need,
    and owns the conversations the 404 tests probe."""
    await client.post("/api/auth/register", json={"email": "owner@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "owner@example.com", "password": "pw123456"})

    user = await db.scalar(select(User).where(User.email == "owner@example.com"))
    collection = Collection(name="관측", created_by=user.id)
    db.add(collection)
    await db.flush()
    document = Document(
        collection_id=collection.id,
        filename="역병 방제.pdf",
        file_type="pdf",
        size_bytes=1,
        storage_path="x",
        status="indexed",
        uploaded_by=user.id,
    )
    db.add(document)
    await db.flush()
    db.add_all(
        [
            Chunk(
                document_id=document.id,
                chunk_index=index,
                content=content,
                token_count=count_tokens(content),
                char_count=len(content),
                page=index + 1,
                section=None,
                chunk_metadata={},
                # Identical vectors: this suite is about what happens AFTER
                # retrieval, and an ordering that depended on cosine noise would
                # make the cut-evidence assertions flap.
                embedding=vec(1.0),
            )
            for index, content in enumerate(CHUNK_TEXTS)
        ]
    )
    await db.commit()
    return client


@pytest_asyncio.fixture
async def other_client(app, client):
    """A second logged-in account. `client` first, so the account this one gets
    is never the bootstrap admin."""
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": "pw123456"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        await other.post("/api/auth/login", json={"email": "other@example.com", "password": "pw123456"})
        yield other


async def ask(client, message: str = QUESTION) -> dict:
    """The `done` frame, which now carries the assistant message id."""
    response = await client.post("/api/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return parse_sse(response.text)[-1]


# --- The trace ---------------------------------------------------------------


async def test_done_frame_carries_the_real_assistant_message_id(owner, db):
    done = await ask(owner)
    message = await db.get(Message, uuid.UUID(done["message_id"]))
    assert message is not None
    assert message.role == "assistant"


async def test_trace_shows_every_retrieval_stage_and_the_answer_metadata(owner):
    done = await ask(owner)
    trace = (await owner.get(f"/api/messages/{done['message_id']}/trace")).json()

    assert trace["has_trace"] is True
    assert trace["model"] == "gpt-4o"
    assert trace["prompt_name"] == "answer_agent"
    assert trace["prompt_version"] == "1"
    assert trace["latency_ms"] is not None and trace["retrieval_ms"] is not None
    assert trace["usage"] == {"total_tokens": 42}

    assert trace["retrieval"]["rrf_k"] == 60
    assert trace["retrieval"]["evidence_count"] == len(CHUNK_TEXTS)
    assert trace["retrieval"]["token_budget"] > 0

    item = trace["evidence"][0]
    # The four Slice 1 kept separate rather than collapsing into one score. If any
    # of them is missing here, the screen can show a total and nothing else.
    assert item["vector_rank"] is not None
    assert item["keyword_rank"] is not None
    assert item["rrf_score"] is not None
    assert "rerank_score" in item
    assert item["filename"] == "역병 방제.pdf"
    assert item["tokens"] > 0


async def test_the_trace_records_evidence_the_token_budget_cut(owner, db):
    """THE test of this slice. "Why did it not answer from the document I
    uploaded" is almost always "it was rank 3 and the budget stopped at 2", and
    before this nothing in the product recorded that at all.

    Self-calibrating rather than hard-coding a budget: the first answer reports
    what each item actually cost, and the budget is then set to fit exactly one
    of them. A hard-coded number would silently stop cutting anything the day the
    system prompt is edited, and this test would keep passing.
    """
    first = await ask(owner)
    trace = (await owner.get(f"/api/messages/{first['message_id']}/trace")).json()
    assert [item["included"] for item in trace["evidence"]] == [True] * len(CHUNK_TEXTS)

    mandatory = count_tokens(ANSWER_SYSTEM_PROMPT) + count_tokens(QUESTION)
    budget = mandatory + trace["evidence"][0]["tokens"] + 60
    put = await owner.put("/api/settings/ANSWER_CONTEXT_TOKEN_BUDGET", json={"value": str(budget)})
    assert put.status_code == 200, put.text

    second = await ask(owner)
    trace = (await owner.get(f"/api/messages/{second['message_id']}/trace")).json()
    included = [item for item in trace["evidence"] if item["included"]]
    cut = [item for item in trace["evidence"] if not item["included"]]
    assert included, "at least one item must still have reached the prompt"
    assert cut, "the budget was set to fit one item; the rest must be recorded as cut"
    assert trace["retrieval"]["included_count"] == len(included)
    # The cut item is still fully described - filename, page and the ranks that
    # explain why it was ordered where it was. Recording only that "something was
    # dropped" would answer none of the questions this screen exists for.
    assert cut[0]["filename"] == "역병 방제.pdf"
    assert cut[0]["page"] is not None
    assert cut[0]["snippet"]

    stored = await db.get(Message, uuid.UUID(second["message_id"]))
    assert stored.trace["retrieval"]["token_budget"] == budget


def test_build_trace_marks_cut_items_by_identity_not_by_position():
    """`build_prompt` happens to return a prefix, so `index < len(used)` would
    agree today. It is not asserted anywhere that it will keep doing so, and if
    it ever skips-and-continues the per-item scores would be attached to the
    wrong rows in silence."""
    evidence = [Evidence(source_type="rag", ref=f"chunk:{i}", content="x", score=0.1) for i in range(3)]
    trace = build_trace(
        evidence,
        [evidence[0], evidence[2]],
        settings=AsyncMock(
            retrieval_top_n=6,
            retrieval_candidate_limit=20,
            rrf_k=60,
            sparse_weight=1.0,
            answer_context_token_budget=8000,
        ),
        prompt=AsyncMock(name="p", version="1"),
    )
    assert [item["included"] for item in trace["evidence"]] == [True, False, True]


async def test_an_answer_written_before_the_trace_column_is_not_an_error(owner, db):
    """`{}` is what every message written before migration 0005 carries. The
    screen has to say "no trace" rather than 500."""
    done = await ask(owner)
    message = await db.get(Message, uuid.UUID(done["message_id"]))
    message.trace = {}
    await db.commit()

    trace = (await owner.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert trace["has_trace"] is False
    assert trace["evidence"] == []
    # The columns are real even when the JSON is not.
    assert trace["model"] == "gpt-4o"


async def test_another_users_trace_is_404_not_403(owner, other_client):
    """A 403 would confirm that the message id exists, which is the whole reason
    get_owned_conversation answers 404. Both halves are asserted: the OWNER gets
    200 on the same id, so a route that 404s for everybody would fail this."""
    done = await ask(owner)
    assert (await owner.get(f"/api/messages/{done['message_id']}/trace")).status_code == 200

    stolen = await other_client.get(f"/api/messages/{done['message_id']}/trace")
    assert stolen.status_code == 404
    assert stolen.json()["detail"] == "답변을 찾을 수 없습니다."
    # The same status for an id that never existed, so the two are indistinguishable.
    unknown = await other_client.get(f"/api/messages/{uuid.uuid4()}/trace")
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == stolen.json()["detail"]


async def test_a_user_turn_has_no_trace(owner, db):
    """Only an assistant answer has one, and asking for a user turn's must not
    leak that the id resolved to a real row."""
    done = await ask(owner)
    user_message = await db.scalar(
        select(Message).where(
            Message.conversation_id == uuid.UUID(done["conversation_id"]), Message.role == "user"
        )
    )
    assert (await owner.get(f"/api/messages/{user_message.id}/trace")).status_code == 404


async def test_trace_requires_auth(client):
    assert (await client.get(f"/api/messages/{uuid.uuid4()}/trace")).status_code == 401


# --- Feedback ----------------------------------------------------------------


async def test_feedback_is_one_per_user_per_message_and_changeable(owner, db):
    done = await ask(owner)
    url = f"/api/messages/{done['message_id']}/feedback"

    up = await owner.put(url, json={"rating": "up"})
    assert up.status_code == 200
    assert up.json()["rating"] == "up"

    down = await owner.put(url, json={"rating": "down", "comment": "근거가 엉뚱합니다."})
    assert down.status_code == 200
    assert down.json()["rating"] == "down"
    assert down.json()["comment"] == "근거가 엉뚱합니다."

    rows = (
        await db.scalars(
            select(MessageFeedback).where(MessageFeedback.message_id == uuid.UUID(done["message_id"]))
        )
    ).all()
    assert len(rows) == 1, "a changed rating must UPDATE, never insert a second row"
    assert rows[0].rating == "down"
    assert rows[0].updated_at > rows[0].created_at


async def test_feedback_rides_the_transcript_so_a_reload_still_shows_it(owner):
    done = await ask(owner)
    await owner.put(f"/api/messages/{done['message_id']}/feedback", json={"rating": "up"})

    messages = (await owner.get(f"/api/conversations/{done['conversation_id']}/messages")).json()
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["feedback"]["rating"] == "up"
    assert next(m for m in messages if m["role"] == "user")["feedback"] is None


async def test_feedback_joins_to_the_trace(owner, db):
    """The reason this table exists: "every down-vote since Tuesday, with the
    evidence its budget cut" has to be one query, not a log grep."""
    done = await ask(owner)
    await owner.put(f"/api/messages/{done['message_id']}/feedback", json={"rating": "down"})

    row = (
        await db.execute(
            select(MessageFeedback.rating, Message.trace)
            .join(Message, Message.id == MessageFeedback.message_id)
            .where(MessageFeedback.rating == "down")
        )
    ).one()
    assert row.rating == "down"
    assert row.trace["evidence"]


async def test_feedback_on_another_users_message_is_404(owner, other_client):
    done = await ask(owner)
    stolen = await other_client.put(f"/api/messages/{done['message_id']}/feedback", json={"rating": "up"})
    assert stolen.status_code == 404


async def test_feedback_rejects_a_rating_that_is_not_up_or_down(owner):
    done = await ask(owner)
    bad = await owner.put(f"/api/messages/{done['message_id']}/feedback", json={"rating": "meh"})
    assert bad.status_code == 422


async def test_feedback_requires_auth(client):
    posted = await client.put(f"/api/messages/{uuid.uuid4()}/feedback", json={"rating": "up"})
    assert posted.status_code == 401


# --- Runtime settings --------------------------------------------------------


async def _clear_overrides(db) -> None:
    """Explicitly, in the test. `clean_db` truncates app_settings between tests,
    but a "when the table is empty" test that relies on a fixture to empty it
    passes just as happily with its own guard deleted - which is how a
    prompt-admin test in this project went green over a hole. This is the line
    that makes the precondition the test's own."""
    await db.execute(delete(AppSetting))
    await db.commit()


async def test_an_empty_settings_table_behaves_exactly_like_the_environment(owner, db, app):
    await _clear_overrides(db)
    assert await db.scalar(text("SELECT count(*) FROM app_settings")) == 0

    base = app.state.settings
    listed = (await owner.get("/api/settings")).json()
    assert [s["key"] for s in listed["settings"]] == list(RUNTIME_SAFE_SETTINGS)
    for entry in listed["settings"]:
        spec = RUNTIME_SAFE_SETTINGS[entry["key"]]
        assert entry["overridden"] is False
        assert entry["value"] == entry["env_value"] == getattr(base, spec.field)

    # And the behaviour, not just the report: retrieval with no rows in the table
    # returns what RETRIEVAL_TOP_N says it should.
    results = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(results) == min(base.retrieval_top_n, len(CHUNK_TEXTS))


async def test_an_override_changes_behaviour_on_the_very_next_request(owner, db):
    """No restart, no cache to invalidate - the same property `get_prompt` has.

    Asserted on the RESULT of a search, not on the value read back from
    GET /api/settings: reading back what was just written proves the row exists
    and nothing about whether anything uses it.
    """
    await _clear_overrides(db)
    before = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(before) == len(CHUNK_TEXTS)

    put = await owner.put("/api/settings/RETRIEVAL_TOP_N", json={"value": "1"})
    assert put.status_code == 200
    assert put.json()["value"] == 1 and put.json()["overridden"] is True

    after = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(after) == 1

    # And removing the override puts it back, which is what makes 기본값으로
    # 되돌리기 a promise rather than a button.
    assert (await owner.delete("/api/settings/RETRIEVAL_TOP_N")).status_code == 200
    restored = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(restored) == len(CHUNK_TEXTS)


@pytest.mark.parametrize("key", ["OPENAI_API_KEY", "DATABASE_URL", "EMBEDDING_DIM", "EMBEDDING_MODEL"])
async def test_a_key_that_is_not_runtime_safe_is_refused(owner, key):
    """Including the two the screen explains rather than offers. EMBEDDING_MODEL
    and EMBEDDING_DIM need a migration and a full re-index; a control for them
    would corrupt the corpus quietly."""
    put = await owner.put(f"/api/settings/{key}", json={"value": "x"})
    assert put.status_code == 400
    assert key in put.json()["detail"]
    assert (await owner.delete(f"/api/settings/{key}")).status_code == 400


async def test_the_api_key_can_be_neither_read_nor_written(owner, db, app):
    """Structural, not a filter: RUNTIME_SAFE_SETTINGS has no entry for it, so
    there is nothing for a future key to be added to by accident. The env-only
    notes are checked too - they are rendered on screen."""
    assert "OPENAI_API_KEY" not in RUNTIME_SAFE_SETTINGS
    body = (await owner.get("/api/settings")).json()
    serialised = str(body)
    assert "OPENAI_API_KEY" not in serialised
    assert "openai_api_key" not in serialised
    if app.state.settings.openai_api_key:
        assert app.state.settings.openai_api_key not in serialised
    assert {item["key"] for item in body["env_only"]} == {"EMBEDDING_MODEL", "EMBEDDING_DIM"}
    assert all(item["reason"] for item in body["env_only"])

    # Even a row written straight into the table cannot make it readable or
    # applicable: load_overrides drops keys that are not in the spec table.
    db.add(AppSetting(key="OPENAI_API_KEY", value="sk-forged"))
    await db.commit()
    listed = await owner.get("/api/settings")
    # 200, not just "the value is absent": without the filter in load_overrides
    # this row reaches apply_overrides, raises KeyError inside the settings
    # dependency, and every request in the app becomes a 500.
    assert listed.status_code == 200
    assert "sk-forged" not in str(listed.json())
    assert (await owner.post("/api/search", json={"query": QUESTION})).status_code == 200


async def test_settings_are_admin_only(owner, other_client):
    assert (await other_client.get("/api/settings")).status_code == 403
    denied = await other_client.put("/api/settings/RETRIEVAL_TOP_N", json={"value": "2"})
    assert denied.status_code == 403
    assert (await other_client.delete("/api/settings/RETRIEVAL_TOP_N")).status_code == 403


async def test_settings_require_auth(client):
    assert (await client.get("/api/settings")).status_code == 401


async def test_a_value_outside_its_range_is_refused_in_korean(owner):
    for value in ("0", "999999", "abc"):
        refused = await owner.put("/api/settings/RETRIEVAL_TOP_N", json={"value": value})
        assert refused.status_code == 400
        assert any("가" <= ch <= "힣" for ch in refused.json()["detail"])


async def test_a_pair_that_only_breaks_together_is_refused(owner, db):
    """CHUNK_OVERLAP is in range on its own and invalid against CHUNK_SIZE. A
    per-key check would save it and every later ingestion would raise."""
    await _clear_overrides(db)
    assert (await owner.put("/api/settings/CHUNK_SIZE", json={"value": "400"})).status_code == 200
    refused = await owner.put("/api/settings/CHUNK_OVERLAP", json={"value": "800"})
    assert refused.status_code == 400
    assert await db.scalar(select(AppSetting.value).where(AppSetting.key == "CHUNK_OVERLAP")) is None


async def test_a_bad_row_is_ignored_rather_than_taking_answering_down(owner, db):
    """The read path never raises. A row that does not parse - only reachable by
    editing the table by hand - is dropped with a log, exactly as get_prompt
    falls back to the module constant."""
    await _clear_overrides(db)
    db.add(AppSetting(key="RETRIEVAL_TOP_N", value="not-a-number"))
    await db.commit()
    results = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(results) == len(CHUNK_TEXTS)


# --- The store on its own ----------------------------------------------------


async def test_effective_settings_returns_the_base_when_the_table_is_empty(db, app):
    await _clear_overrides(db)
    base = app.state.settings
    assert await effective_settings(db, base) is base


def test_apply_overrides_drops_an_invalid_chunk_pair_as_a_pair(app):
    """Keeping the half that parsed is not a repair: FixedChunking raises on
    overlap >= size, so a half-applied pair breaks ingestion instead of degrading
    it."""
    base = app.state.settings
    applied = apply_overrides(base, {"CHUNK_SIZE": "400", "CHUNK_OVERLAP": "800"})
    assert applied.chunk_size == base.chunk_size
    assert applied.chunk_overlap == base.chunk_overlap


def test_validated_settings_refuses_a_key_outside_the_spec_table(app):
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validated_settings(app.state.settings, {"OPENAI_API_KEY": "sk-forged"})


def test_every_spec_names_a_real_settings_field(app):
    """A typo in `field` would make a setting that saves, reports itself as
    applied, and changes nothing."""
    for spec in RUNTIME_SAFE_SETTINGS.values():
        assert hasattr(app.state.settings, spec.field), spec.key
        assert spec.minimum < spec.maximum


def test_a_spec_parses_and_bounds_its_own_value():
    spec = SettingSpec(
        key="X", field="rrf_k", kind=int, minimum=1, maximum=3, group="g", label="l", help="h"
    )
    assert spec.parse("2") == 2
    for bad in ("0", "4", "two", ""):
        with pytest.raises(ValueError):
            spec.parse(bad)


async def test_answer_still_takes_no_session(app, fake_llm):
    """The Slice 3 seam, re-checked from this slice: the trace is built inside
    answer() from what it already has, so nothing here grew a `db` parameter."""
    result = await answer(
        fake_llm,
        "question",
        [],
        [Evidence(source_type="rag", ref="chunk:1", content="evidence text", score=0.5)],
        settings=app.state.settings,
    )
    assert result.trace["evidence"][0]["included"] is True
    assert result.trace["version"] == 1
```

---

### Task 6: The trace dialog and the feedback controls

**Files:**
- Create: `frontend/components/chat/TraceDialog.tsx`
- Modify: `frontend/lib/types.ts`, `frontend/components/chat/MessageBubble.tsx`, `frontend/components/chat/ChatWindow.tsx`


- [ ] **Step 1: Write `frontend/components/chat/TraceDialog.tsx`**

A native `<dialog>` + `showModal()`, portalled to `<body>` — the focus trap, Escape, the inert background and top-layer stacking all come with it. The cut rows sit in the SAME table as the included ones, in retrieval order: the question being answered is "where was my document in the ranking", and a separate "dropped" section destroys exactly that.

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { MessageTrace, TraceEvidence } from "@/lib/types";

/** Why this answer looks the way it does.
 *
 * A native <dialog> + showModal(), the same pattern as CitationBadge and
 * ConfirmDialog: focus trap, Escape, an inert background and top-layer stacking
 * all come with it. Portalled to <body> because it is opened from inside the
 * transcript, where an ancestor's `overflow` would clip it.
 *
 * The cut rows are the reason this screen exists. They are rendered in the SAME
 * table as the included ones, in retrieval order, rather than in a separate
 * "dropped" section - the question being answered is "where was my document in
 * the ranking", and splitting the list destroys exactly that.
 */
function score(value: number | null, digits = 4): string {
  return value === null ? "—" : value.toFixed(digits);
}

function rank(value: number | null): string {
  // "—" for absent, not 0: a chunk the keyword search never returned has no
  // keyword rank, and a zero would read as "ranked zeroth".
  return value === null ? "—" : `${value}위`;
}

function EvidenceRow({ item }: { item: TraceEvidence }) {
  return (
    <tr className="border-b border-outline-variant align-top">
      <td className="px-3 py-3 text-label font-medium">{item.index}</td>
      <td className="px-3 py-3">
        <div className="font-medium">{item.filename ?? item.ref}</div>
        <div className="text-caption text-on-surface-variant">
          {item.page !== null ? `${item.page}쪽` : ""}
          {item.section ? ` · ${item.section}` : ""}
          {item.source_type !== "rag" ? ` · ${item.source_type}` : ""}
        </div>
        <p className="mt-1 line-clamp-2 text-caption text-on-surface-variant">{item.snippet}</p>
      </td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{rank(item.vector_rank)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{rank(item.keyword_rank)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{score(item.rrf_score)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{score(item.rerank_score)}</td>
      <td className="whitespace-nowrap px-3 py-3 tabular-nums">{item.tokens.toLocaleString()}</td>
      <td className="whitespace-nowrap px-3 py-3">
        {item.included ? (
          <span className="rounded-xs bg-primary-container px-2 py-1 text-caption font-medium text-on-primary-container">
            전달됨
          </span>
        ) : (
          // error-container, and the only place in this dialog that uses it:
          // nothing has failed, but this is the row that answers "why was my
          // document not used" and it has to be the thing the eye lands on.
          <span className="rounded-xs bg-error-container px-2 py-1 text-caption font-medium text-on-error-container">
            예산 초과로 제외
          </span>
        )}
      </td>
    </tr>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-sm bg-surface-container p-3">
      <dt className="text-caption text-on-surface-variant">{label}</dt>
      <dd className="mt-1 text-body font-medium text-on-surface">{value}</dd>
    </div>
  );
}

export default function TraceDialog({
  messageId,
  onClose,
}: {
  messageId: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [trace, setTrace] = useState<MessageTrace | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    dialogRef.current?.showModal();
    apiFetch<MessageTrace>(`/api/messages/${messageId}/trace`)
      .then(setTrace)
      .catch((err) => setError(errorMessage(err)));
  }, [messageId]);

  const cut = trace?.evidence.filter((item) => !item.included).length ?? 0;

  return createPortal(
    <dialog
      ref={dialogRef}
      aria-labelledby="trace-title"
      onClose={onClose}
      className="w-full max-w-4xl rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
    >
      <div className="max-h-[80vh] overflow-y-auto p-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 id="trace-title" className="text-title font-medium">
              답변 추적
            </h2>
            <p className="mt-1 text-caption text-on-surface-variant">
              이 답변이 어떤 근거로 만들어졌는지, 무엇이 모델에게 전달되지 않았는지 보여줍니다.
            </p>
          </div>
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            aria-label="닫기"
            className="icon-btn"
          >
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <div className="mt-4">
          <ErrorBanner message={error} />
        </div>

        {trace === null ? (
          !error && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
        ) : (
          <>
            <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Fact label="답변 모델" value={trace.model ?? "—"} />
              <Fact
                label="프롬프트 버전"
                value={trace.prompt_name ? `${trace.prompt_name} v${trace.prompt_version}` : "—"}
              />
              <Fact
                label="검색 시간"
                value={trace.retrieval_ms === null ? "—" : `${trace.retrieval_ms.toLocaleString()}ms`}
              />
              <Fact
                label="생성 시간"
                value={trace.latency_ms === null ? "—" : `${trace.latency_ms.toLocaleString()}ms`}
              />
              <Fact label="RRF 상수" value={trace.retrieval.rrf_k?.toString() ?? "—"} />
              <Fact
                label="키워드 가중치"
                value={trace.retrieval.sparse_weight?.toString() ?? "—"}
              />
              <Fact
                label="토큰 예산"
                value={trace.retrieval.token_budget?.toLocaleString() ?? "—"}
              />
              <Fact
                label="사용 토큰"
                value={
                  typeof trace.usage.total_tokens === "number"
                    ? trace.usage.total_tokens.toLocaleString()
                    : "—"
                }
              />
            </dl>

            {!trace.has_trace ? (
              <p className="mt-6 rounded-md bg-surface-container-high p-4 text-body text-on-surface-variant">
                이 답변에는 근거 추적 정보가 없습니다. 추적 기능이 추가되기 전에 생성된 답변입니다.
              </p>
            ) : (
              <>
                <div className="mt-6 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <h3 className="text-title font-medium">검색된 근거</h3>
                  <p className="text-caption text-on-surface-variant">
                    {trace.retrieval.evidence_count}개 중 {trace.retrieval.included_count}개가 모델에게
                    전달되었습니다.
                  </p>
                </div>
                {cut > 0 && (
                  // The one sentence this whole screen was built to be able to
                  // say. It is not an error banner - nothing went wrong - so it
                  // is a tonal block, per the design language's §1 and §4.
                  <p className="mt-3 rounded-md bg-surface-container-high p-4 text-body text-on-surface">
                    근거 {cut}개가 토큰 예산({trace.retrieval.token_budget?.toLocaleString()})을 넘어
                    모델에게 전달되지 않았습니다. 이 근거가 답변에 필요했다면 고급 설정에서 답변
                    컨텍스트 토큰 예산을 늘리세요.
                  </p>
                )}
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left text-body">
                    <caption className="sr-only">검색된 근거와 각 단계의 점수</caption>
                    <thead>
                      <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                        <th scope="col" className="px-3 py-3">#</th>
                        <th scope="col" className="px-3 py-3">출처</th>
                        <th scope="col" className="px-3 py-3">벡터</th>
                        <th scope="col" className="px-3 py-3">키워드</th>
                        <th scope="col" className="px-3 py-3">RRF</th>
                        <th scope="col" className="px-3 py-3">재순위</th>
                        <th scope="col" className="px-3 py-3">토큰</th>
                        <th scope="col" className="px-3 py-3">전달 여부</th>
                      </tr>
                    </thead>
                    <tbody>
                      {trace.evidence.map((item) => (
                        <EvidenceRow key={item.index} item={item} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </div>
    </dialog>,
    document.body,
  );
}
```

- [ ] **Step 2: Modify `frontend/lib/types.ts`**

The trace shapes, mirroring `app/schemas/observability.py` one for one.

```typescript
export interface TraceEvidence {
```

- [ ] **Step 3: Modify `frontend/lib/types.ts`**

`message_id` on the `done` frame.

```typescript
      message_id: string;
```

- [ ] **Step 4: Modify `frontend/components/chat/MessageBubble.tsx`**

Feedback state seeded from the server and owned locally afterwards: a click updates one row rather than refetching the conversation. 👎 opens the comment box and 👍 does not — a complaint is the one worth a sentence, and asking every satisfied user to type something is how a feedback control stops being clicked.

```tsx
  async function rate(rating: "up" | "down", withComment?: string) {
```

- [ ] **Step 5: Modify `frontend/components/chat/ChatWindow.tsx`**

The row id from the frame, not a fabricated one.

```tsx
                id: event.message_id,
```

---

### Task 7: The 고급 설정 screen

**Files:**
- Create: `frontend/app/(app)/settings/page.tsx`
- Modify: `frontend/components/layout/Sidebar.tsx`


- [ ] **Step 1: Write `frontend/app/(app)/settings/page.tsx`**

The server is what refuses a bad value, so no input is clamped and no save button is disabled: the Korean 400 renders under the field it belongs to. The 문서 분할 group carries the sentence an admin would otherwise learn the slow way, and the env-only block renders its reasons from the API.

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { RuntimeSetting, SettingsPayload } from "@/lib/types";

// The API returns a group key, not a heading. The copy belongs on the screen,
// and an unmapped group falls back to its key rather than to a blank so a
// setting added later is still visible.
const GROUP_TITLE: Record<string, string> = {
  retrieval: "검색과 답변",
  chunking: "문서 분할",
};

const GROUP_NOTE: Record<string, string> = {
  retrieval: "저장하면 다음 질문부터 바로 적용됩니다. 서버를 다시 시작할 필요는 없습니다.",
  chunking:
    "저장하면 앞으로 등록되는 문서에만 적용됩니다. 이미 색인된 문서의 청크는 바뀌지 않으며, 바꾸려면 그 문서를 다시 등록해야 합니다.",
};

function SettingRow({
  setting,
  onSaved,
}: {
  setting: RuntimeSetting;
  onSaved: () => Promise<void>;
}) {
  // Uncontrolled by the server once the admin has typed: a background reload
  // must not overwrite what is under the cursor. Re-seeded when the saved value
  // changes, which is what makes 되돌리기 update the box.
  const [draft, setDraft] = useState(String(setting.value));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setDraft(String(setting.value));
  }, [setting.value]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // The SERVER is what refuses a bad value, so the input is not clamped and
      // the button is never disabled on one: the Korean 400 renders under the
      // field. A disabled button would hide the guard instead of exercising it.
      await apiFetch(`/api/settings/${setting.key}`, {
        method: "PUT",
        body: JSON.stringify({ value: draft }),
      });
      await onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/settings/${setting.key}`, { method: "DELETE" });
      await onSaved();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="rounded-md bg-surface-container-low p-4">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3 className="text-body font-medium text-on-surface">{setting.label}</h3>
        <code className="text-caption text-on-surface-variant">{setting.key}</code>
        {setting.overridden && (
          <span className="rounded-xs bg-primary-container px-2 py-0.5 text-caption font-medium text-on-primary-container">
            변경됨
          </span>
        )}
      </div>
      <p className="mt-1 text-caption text-on-surface-variant">{setting.help}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor={setting.key} className="sr-only">
          {setting.label}
        </label>
        <input
          id={setting.key}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          inputMode="decimal"
          className="field w-32"
        />
        <button type="submit" disabled={busy} className="btn-filled btn-compact">
          {busy ? "저장 중..." : "저장"}
        </button>
        {setting.overridden && (
          <button type="button" onClick={() => void reset()} disabled={busy} className="btn-text btn-compact">
            기본값({setting.env_value})으로 되돌리기
          </button>
        )}
        <span className="text-caption text-on-surface-variant">
          허용 범위 {setting.minimum} ~ {setting.maximum}
          {setting.overridden ? ` · .env 값 ${setting.env_value}` : " · .env 값과 동일"}
        </span>
      </div>
      <div className="mt-2">
        <ErrorBanner message={error} />
      </div>
    </form>
  );
}

export default function SettingsPage() {
  // null is "not loaded yet", not "empty". GET /api/settings answers a non-admin
  // with 403 관리자 권한이 필요합니다., which lands in loadError, so this page
  // needs no role branch of its own - the same shape as 프롬프트 관리.
  const [payload, setPayload] = useState<SettingsPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setPayload(await apiFetch<SettingsPayload>("/api/settings"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const groups = [...new Set(payload?.settings.map((s) => s.group) ?? [])];

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">고급 설정</h1>
      <ErrorBanner message={loadError} />

      {payload === null ? (
        !loadError && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      ) : (
        <>
          {groups.map((group) => (
            <section key={group} className="space-y-3">
              <h2 className="text-title font-medium">{GROUP_TITLE[group] ?? group}</h2>
              {GROUP_NOTE[group] && (
                // Tone, not a rule: nothing has gone wrong, so this is a
                // surface-container-high block rather than a banner. It is the
                // sentence an admin who changes 청크 크기 and waits for the
                // corpus to change would otherwise learn the slow way.
                <p className="rounded-sm bg-surface-container-high p-4 text-body text-on-surface">
                  {GROUP_NOTE[group]}
                </p>
              )}
              {payload.settings
                .filter((s) => s.group === group)
                .map((setting) => (
                  <SettingRow key={setting.key} setting={setting} onSaved={load} />
                ))}
            </section>
          ))}

          <section className="space-y-3">
            <h2 className="text-title font-medium">여기서 바꿀 수 없는 값</h2>
            {/* Rendered from the API, not written into this file: the reason has
                to live beside the decision in the settings store, where the next
                person who wants to make one of these editable will read it. */}
            <p className="rounded-sm bg-surface-container-high p-4 text-body text-on-surface">
              아래 값들은 바꾸면 이미 저장된 데이터와 어긋나므로 환경변수(.env)로만 관리합니다. 화면에서
              바꿀 수 있게 두면 코퍼스가 조용히 망가집니다.
            </p>
            {payload.env_only.map((item) => (
              <div key={item.key} className="rounded-md bg-surface-container-low p-4">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <h3 className="text-body font-medium text-on-surface">{item.label}</h3>
                  <code className="text-caption text-on-surface-variant">{item.key}</code>
                </div>
                <p className="mt-1 text-caption text-on-surface-variant">{item.reason}</p>
              </div>
            ))}
          </section>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Modify `frontend/components/layout/Sidebar.tsx`**

Under the existing 관리 group, which is rendered for an admin only.

```tsx
    { href: "/settings", label: "고급 설정" },
```

---

## Verification

```
cd backend && python -m pytest          # own TEST_DATABASE_URL, one session, never -n auto
cd backend && python -m ruff check .
cd frontend && npx tsc --noEmit && npm run build && npm test
python scripts/check_plan_parity.py docs/superpowers/plans/2026-08-30-slice-5-observability.md
```

Then drive it: ask a real question, open 추적, and confirm at least one evidence row reads 예산 초과로 제외; rate the answer and change the rating; change a setting and see the next question behave differently with no restart; confirm another account's trace is a 404 and a non-runtime-safe key is refused.
