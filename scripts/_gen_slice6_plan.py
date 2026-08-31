#!/usr/bin/env python3
"""Emit docs/superpowers/plans/2026-08-31-workflow-engine.md from disk.

The plan is the durable transcription source, and `scripts/check_plan_parity.py`
compares every block in it against the file it names. Writing those blocks by
hand is how four parity claims in this project turned out to be false. So the
PROSE is authored here and the CODE is read off disk, which makes drift
impossible by construction rather than by care.

Run it again after any change to the files it names; it is idempotent.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs/superpowers/plans/2026-08-31-workflow-engine.md"


def whole(path: str, lang: str = "python") -> str:
    return f"```{lang}\n{(REPO / path).read_text(encoding='utf-8').rstrip()}\n```"


def between(path: str, start: str, end: str, lang: str = "python") -> str:
    """A verbatim slice of a file, from the line containing `start` up to but not
    including the line containing `end`. Read off disk, so a `Modify` step's
    snippet cannot drift from what is actually there."""
    text = (REPO / path).read_text(encoding="utf-8")
    i = text.index(start)
    j = text.index(end, i)
    return f"```{lang}\n{text[i:j].rstrip()}\n```"


PARTS: list[str] = []


def add(text: str) -> None:
    PARTS.append(text.strip("\n"))


add(
    """
# MOPAN — 워크플로우와 슈퍼 에이전트 (Slice 6) — Implementation Plan

> **Spec:** `docs/superpowers/specs/2026-08-31-slice-6-workflow-design.md`, settled with the
> product owner. **Scope: `backend/` only.** The composer's `@` menu and the canvas are another
> agent's; this plan reports the API contract they build against and touches no file under
> `frontend/`.

## The one sentence this slice is about

**"에이전트"는 은퇴한다.** 워크플로우 is a graph a person authored; 슈퍼 에이전트 is the mode where
the model authors one per question. **둘 다 같은 실행기를 지난다.** If a second execution path
appears anywhere in this diff, the slice has been undone — that is the design's central point and
its fifth acceptance criterion.

## Decisions

**One `validate_graph`, one `WorkflowRun`, one trace shape.** The planner's output stopped being
an `ExecutionPlan` and became a workflow graph, so `app/orchestrator/plan.py` and
`app/orchestrator/executor.py` were **deleted** rather than kept beside the new code. A
compatibility shim accepting both shapes would have been the second execution path in disguise.
`app/orchestrator/approval.py` moved to `app/workflow/approval.py` **byte for byte** — the pause is
reused as it stands, not redesigned.

**A reference is a path evaluation, never a string substitution.** `"{{검색.top.title}}"` is a
reference; `"제목: {{검색.top.title}}"` is a template and is refused **at save**. Under substitution
the next tool's argument would be a string a third-party MCP server wrote most of. A resolved value
must be one scalar and is capped at 2000 characters. `app/workflow/expr.py`, and **there is no
`eval` in it**.

**Branch conditions are JSON, not a string grammar.** The only thing that genuinely needs parsing
is the reference, so that is the only parser written: `{"kind": "compare", "left": "{{a.count}}",
"op": ">", "right": 0}` is what the canvas renders as `{{a.count}} > 0`. The structural set is
`== != > >= < <=`, `exists`, `empty`, `and`, `or`, `not`. **`kind: "llm"` is in the schema and is
refused at save** — a branch that costs a model call per question gets switched on by an owner who
can see the price.

**There is deliberately no third "허용 워크플로우" allow-list table.** Section 5 of the spec asks the
슈퍼 에이전트 setting to scope-check workflows as well as collections and tools. A list of NAMES
would say which boundaries you may delegate to; `AvailableResources.narrow` intersects the caller's
catalogue with the callee's instead, which says what can actually be reached. A workflow restricted
to 분류 A therefore cannot reach 분류 B by calling a workflow that carries B — strictly stronger than
the list, and one method instead of a table, a migration, a CRUD field and a UI list.

**The active version lives on the version row.** `WorkflowVersion.is_active` with a partial unique
index, exactly as `prompts` already does it — not an `active_version_id` on `workflows`, which
would be a nullable FK (a third entry in `tests/test_schema.py:NULLABLE_FK_EXCEPTIONS`) and a
circular constraint alembic can only create with `use_alter`.

**Two new settings, and the four `orchestrator_*` bounds keep their names.** They now bound ONE
executor whoever authored the graph. `WORKFLOW_MAX_NODES` exists because `input`, `answer` and
`branch` are not steps and cost nothing, so a ceiling of 5 steps would refuse an ordinary
four-search canvas. `WORKFLOW_MAX_DEPTH` exists because a workflow can call a workflow. Renaming
the other four would silently revert a deployment to the default for no gain: an operator's `.env`
is not where the 에이전트 rename buys anything.

**One deviation from the spec, stated out loud.** Section 6 says an existing agent's allowed MCP
tools should become parallel `tool` nodes in its converted graph. Migration 0010 **does not do
that**, and the same paragraph's stronger claim — 동작이 바뀌지 않는 변환 — is why. An agent's tool
list is a PERMISSION list: nothing called those tools automatically before, so turning them into
nodes would call every one of them on every question. And an MCP node needs arguments, of which
there are none to write; a `write`-classified tool invoked with `{}` on every question is exactly
the unattended call the approval gate exists to prevent. The tools stay in `workflow_tools` as the
boundary, and an admin adds a node with real arguments on the canvas.

## The API contract the frontend builds against

| method | path | who | body / notes |
|---|---|---|---|
| `GET` | `/api/workflows` | admin | list, each with its active graph |
| `POST` | `/api/workflows` | admin | `WorkflowCreate`; `graph` optional, seeded when omitted → 201 |
| `GET` | `/api/workflows/{id}` | admin | the row **and** the active graph, one request |
| `PATCH` | `/api/workflows/{id}` | admin | `WorkflowUpdate`; the graph is **not** here |
| `DELETE` | `/api/workflows/{id}` | admin | 204; versions cascade, messages do not |
| `GET` | `/api/workflows/selectable` | any user | the `@` menu's workflow entries |
| `GET` | `/api/workflows/{id}/versions` | admin | newest first, the 되돌리기 list |
| `POST` | `/api/workflows/{id}/versions` | admin | `{graph, note}` → 201, becomes active |
| `POST` | `/api/workflows/{id}/versions/{version}/activate` | admin | rollback |
| `GET` | `/api/tools` | any user | **one list**: `rag`, `mcp:서버/도구`, `workflow:이름` |

`POST /api/chat` takes `workflow_id` where it took `agent_id`. The `done` frame and
`GET /api/conversations/{id}/messages` carry `workflow_name` and `workflow_version` where they
carried `agent_name`. `GET /api/messages/{id}/trace` carries the same two plus
`plan.author` — `"사람"` or `"슈퍼 에이전트"`.
"""
)

# ---------------------------------------------------------------- Task 1
add(
    """
---

### Task 1: expressions, the catalogue, and the graph that refuses itself

**Goal.** The two files nothing else can be built without: the reference/condition evaluator that
has no `eval` in it, and the validator that both authors go through.

**Why this order.** `validate_graph` is the boundary, and every later task is a caller of it. It is
also where the fourth acceptance criterion lives — *그래프가 허용 밖 도구를 참조하면 저장 시점에
거부한다* — so it is the file to get right before anything can save.
"""
)
add("- [ ] **Step 1: Create `backend/app/workflow/__init__.py`**")
add("```python\n```")
add("- [ ] **Step 2: Write `backend/app/workflow/expr.py`**")
add(whole("backend/app/workflow/expr.py"))
add("- [ ] **Step 3: Write `backend/app/workflow/catalogue.py`**")
add(whole("backend/app/workflow/catalogue.py"))
add("- [ ] **Step 4: Write `backend/app/workflow/graph.py`**")
add(whole("backend/app/workflow/graph.py"))
add("- [ ] **Step 5: Modify `backend/app/core/config.py` — the two new bounds**")
add(
    between(
        "backend/app/core/config.py",
        "    planner_model: str = \"\"\n    # Slice 6.",
        "\n    @property\n    def selectable_models",
    )
)
add("- [ ] **Step 6: Modify `backend/app/core/config.py` — their validators**")
add(
    between(
        "backend/app/core/config.py",
        "        # 3 is the floor, not 1:",
        "        # A typo here is the one that matters",
    )
)
add("- [ ] **Step 7: Modify `.env.example`**")
add(
    between(
        ".env.example",
        "# Slice 6. The five settings above now bound ONE executor",
        "\n# Which model writes the plan.",
        lang="text",
    )
)

# ---------------------------------------------------------------- Task 2
add(
    """
---

### Task 2: the Tool interface, and the ONE executor

**Goal.** `Tool` with three implementations, all returning `list[Evidence]`; and `WorkflowRun`,
which is the only thing in this codebase that runs a graph.

**Why RAG is not an MCP server.** The owner's phrasing was *"RAG를 수행하는 MCP"* and the intent is
right — everything callable must look the same to the planner and the executor. But MCP is a
transport, not the definition of a tool: making retrieval a real MCP server puts an HTTP round
trip, a serialisation and an auth handshake in front of a search measured at 269ms in-process.
Uniformity comes from the interface.

**The wall clock is applied per WAVE against ONE deadline.** The orchestrator already paid for the
alternative: an `asyncio.timeout` that fires while an async generator is suspended at a `yield`
cancels the CONSUMER's task and escapes as a bare `CancelledError`, killing the SSE stream instead
of ending the run. Nothing is yielded inside the block.
"""
)
add("- [ ] **Step 1: Write `backend/app/workflow/tools.py`**")
add(whole("backend/app/workflow/tools.py"))
add("- [ ] **Step 2: Write `backend/app/workflow/executor.py`**")
add(whole("backend/app/workflow/executor.py"))
add(
    "- [ ] **Step 3: Modify `backend/app/schemas/observability.py` — one trace shape, "
    "with the author as a field**"
)
add(
    between(
        "backend/app/schemas/observability.py",
        "class TracePlan(BaseModel):",
        "\nclass TraceResponse",
    )
)
add("- [ ] **Step 4: Modify `backend/app/observability/router.py`**")
add(
    between(
        "backend/app/observability/router.py",
        "        workflow_name=message.workflow_name,",
        "\n        prompt_name=",
    )
)

# ---------------------------------------------------------------- Task 3
add(
    """
---

### Task 3: `agents` → `workflows`, versioned graphs, and every existing row converted

**Goal.** The rename through the database, and the storage the canvas needs.

**Why `ALTER TABLE ... RENAME` and not create-copy-drop.** It keeps every row, every id and every
foreign key that points at one, so `messages` written before the migration still name the same
thing afterwards. Constraints and indexes carry their old names through a table rename, so every
one of them is renamed too — a constraint still called `fk_agent_tools_agent_id_agents` is the
confusion this migration exists to remove.

**`agents.orchestrator` is dropped.** It is the column that mixed the layers: "a fixed procedure"
was switching on "autonomous planning". 슈퍼 에이전트 is a per-conversation choice and nothing else
now. A deployment that had an agent with `orchestrator = true` loses that default and turns the
composer's toggle on instead; that is the spec's instruction and it is stated here so nobody
rediscovers it as a bug.
"""
)
add("- [ ] **Step 1: Write `backend/app/models/workflow.py`**")
add(whole("backend/app/models/workflow.py"))
add("- [ ] **Step 2: Modify `backend/app/models/__init__.py`**")
add(
    between(
        "backend/app/models/__init__.py",
        "from app.models.workflow import (",
        "\n__all__",
    )
)
add("- [ ] **Step 3: Modify `backend/app/models/message.py`**")
add(
    between(
        "backend/app/models/message.py",
        "    # WHICH WORKFLOW ANSWERED,",
        "\n    prompt_name:",
    )
)
add("- [ ] **Step 4: Write `backend/alembic/versions/0010_workflows.py`**")
add(whole("backend/alembic/versions/0010_workflows.py"))
add("- [ ] **Step 5: Modify `backend/tests/conftest.py` — the truncation list**")
add(
    between(
        "backend/tests/conftest.py",
        "    # Before `collections` and `mcp_tools`",
        "\n    \"chunks\",",
    )
)

# ---------------------------------------------------------------- Task 4
add(
    """
---

### Task 4: the API

**Goal.** `/api/workflows`, its versions, and `/api/tools` — the one menu `@` opens.

**Why the graph is saved by POSTing a version and not by PATCHing the workflow.** Every save makes
a version, and a PATCH that silently created one would hide that. The rollback is
`POST .../versions/{n}/activate`, which activates an existing row rather than copying it forward,
so the history stays a history instead of growing a duplicate on every rollback.

**Why `activate` does not re-validate.** A version that was refused never got saved, and
re-validating on rollback would fail precisely when an admin has disabled a tool — which is the
moment somebody wants to roll back. The run-time boundary still holds: `validate_graph` runs again
on every question.
"""
)
add("- [ ] **Step 1: Write `backend/app/schemas/workflow.py`**")
add(whole("backend/app/schemas/workflow.py"))
add("- [ ] **Step 2: Write `backend/app/workflow/router.py`**")
add(whole("backend/app/workflow/router.py"))
add("- [ ] **Step 3: Modify `backend/app/main.py` — register the router**")
add(
    between(
        "backend/app/main.py",
        "    from app.attachments.router import router as attachments_router",
        "\n    return app",
    )
)
add("- [ ] **Step 4: Modify `backend/app/mcp/service.py` — the boundary object it takes**")
add(
    between(
        "backend/app/mcp/service.py",
        "from app.workflow.catalogue import (",
        "\nlogger = logging",
    )
)

# ---------------------------------------------------------------- Task 5
add(
    """
---

### Task 5: 슈퍼 에이전트 emits a graph

**Goal.** The planner returns a `WorkflowGraph`, validated by the same function the canvas's save
goes through.

**Why a second prompt seed rather than an edit.** `PLANNER_SYSTEM_PROMPT` is what version 1 said and
version 1 is still in `prompts` for an admin to read; a migration is a historical record. Version
2 is seeded active by 0011, and the module fallback moves to it as well — a deployment whose
`prompts` table is empty must still get a planner that produces something the executor will run.

**`approval.py` moved and did not change.** Single-use token, `GETDEL`, burned on refusal, names in
Redis rather than resolved objects. It was already right.
"""
)
add("- [ ] **Step 1: Modify `backend/app/chat/prompt.py` — the graph planner prompt**")
add(
    between(
        "backend/app/chat/prompt.py",
        "# Slice 6. THE PLANNER EMITS A WORKFLOW GRAPH",
        "\n@dataclass(frozen=True)",
    )
)
add("- [ ] **Step 2: Modify `backend/app/chat/prompt.py` — the fallback entry**")
add(
    between(
        "backend/app/chat/prompt.py",
        "    # Version 2 as of Slice 6",
        "\n_ACTIVE_PROMPT_SQL",
    )
)
add("- [ ] **Step 3: Write `backend/app/workflow/planner.py`**")
add(whole("backend/app/workflow/planner.py"))
add("- [ ] **Step 4: Write `backend/alembic/versions/0011_planner_graph_prompt.py`**")
add(whole("backend/alembic/versions/0011_planner_graph_prompt.py"))
add(
    "- [ ] **Step 5: Delete the rest of `app/orchestrator/` and all of `app/agents/`**"
)
add(
    "```bash\n"
    "git rm backend/app/orchestrator/plan.py backend/app/orchestrator/executor.py \\\n"
    "       backend/app/orchestrator/planner.py backend/app/orchestrator/__init__.py \\\n"
    "       backend/app/agents/router.py backend/app/agents/service.py \\\n"
    "       backend/app/agents/__init__.py backend/app/models/agent.py \\\n"
    "       backend/app/schemas/agent.py\n"
    "```"
)

# ---------------------------------------------------------------- Task 6
add(
    """
---

### Task 6: one execution path in the chat router

**Goal.** `/api/chat` runs a graph. Where the graph came from is one `if`, and everything after it
is shared.

**Why a saved workflow and 슈퍼 에이전트 never both run on one turn.** 슈퍼 에이전트 is a way of
AUTHORING a graph. With both selected the model writes the graph and the workflow supplies the
boundary, the prompt and the answer model — which is exactly what the spec's section 5 reduced the
setting to.

**Why a saved graph is re-validated on every question.** It was valid when it was saved; an admin
may have disabled a tool since. A refusal here is not a 400 — the question is still answerable from
the direct path, so it is recorded in the trace and the fallback runs, the same posture a refused
plan has had since Slice 3.
"""
)
add(
    "- [ ] **Step 1: Write `backend/app/workflow/approval.py`** — this is "
    "`backend/app/orchestrator/approval.py` moved with `git mv` and **not edited**; the whole file "
    "is transcribed here because it is the pause this slice reuses as it stands, and because a "
    "task whose steps are all `Modify` has no `Write` for `check_plan_parity.py` to anchor on and "
    "is silently skipped whole."
)
add(whole("backend/app/workflow/approval.py"))
add("- [ ] **Step 2: Modify `backend/app/schemas/chat.py` — the request and the transcript**")
add(
    between(
        "backend/app/schemas/chat.py",
        "    # THE WORKFLOW, chosen per question",
        "\nclass ApprovalDecision",
    )
)
add("- [ ] **Step 3: Modify `backend/app/chat/service.py` — the boundary and what is persisted**")
add(
    between(
        "backend/app/chat/service.py",
        "async def retrieve(",
        "\ndef _citations_from",
    )
)
add("- [ ] **Step 4: Modify `backend/app/chat/router.py` — the pause frame**")
add(between("backend/app/chat/router.py", "async def _pause_frame(", "\nasync def _complete("))
add("- [ ] **Step 5: Modify `backend/app/chat/router.py` — the graph phase**")
add(
    between(
        "backend/app/chat/router.py",
        "            # Phase 1: the GRAPH.",
        "\n            # Phases 2 and 3.",
    )
)
add("- [ ] **Step 6: Modify `backend/app/chat/router.py` — `POST /api/chat/approve`**")
add(
    between(
        "backend/app/chat/router.py",
        "    stored = await consume_pending(",
        "\n    async def stream() -> AsyncIterator[str]:\n        try:\n            run = WorkflowRun(",
    )
)

# ---------------------------------------------------------------- Task 7
add(
    """
---

### Task 7: tests, one incidental bug, and the plan checker

**Goal.** A test for every guard, each written to fail without its guard.

**The incidental fix.** `FENCE_RESERVE_TOKENS` was 59 and the fence carries a random 16-character
nonce TWICE, so the real charge ranges 49–69 depending on the draw. The reserve was under the
charge on about one request in five, and
`test_the_fence_reserve_still_matches_what_build_prompt_charges` failed at the same rate — it was
flaky, not stale, which is why re-running made it go away. 71 is the arithmetic upper bound (39
tokens of fence, plus one token per nonce character worst case, twice), not the largest number
anybody happened to observe.
"""
)
add("- [ ] **Step 1: Write `backend/tests/test_workflow_engine.py`**")
add(whole("backend/tests/test_workflow_engine.py"))
add("- [ ] **Step 2: Write `backend/tests/test_workflows.py`**")
add(whole("backend/tests/test_workflows.py"))
add("- [ ] **Step 3: Modify `backend/app/retrieval/neighbors.py` — the fence reserve**")
add(
    between(
        "backend/app/retrieval/neighbors.py",
        "# 71, not 59,",
        "\n# One evidence item's",
    )
)
add(
    "- [ ] **Step 4: Modify `scripts/eval_retrieval.py`** — it imported `PlanRun` and "
    "`validate_plan` directly, on purpose, so that the eval measures the shipped objects rather "
    "than a re-implementation. That is still the rule; the objects moved."
)
add(
    between(
        "scripts/eval_retrieval.py",
        "    from app.retrieval.keyword_search import keyword_search",
        "\n    async with maker() as session:",
    )
)
add("- [ ] **Step 5: Modify `scripts/check_all_plans.py`**")
add(between("scripts/check_all_plans.py", "PLANS = [", "\nmissing = ["))

OUT.write_text("\n\n".join(PARTS) + "\n", encoding="utf-8")
print(f"wrote {OUT} ({len(OUT.read_text(encoding='utf-8').splitlines())} lines)")
