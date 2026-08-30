# MOPAN Slices 2–5 — Design

Slice 1 shipped and the roadmap in `2026-08-28-vertical-slice-1-design.md` stands.
This covers the rest. It is decisions, not prose: every section says what is being
built, what is deliberately not, and why.

Slice 4's prompt half already shipped early (`2026-08-30-prompt-admin.md`), so what
remains of it is agent management.

**The seams Slice 1 built for this are real and verified in code**, which is what
makes these additions rather than rewrites:

| seam | where |
|---|---|
| `tools: list[dict] \| None` on `LLMProvider.chat` | `backend/app/llm/base.py:61` |
| `ToolCall` / `ChatResult.tool_calls` | `backend/app/llm/base.py:11,48` |
| `SourceType = Literal["rag", "mcp", "attachment"]` | `backend/app/retrieval/evidence.py:9` |
| `retrieve()` / `answer()` split over `Evidence` | `backend/app/chat/service.py` |
| per-stage scores kept separate | `vector_rank`, `keyword_rank`, `rrf_score`, `rerank_score` |
| `get_prompt(name)` indirection | now DB-backed |

`"attachment"` was added to that union later and needed no change to `answer()`.
That is the proof the seam works; MCP goes in the same slot.

---

## Slice 2 — MCP Server Registry and tool calls

### Transport: HTTP only, stdio deferred

MCP defines stdio and HTTP transports. **Only HTTP (streamable HTTP / SSE) is
supported.** stdio means the backend spawns and supervises child processes inside
its container: a second lifecycle to manage, a sandbox question for every server a
user registers, and no answer for horizontal scaling. A web application that lets
any admin register a server must not be in the business of executing arbitrary
local binaries. Record the omission; revisit if a needed server is stdio-only.

### Data

`mcp_servers` — id, name, base_url, enabled, auth kind + secret, created_by, timestamps.
`mcp_tools` — id, server_id, name, description, input_schema (JSONB), **risk_level**,
enabled, discovered_at.

**`risk_level` exists from this table's first migration**, per the Slice 1 note:
"MCP tool registry는 첫 마이그레이션부터 risk_level 컬럼을 가져야 한다(Human Approval
구조의 최소 형태)". Values `read` / `write` / `destructive`. It is not decoration —
Slice 3's approval gate reads it, and adding it later would mean a migration plus a
re-discovery pass over every registered server.

Default on discovery is **`write`**, not `read`. An unclassified tool must not be
the cheap one: a server author's description is not a security boundary, and the
cost of mis-defaulting downward is an unattended destructive call.

### Secrets

An auth token is write-only over the API: accepted on create/update, never returned,
never logged, and redacted in any error. The list endpoint returns whether a token is
set, not the token. Encrypt at rest if a key is available; if not, say so plainly in
the admin UI rather than implying protection that is not there.

### Discovery

On registration and on demand, call `tools/list` and upsert `mcp_tools`. A tool that
disappears is marked disabled rather than deleted — `messages` will reference it, and
a foreign key into a vanished row is worse than a tombstone.

Discovery talks to a URL an admin supplied. That is an SSRF surface: the backend will
fetch whatever it is pointed at, including `169.254.169.254` and anything else on the
internal network. Block link-local and loopback by default with an explicit
`MCP_ALLOW_PRIVATE_NETWORKS` escape hatch for local development, and say which.

### Invocation and the security property that matters

A tool result becomes `Evidence(source_type="mcp", ref="mcp:{server}/{tool}", ...)`
and joins the same list RAG evidence goes into. It therefore inherits, structurally
rather than by promise, the nonce fence, `_strip_fence_markers` and the single
`ANSWER_CONTEXT_TOKEN_BUDGET`.

**A tool result is untrusted input from a third party.** It is strictly more
dangerous than a document, because a server the admin registered can return anything
on every call and can change what it returns between calls. It never becomes an
instruction. Test it the way attachment injection is tested: a tool returning
"ignore previous instructions" must get no further than a PDF saying the same.

Slice 2 ships **manual** invocation — the user picks a tool. Automatic selection is
Slice 3. That split is deliberate: it lets the untrusted-output path be tested before
a planner is deciding when to walk it.

---

## Slice 3 — Super Agent / Orchestrator

### Shape

`plan(question, available) -> ExecutionPlan` is one LLM call returning steps. A step
is a RAG search over named collections, or an MCP tool call with arguments. Steps
declare dependencies; independent steps run concurrently. Every step yields
`list[Evidence]`, they are concatenated, and the **unchanged** `answer()` is called.

`answer()` not changing is the acceptance test for this slice. If it needs to change,
the Slice 1 seam was wrong and that is worth knowing.

### Bounds, because a planner that loops is a bill

- Maximum steps per plan, and maximum total tool calls per question.
- A wall-clock budget for the whole plan, enforced with `asyncio.timeout`, the way
  `worker.py` already bounds ingestion at `PIPELINE_TIMEOUT`.
- A step that fails is recorded and the plan continues; a plan that yields no evidence
  falls back to plain RAG rather than answering ungrounded.
- The planner may only name servers, tools and collections that were passed to it.
  A hallucinated tool name is refused, not attempted.

### Human approval

A step whose tool is `risk_level` above the configured threshold **pauses** and asks.
The SSE contract already streams `status`; approval adds a frame the client answers.
An unattended destructive call is the failure this must not have, and `risk_level`
existing from Slice 2's first migration is what makes the gate possible.

### Streaming

Slice 1 emits `status: searching` then `answering`. Slice 3 emits per-step status —
this is what the requirement's "문서 검색 → 진단 → 결과 종합" asked for. `token` stays
reserved.

### Falling back

Super Agent is **opt-in per conversation**, selected like the model. Slice 1's direct
RAG path stays and stays the default until the orchestrator has measured better on the
eval set. A planner is a new failure mode; making it mandatory on day one means every
regression is now two systems deep.

---

## Slice 4 (remainder) — Agent management

An agent is a saved configuration: name, prompt (from the prompt store), allowed
collections, allowed MCP tools, model, enabled. The chat picks an agent the way it now
picks a model.

This is deliberately configuration, not code. The moment an agent needs custom logic it
stops being a row and becomes a deployment, and the platform's whole claim is that a
user assembles one without a deployment.

**An agent's allowed-tool list is a permission boundary, not a hint.** A plan step
naming a tool the agent does not carry is refused server-side. Otherwise "restrict this
agent to read-only tools" means nothing.

---

## Slice 5 — Observability, admin, advanced settings

### Conversation trace

Slice 1 already stores what this needs, separately and on purpose: `vector_rank`,
`keyword_rank`, `rrf_score`, `rerank_score` per evidence item, and `model`,
`prompt_name`, `prompt_version`, `latency_ms`, `retrieval_ms`, `usage` per assistant
message. Slice 5 renders it. Nothing new to capture for RAG; Slice 3 adds the plan and
its steps.

A trace shows, for one answer: the plan if there was one, each retrieval stage with its
scores, which evidence reached the prompt and which was truncated by the budget, the
model, the prompt version, and the timings.

**Truncated evidence is the interesting part.** "Why did it not answer from the document
I uploaded" is usually "it was rank 9", and today nothing can show that.

### Feedback

👍/👎 per assistant message, with the trace attached. Cheap to store, and the only
thing that turns "answers feel worse since Tuesday" into a query.

### Advanced settings

The `.env` values that are safe to change at runtime, in an admin screen, stored in the
database and read through an indirection the way prompts now are.

**Not everything belongs here.** `EMBEDDING_DIM` and `EMBEDDING_MODEL` require a
migration and a full re-index; they stay environment-only, and the screen says why
rather than offering a control that would corrupt the corpus. `RETRIEVAL_TOP_N`,
`RRF_K`, `SPARSE_WEIGHT`, `ANSWER_CONTEXT_TOKEN_BUDGET` and the chunking values are
safe to change — the chunking ones affect only documents ingested afterwards, and the
screen must say that too.

---

## Order

Slice 2 and Slice 5 are independent of each other and go first, in parallel. Slice 3
needs Slice 2's tools. Slice 4 needs Slice 3's shape.

Each slice gets its own plan file. Two agents appending to one plan already clobbered an
entire task in this project, and the parity checker printed DRIFT 0 over the hole.
