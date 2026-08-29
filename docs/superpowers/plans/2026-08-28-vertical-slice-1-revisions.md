# Vertical Slice 1 — Revision Changelog

Maps every finding from the two adversarial re-reviews to what changed in
`docs/superpowers/specs/2026-08-28-vertical-slice-1-design.md` (spec) and
`docs/superpowers/plans/2026-08-28-vertical-slice-1.md` (plan).

**Sources**
- `planning-*` → `.superpowers/sdd/2026-08-28-vertical-slice-1/planning-rereview.md`
- `code-*` → `.superpowers/sdd/2026-08-28-vertical-slice-1/code-rereview.md` (independent IDs)

**Scope note.** Only these three documents were edited. No file under `backend/`,
`worker/`, `frontend/`, or `docker-compose.yml` was touched; the already-committed
Tasks 1–3 code predates this revision and must be rebuilt from the revised plan.

**Task count: 20 → 24.** Task 9 split into 9 (chunking primitives) + 10
(strategies); a `VectorStore` task was added (12); chat split into 16 (prompt),
17 (`retrieve`/`answer`), 18 (routers); an end-to-end test task was added (19);
old Tasks 19+20 merged into 24. The File Structure section and the new
task→file map were rewritten to match.

**Totals: 96 findings addressed — 89 applied, 7 consciously not applied** (each
justified below, marked **NOT APPLIED**).

---

## Planning re-review — Critical

- **C1 — semantic chunker emits one chunk per PDF.** Rewritten as two tasks.
  Task 9 adds `split_sentences`, `split_to_token_limit` (sentence boundaries,
  then a tiktoken hard split for a single oversized sentence), and
  `build_size_bounded_candidates` — a real size pass that opens a new candidate
  at a heading **or** when the token limit would be exceeded, with incremental
  token accounting (no O(n²) re-encoding). Task 8's `PdfParser` now assembles
  paragraphs from single-`\n` lines and emits `heading` blocks via a conservative
  heuristic (numbered section / all-caps / short line followed by a blank line),
  with unit tests for both the accept and reject sides. New regression tests:
  a 40-block heading-less document must yield `>1` candidate with every
  `token_count <= max_chunk_tokens`, at the primitive level (Task 9) and through
  the full strategy (Task 10). Spec gained a "Chunking" section describing the
  two passes and why the order matters.
- **C2 — `docker compose up` dies on `localhost` hostnames.** Task 1's compose
  gives `migrate`/`backend`/`worker` explicit `environment:` overrides with
  `postgres`/`redis` hostnames that win over `env_file`, and `env_file` is
  `{path: .env, required: false}`. `.env.example` keeps `localhost` with a
  comment saying it is for host-side tooling only, and the unread
  `POSTGRES_HOST`/`POSTGRES_PORT` keys are gone. (Reconciled with code-C3 as one
  fix.)
- **C3 — path traversal via upload filename.** `storage_path()` is
  `<upload_dir>/<document_id>/source.<validated-ext>`; the client filename never
  contributes to the path and is kept only in `documents.filename` for display.
  Tests upload `"../../evil.txt"` and assert the file lands inside the upload
  root.
- **C4 — no authorization.** Spec gained an explicit authorization table; the
  plan adds `app/auth/authorization.py` with `get_owned_conversation` (404, not
  403) and `get_readable_document`, plus `require_admin`. Applied to
  `/api/conversations/{id}/messages`, `DELETE /api/conversations/{id}`, and the
  `conversation_id` branch of `/api/chat`. `upload_document` validates
  `collection_id` exists and returns 404. Tests: another user's conversation
  returns 404 on read and an `error` event on write.
  **Conflict with C10, resolved:** C4 implies per-user document isolation, C10
  implies one shared corpus with admin-only writes. Per-user document isolation
  would break citation click-through for everyone but the uploader and
  contradicts "answers drawn from the org corpus". Resolution: documents/chunks
  are readable by any authenticated user, writable by admin only; conversations
  and messages are strictly owner-only. Recorded in the spec's권한 model table.
- **C5 — `PendingRollbackError` on the failure path, non-idempotent retries.**
  Task 13's pipeline calls `await db.rollback()` before re-fetching the document
  and writing `failed`, and calls `vector_store.delete_by_document()` at the top
  of the try. New test injects a **DB** error (a 600-char `section` into
  `String(500)`) and asserts `failed` + `error_message`; another test asserts
  re-processing yields the same chunk count.
- **C6 — unbatched, untimed, unretried embeddings; double-embedding.**
  `OpenAIProvider` batches by item count **and** character budget,
  `AsyncOpenAI(timeout=…, max_retries=…)`, SDK errors wrapped in `LLMError`.
  Double-embed removed by carrying `ChunkCandidate.embedding`: the semantic
  strategy keeps the pass-1 vector for candidates it did **not** merge and sets
  `None` on merged ones; the pipeline only embeds the `None`s. Chosen over
  averaging merged members because averaging degrades vector quality.
- **C7 — missing `VectorStore`.** New Task 12: `VectorStore` ABC + `VectorItem`,
  `ScoredId`, `PgVectorStore`. The pipeline (Task 13) and the retrieval service
  (Task 15) talk only to it and never touch `Chunk.embedding`. `collection_ids`
  is a first-class `search()` parameter.
- **C8 — concurrency.** Provider built once in the lifespan and injected
  (`get_llm_provider`); `db_pool_size`/`db_max_overflow` in `Settings` and passed
  to `create_async_engine`; `/api/chat` uses three short sessions — retrieval,
  then **no session** across the LLM call, then a fresh session to persist.
- **C9 — Cloudflare Tunnel impossible.** Adopted the single-origin rewrite:
  `next.config.js` `rewrites()` proxies `/api/*` to `API_INTERNAL_URL`,
  `lib/api.ts` uses `API_BASE_URL = ""`. CORS never fires, `SameSite=Lax` is
  correct, nothing reaches the **client bundle**, one tunnel on :3000 exposes
  the app. Corrected during Task 20: this originally read "nothing is baked at
  build time", which is false of the **server** half and the overstatement hid a
  live bug. `next build` evaluates `rewrites()` once and writes the resolved
  destination into `.next/routes-manifest.json`; `next start` never re-reads
  `API_INTERNAL_URL`. Compose supplied it under `environment:` — measured to do
  nothing, leaving the container proxying to its own empty port 8000. It is now
  a Docker `ARG` passed under `build.args`.
  `cors_origins` still moved into `Settings` as a fallback for direct backend
  access.
- **C10 — admin never enforced, open registration.** `require_admin` gates
  `POST /api/collections`, `POST /api/documents`, `DELETE /api/documents/{id}`.
  First registered user becomes `admin` and gets a default collection (this is
  what makes the three-command quick start actually work);
  `ALLOW_SELF_REGISTRATION` defaults off in production. `scripts/create_admin.py`
  added for the seeded path. Tests cover the 403 and the admin path.
- **C11 — whole upload buffered; MIME never checked.** `save_upload_stream`
  streams in 1 MB pieces with a running counter and 413s past the limit, deleting
  the partial directory. `validate_upload_metadata` checks extension **and**
  `Content-Type` against a per-extension allowlist; `validate_magic_bytes` sniffs
  with `filetype` (pure Python — `python-magic` needs libmagic and breaks
  Windows). Tests cover oversize streaming rejection and HTML-renamed-as-PDF.
- **C12 — blocking I/O and CPU work on the event loop.** `anyio.to_thread` wraps
  every file write/read, `parser.parse` in both the pipeline and the
  `/structure` endpoint, and directory removal.

## Planning re-review — Important

- **I1 — ivfflat on an empty table.** HNSW (`m=16, ef_construction=64`), declared
  in `Chunk.__table_args__` and mirrored in the migration. (Reconciled with
  code-I2 / code-C2 as one fix.)
- **I2 — 3-dim vector into `Vector(1536)`.** All fake providers pad to
  `EMBEDDING_DIM`, imported from `app.models.chunk`, which reads
  `Settings.embedding_dim`.
- **I3 — weak prompt-injection defense.** Task 16: evidence goes in its **own**
  message inside a per-request nonce fence; `_strip_fence_markers` removes the
  nonce and any `<<…EVIDENCE…>>` sequence from chunk text; a reminder follows the
  closing fence; `sanitize_history` whitelists `role in {user, assistant}`. Test
  asserts a hostile chunk containing a forged closing fence cannot break out.
- **I4 — rerank after truncation; interface loses the score.** `hybrid_search`
  reranks `fused[:candidate_limit]` and truncates to `top_n` afterwards.
  `Reranker` now takes and returns `list[RetrievedChunk]` with a `rerank_score`
  field. Test: with `top_n=1`, a reversing reranker changes which chunk survives
  — impossible under the old order.
- **I5 — no collection/ownership scoping in retrieval.** `collection_ids` threads
  through `retrieve` → `hybrid_search` → `VectorStore.search` / `keyword_search`,
  joining `chunks → documents`. Exposed on `/api/chat` and `/api/search`.
- **I6 — citations wrong three ways.** `_citations_from` keeps only the indexes
  the model actually emitted; citations carry `filename`, `page`, `section`,
  `index`, `score`; `MessageBubble` parses `[n]` and renders inline
  `CitationBadge`s; `GET /api/chunks/{id}` added and the modal fetches the full
  chunk. (Also covers G7.)
- **I7 — no logging, usage discarded.** `app/core/logging.py` (JSON formatter,
  `request_id` contextvar, `log_event`) + pure-ASGI `RequestContextMiddleware`
  (pure ASGI so SSE is unaffected) + `duration_ms` logs at the embedding,
  chat-completion, retrieval, and pipeline boundaries. `messages` gained `model`,
  `prompt_name`, `prompt_version`, `usage`, `latency_ms`, `retrieval_ms`, all
  persisted by `persist_turn` and asserted in a test. (Also covers G3, G4,
  code-I21.)
- **I8 — non-deterministic history ordering.** `messages.created_at` uses
  `server_default=clock_timestamp()` instead of `now()`. Chose this over a `seq`
  column: one word, no extra column, and `compare_server_default` is off by
  default so it adds no drift risk. Test asserts stable
  user/assistant/user/assistant order across two turns.
- **I9 — `conversation.updated_at` never bumped.** `persist_turn` issues an
  explicit `UPDATE … SET updated_at = now()`. Test asserts a revived conversation
  floats to the top of `/api/conversations`.
- **I10 — `apiFetch` breaks multipart.** `...(typeof options.body === "string" ?
  {...} : {})`. `UploadDropzone` now uses `apiFetch` with `FormData`.
- **I11 — no frontend error handling / guard / logout / register / root route.**
  `middleware.ts` redirects to `/login`; `app/page.tsx` redirects to `/chat`;
  `Sidebar` shows the user and a logout button; `/register` page added;
  `ErrorBanner` and a `catch` at every call site.
- **I12 — CWD-relative `UPLOAD_DIR`.** `upload_dir: Path` with a validator
  absolutizing against `REPO_ROOT`; the lifespan `mkdir`s it; Compose sets
  `/app/data/uploads` explicitly. (Reconciled with code-I9 as one fix.)
- **I13 — `.sh` scripts and `/tmp`.** Both shell scripts deleted. Migrations run
  automatically via the one-shot `migrate` Compose service (no entrypoint script,
  so no CRLF exec hazard either); `scripts/smoke_test.py` uses `httpx` and
  `tempfile.gettempdir()`. `.gitattributes` with `* text=auto eol=lf` added.
  (Reconciled with code-Minor-8.)
- **I14 — `FixedChunking` crashes; loses location metadata.** `__init__`
  validates `0 <= overlap < chunk_size` (and `Settings` validates the same pair);
  block offsets are tracked so each window inherits the `page`/`section` of the
  block it starts in. Both tested.
- **I15 — hardcoded 1536.** `Settings.embedding_dim` → `EMBEDDING_DIM` in
  `models/chunk.py` → the migration → tests, plus a startup/readiness check
  comparing it against the deployed `atttypmod` and failing loudly. (Reconciled
  with code-I3.)
- **I16 — no streaming.** `POST /api/chat` is SSE from day one, emitting
  `status: searching` → `status: answering` → `citations` → `done`, with `token`
  reserved. `streamChat()` reader in `lib/api.ts`; `ChatWindow` shows the live
  status. (Also covers A6.)
- **I17 — no token budget.** `build_prompt` takes `token_budget`, fills evidence
  first, then history newest-first, and returns the evidence that actually fit so
  citations can only reference what the model saw. Two tests.
- **I18 — no FK indexes.** All five added in `0001_initial` and mirrored in the
  ORM, plus a test asserting no FK column is nullable. (Reconciled with
  code-I6 / code-C1.)
- **I19 — vacuous tests, nothing proves the slice works.** `test_all_tables_exist`
  and the empty-list chunk test deleted; new Task 19 `test_end_to_end.py` ingests
  a document with a deterministic provider, asserts it is retrievable via
  `/api/search`, that `/api/chat` produces a citation with real provenance, that
  the chunk text reached the fenced evidence message, and that the cited chunk is
  fetchable. Status-transition test added to `test_pipeline.py`.
- **I20 — no `pytest.ini`; test deps in production requirements.** `pytest.ini`
  is now an explicit Task 1 artifact; `requirements-dev.txt` split out and the
  Dockerfiles install only `requirements.txt`. (Reconciled with code-T1/T2/I18.)
- **I21 — silent enqueue failures; no arq timeouts.** Enqueue wrapped in
  try/except marking the document `failed` with a user-safe message;
  `WorkerSettings` sets `job_timeout=900`, `max_tries=2`, `keep_result`, and an
  `on_job_failure` hook that marks a timed-out document `failed`.
- **I22 — declared but unperformed compose change.** Replaced by the real
  `migrate` service in Task 1 (and referenced from Task 24).
- **I23 — no `.dockerignore`; host `node_modules` copied.** `.dockerignore`
  added; the frontend image uses `npm ci` with a committed lockfile and no host
  `npm install` is required before building.
- **I24 — worker module path disagreement.** Worker entrypoint moved to
  `backend/app/worker.py`; `arq app.worker.WorkerSettings` is identical in Docker
  and locally. `worker/` keeps only a thin Dockerfile.
- **I25 — password policy, 500 on long passwords, user enumeration.**
  `Field(min_length=8, max_length=72)`; `verify_password` returns `False` on a
  corrupt hash; `dummy_verify()` on the user-not-found branch; duplicate
  registration returns a generic message (test asserts "already" is absent).

## Planning re-review — Requirement Gaps

- **G1 — document table columns / search.** `GET /api/documents` returns
  `collection_name`, `uploader_email`, and `chunk_count` (correlated subquery, not
  N+1). `DocumentTable` renders all eight columns; the page has a filter input.
- **G2 — original-vs-chunk side-by-side.** `GET /api/documents/{id}/structure`
  re-parses the stored file in a thread; the detail page is a two-pane grid
  (`StructureViewer` | `ChunkViewer`). Chose re-parsing over a
  `documents.parsed_structure` JSONB column: no schema cost and no duplication of
  every document's full text.
- **G3 — logging structure.** See I7.
- **G4 — token usage / latency.** See I7.
- **G5 — chunking strategy not selectable.** `Settings.chunking_strategy` +
  `get_chunking_strategy(settings)`; the worker no longer hardcodes one. Tested.
- **G6 — retrieval params hardcoded.** `retrieval_top_n`,
  `retrieval_candidate_limit`, `semantic_similarity_threshold`,
  `max_chunk_tokens`, `chunk_size`, `chunk_overlap`,
  `answer_context_token_budget` all in `Settings` and `.env.example`.
- **G7 — no `/api/chunks`.** Added (`GET /api/chunks/{id}`).
- **G8 — no `/api/search`.** Added (`POST /api/search`, returns `Evidence`).
- **G9 — logout / registration UI.** See I11.
- **G10 — `shared/` never created.** **NOT APPLIED.** A Python↔TypeScript shared
  package costs a build step and a codegen pipeline for six small response
  shapes; the spec instead documents the 1:1 correspondence and names
  `openapi-typescript` as the escape hatch if drift becomes real. Recorded in the
  spec and in the README outline.
- **G11 — Human Approval / `risk_level`.** Slice 1 has no tools, so nothing was
  built. Carried forward as an explicit "Slice 2 memo" line in the spec's
  Extensibility Seams so the MCP tool registry ships `risk_level` in its first
  migration.

## Planning re-review — Architectural Concerns

- **A1 — `answer_question` shape.** Applied and it is the largest structural
  change. `Evidence(source_type, ref, content, score, metadata)` added;
  `hybrid_search` returns `list[Evidence]`; Task 17 splits `retrieve(...)` and
  `answer(question, history, evidence)`. Slice 3's Orchestrator produces
  `list[Evidence]` from a plan and calls the unchanged `answer()`.
- **A2 — `LLMProvider.chat` has no tool surface.** Signature is now
  `chat(messages: list[ChatMessage], *, temperature=0.2, tools=None, **kwargs)`
  and `ChatResult` carries `tool_calls` and `model`. `ChatMessage` restores the
  spec's typed messages the old plan had downgraded to `list[dict]`. A test
  proves tool calls surface even though Slice 1 always passes `tools=None`.
- **A3 — `VectorStore` blocks collection-scoped retrieval.** Covered by C7 + I5.
- **A4 — `SYSTEM_PROMPT` with no indirection.** `get_prompt(name) ->
  PromptTemplate(name, version, text)`; the call site already goes through it and
  already persists `prompt_name`/`prompt_version` on the assistant message.
- **A5 — per-stage scores collapsed.** `RetrievedChunk` keeps `vector_rank`,
  `keyword_rank`, `rrf_score`, `rerank_score` separately and they are carried
  into `Evidence.metadata`. Tested.
- **A6 — non-streaming contract.** See I16.
- **A7 — `role` unused.** See C10.

## Planning re-review — Over-Engineering cuts

- **X1 — parser registry with import side effects.** Replaced by a `PARSERS` dict
  and `get_parser` with a `KeyError → ValueError` wrapper. `supports()` deleted.
- **X2 — `Storage` ABC.** Deleted. `storage.py` is module functions
  (`save_upload_stream`, `read_upload`, `storage_path`, `document_dir`,
  `delete_document_files`). The demanded `VectorStore` exists instead.
- **X3 — `test_migration.py`.** Deleted; replaced by `test_schema.py` (see
  code-T5).
- **X4 — `API_BASE_URL_FALLBACK` export.** Deleted along with the raw-`fetch`
  workaround; the I10 fix removes the need.
- **X5 — client-side auto-creation of a "일반" collection.** Deleted. The default
  collection is seeded when the first admin registers (and by
  `scripts/create_admin.py`); the documents page has a real collection selector.
- **X6 — unconditional 3s polling.** Gated on a non-terminal status existing and
  on `document.hidden`.

## Planning re-review — "What's Actually Good" (preserved unchanged)

Task decomposition with `Interfaces: Consumes/Produces`; TDD ordering (write
test → run → expect FAIL → implement → expect PASS → commit); one commit per task
with literal `git add`/`git commit`; literal complete runnable code with no prose
placeholders; the Global Constraints header (expanded, not replaced); RRF as a
pure function with arithmetic-pinning tests (Task 14 is carried over verbatim);
Redis-backed opaque session cookies over JWT; structure-first parsing into typed
`Block`s; arq over Celery, one Redis, one Postgres; the `documents.status` state
machine with Korean labels and red `failed`; the frontend's flat/bordered visual
restraint; a hand-written Alembic migration creating the `vector` extension.

---

## Code re-review — Critical

- **C1 — 17 drifted columns.** `0001_initial` amended in place (never shipped
  past a dev DB): `nullable=False` on every FK and on
  `created_at`/`updated_at`/`chunk_metadata`/`citations`/`title`/`usage`.
- **C2 — next autogenerate drops both retrieval indexes.** Both declared in
  `Chunk.__table_args__` (plus `ix_chunks_document_id` and the
  `(document_id, chunk_index)` unique constraint); the raw `op.execute` is gone,
  replaced by `op.create_index(..., postgresql_using="hnsw", …)`.
- **C3 — compose cannot work end-to-end.** Same fix as planning-C2, plus
  `env_file: {required: false}` so a fresh clone with no `.env` still comes up.
- **C4 — module-global engine breaks across loops.** `make_engine(settings)` +
  `create_app()` + lifespan owning `engine`/`sessionmaker` on `app.state`;
  `get_db_session(request)` reads from there; explicit `pool_size`,
  `max_overflow`, `pool_recycle`; `dispose()` on shutdown. arq's `on_startup`
  does the equivalent in `ctx`.
- **C5 — Redis global singleton.** `make_redis(settings)` in the lifespan,
  `get_redis(request)` from `app.state`, `aclose()` on shutdown. A comment states
  `decode_responses=True` is sessions-only, and arq gets its own `ArqRedis` via
  `make_arq_pool` (also owned by the lifespan).
- **C6 — `Settings` loads none of `.env` from `backend/`.** `env_file` anchored
  to `REPO_ROOT` (with `backend/.env` as a second candidate), `upload_dir`
  absolutized, `model_validator` refusing production with an empty
  `OPENAI_API_KEY` or a default DB password. Eight `test_settings.py` tests added
  (there was no `Settings` test at all).

## Code re-review — Important

- **I1 — naive timestamps.** `DateTime(timezone=True)` everywhere, in both the
  ORM and the migration.
- **I2 — ivfflat.** See planning-I1.
- **I3 — hardcoded 1536.** See planning-I15.
- **I4 — no lifespan/shutdown.** See C4; `create_app()` added with
  `app = create_app()` kept at module scope so `uvicorn app.main:app` still works.
- **I5 — no `ON DELETE`.** `CASCADE` on `chunks.document_id`,
  `messages.conversation_id`, `documents.collection_id`, `conversations.user_id`;
  `RESTRICT` on `collections.created_by` and `documents.uploaded_by`. Mirrored in
  the ORM. `DELETE /api/documents/{id}` relies on the cascade.
- **I6 — no FK indexes.** See planning-I18; `UniqueConstraint(document_id,
  chunk_index)` added, which is what makes re-index idempotency enforceable.
- **I7 — hardcoded CORS.** `Settings.cors_origins` (+ `.env.example`), narrowed
  `allow_methods` and `allow_headers`. Largely moot after the same-origin proxy
  (planning-C9) but kept for direct backend access.
- **I8 — health endpoint checks nothing.** `/api/health` stays a cheap liveness
  probe; `/api/health/ready` does `SELECT 1`, `redis.ping()`, and the embedding-dim
  check, returning 503 on failure. Backend gained a compose `healthcheck:` and
  the frontend depends on `service_healthy`.
- **I9 — CWD-relative `upload_dir`.** See planning-I12; it is a `Path`, and Task 6
  uses `Path` joins with a server-chosen filename.
- **I10 — two-line `.gitignore`.** Expanded (bytecode, caches, venvs,
  `node_modules`, `.next`, `data/uploads/*` with a `.gitkeep` exception).
- **I11 — no migration step; missing `prepend_sys_path`.** `prepend_sys_path = .`
  added to `alembic.ini`; a one-shot `migrate` service runs `alembic upgrade head`
  with `depends_on: postgres: service_healthy`, and backend/worker depend on it
  with `service_completed_successfully`. **Partially applied:** the suggested
  `backend/pyproject.toml` + `pip install -e backend` was **not** adopted —
  `prepend_sys_path = .` and `pythonpath = .` in `pytest.ini` solve the same
  problem in two lines and without adding an install step to every documented
  command.
- **I12 — case-sensitive email uniqueness.** **Partially applied.** Emails are
  normalised to lowercase in the Pydantic schema *and* the auth service, and a
  `CHECK (email = lower(email))` constraint enforces the invariant at the DB
  level. The suggested functional unique index on `lower(email)` was **not**
  used: Alembic's `compare_metadata` reflects expression indexes unreliably and
  would produce a permanent false positive in the drift test (T5), which is the
  highest-value test in the project. Trade recorded here deliberately.
- **I13 — 0.0.0.0 ports, default credentials, no Redis password.** Ports bound to
  `127.0.0.1`; Redis gets `--requirepass ${REDIS_PASSWORD}` and the URL carries
  it; `Settings` refuses production with a default DB password.
- **I14 — no Redis persistence/healthcheck.** `--appendonly yes` plus a named
  `redisdata` volume and a `redis-cli ping` healthcheck; dependents switched to
  `condition: service_healthy`.
- **I15 — Dockerfiles reference non-existent files.** `worker/Dockerfile` no
  longer copies `worker/main.py` (the entrypoint is `app/worker.py`); Task 1's
  verification step is `docker compose config --quiet` rather than a build, and
  the build happens at Task 24 once `frontend/` exists.
- **I16 — `NEXT_PUBLIC_API_BASE_URL` unreachable at build time.** Superseded, not
  patched: the same-origin rewrite (planning-C9) removes the variable entirely,
  so no build arg is needed. `env_file` was also removed from the `frontend`
  service so `OPENAI_API_KEY`/`POSTGRES_PASSWORD`/`DATABASE_URL` never enter the
  most internet-exposed container. **Conflict with the review's suggested build-arg
  fix, resolved in favour of the single-origin design**, which fixes four
  problems at once instead of one.
- **I17 — no `MetaData` naming convention.** `NAMING_CONVENTION` added to `Base`,
  and every constraint in `0001_initial` is given the matching explicit name so
  the DB and the convention agree from day one.
- **I18 — missing dependencies.** Added `email-validator`, `filetype`,
  `sqlalchemy[asyncio]`, `ruff`; test deps split into `requirements-dev.txt`.
  **`mypy` NOT APPLIED:** clean mypy over SQLAlchemy 2.0 + pydantic-settings +
  the OpenAI SDK requires plugin and stub work that would gate all 24 tasks; ruff
  (with `E,F,I,UP,B,ASYNC`) is adopted instead and run at every task boundary.
- **I19 — Python 3.12 images vs 3.13 dev.** Images moved to `python:3.13-slim`
  and `requirements.txt` carries a comment recording why `asyncpg`/`tiktoken` are
  pinned above the original versions (Windows 3.13 wheels).
- **I20 — root containers, no `.dockerignore`.** `useradd -m -u 1000 app` +
  `USER app` in both Python images and `USER node` in the frontend image;
  `.dockerignore` added. Uploads moved from a host bind mount to a named
  `uploaddata` volume, which also removes the host-UID ownership problem and the
  risk of user documents entering the build context or git.
- **I21 — no structured logging / error handling / correlation.** See
  planning-I7. Additionally `documents.error_message` now holds a fixed
  user-facing Korean string; the traceback goes only to
  `logger.exception`, with a test asserting no traceback or filesystem path
  leaks into the column rendered in the UI.

## Code re-review — Test Quality

- **T1 — `@pytest.fixture` on an async generator.** Every async fixture is
  `@pytest_asyncio.fixture`. `asyncio_mode = auto` is kept as a **documented
  deliberate choice** and all now-redundant `@pytest.mark.asyncio` decorators are
  removed, so the file no longer says two contradictory things.
- **T2 — `pytest.ini` / `tests/__init__.py` as accidental mechanisms.**
  `pytest.ini` is a Task 1 artifact with `pythonpath = .`, `testpaths`,
  `asyncio_mode`, and a registered `integration` marker; `tests/__init__.py` is
  explicitly not created, with a comment in `pytest.ini` saying so.
- **T3 — suite breaks at Task 4.** Session-scoped `NullPool` test engine (a
  plain sync fixture, so it needs no session-scoped loop and cannot leave a
  connection bound to a dead one), function-scoped sessions, and
  `dependency_overrides` for `get_db_session` and `get_redis` so tests never
  touch a global.
- **T4 — `test_health.py` proves almost nothing.** Kept as the import/route-mount
  canary and joined by `test_ready_reports_ready_when_dependencies_work` against
  the real readiness checks.
- **T5 — `test_migration.py` does not test the migration.** Replaced by
  `test_schema.py`: `conftest` creates `mopan_test` and runs `alembic upgrade
  head` in-process, then `compare_metadata(...) == []`, plus explicit assertions
  for the `vector` extension, `content_tsv` being `GENERATED ALWAYS`, both chunk
  indexes with the expected access methods (`gin`, `hnsw`), no nullable FK
  column, the embedding width matching `Settings`, and a
  `downgrade → upgrade` round trip. `alembic/env.py` now honours
  `config.get_main_option("sqlalchemy.url")` so the test can retarget it.
- **T6 — missing tests for existing behaviour.** `test_settings.py` added (8
  tests); `get_db_session`/`get_redis` exercised through every router test via
  overrides; `downgrade()` covered by the round-trip test; a dedicated
  `mopan_test` database plus an autouse `TRUNCATE … CASCADE` fixture gives
  isolation.

## Code re-review — Minor

1. Redundant module-level `settings` in `db.py` — gone with the C4 rewrite.
2. `content_tsv: Mapped[str]` holding a TSVECTOR — retyped `Mapped[Any]` with a
   "DB-maintained, never written by application code" comment.
3. `DROP EXTENSION` on downgrade — removed, with a comment explaining that
   extensions are database-wide.
4. No `server_default` on `id` — **NOT APPLIED.** Every insert path in Slice 1
   goes through the ORM (which supplies `uuid.uuid4`), and
   `server_default=gen_random_uuid()` is a server default that `compare_metadata`
   would need `compare_server_default` enabled to see, adding drift-test surface
   for no current benefit. Revisit when a raw-SQL seed path exists.
5. Undocumented `'simple'` regconfig — documented in the plan next to both the
   model and the migration, with an explicit warning that Task 15's query side
   must use `plainto_tsquery('simple', …)` or it silently bypasses the GIN index.
6. Inconsistent `restart:` policies — `restart: unless-stopped` on every
   long-running service; `restart: "no"` on the one-shot `migrate`.
7. No `file_template` in `alembic.ini` — added (`%%(rev)s_%%(slug)s`).
8. No `.gitattributes` — added. See planning-I13.
9. `DOCUMENT_STATUSES` documentation-pretending-to-be-a-constraint — now a real
   `CHECK` constraint in both the ORM and the migration; the tuple is still
   exported for the frontend labels.
10. `Message.role` unconstrained — `CHECK (role in ('user','assistant'))` added,
    and `sanitize_history` enforces the same set on the way into a prompt.
11. `__pycache__` in the working tree — covered by the `.gitignore` expansion.

---

## Not applied — summary

| Finding | Reason |
|---|---|
| planning-G10 (`shared/` package) | Codegen/build cost outweighs six small shapes; documented correspondence + `openapi-typescript` escape hatch instead. |
| planning-G11 (`risk_level` column) | Slice 1 has no tools; carried as an explicit Slice 2 memo in the spec rather than a premature column. |
| code-I11 (`pyproject.toml` + editable install) | `prepend_sys_path` + `pythonpath` solve the same problem in two lines with no extra install step. |
| code-I12 (functional `lower(email)` unique index) | Alembic cannot reflect expression indexes reliably; it would permanently break the drift test. Replaced with app-level normalisation + a `CHECK` constraint. |
| code-I16 (`NEXT_PUBLIC_*` build arg) | Superseded by the same-origin rewrite proxy, which removes the variable entirely and fixes CORS, `SameSite`, and the two-tunnel problem in the same move. |
| code-I18 (mypy) | Requires plugin/stub work that would gate all 24 tasks; ruff adopted and enforced at every task boundary instead. |
| code-Minor-4 (`gen_random_uuid()` id defaults) | No raw-SQL insert path exists in Slice 1; adds drift-test surface for no benefit. |

## Conflicts resolved

1. **planning-C4 (per-user document isolation) vs planning-C10 (shared corpus,
   admin-only writes).** Resolved toward C10 for documents and toward C4 for
   conversations. Per-user document isolation would break citation click-through
   for every non-uploader and contradicts the binding requirement that answers
   draw on the organisation's corpus. Documented as an explicit authorization
   table in the spec.
2. **code-I16 (build-arg fix) vs planning-C9 (single-origin rewrite).** Resolved
   toward the rewrite proxy: it satisfies the binding Cloudflare Tunnel
   requirement, whereas the build arg only fixes one of the four stacked
   blockers.
3. **code-I12 (DB-enforced case-insensitive email) vs code-T5 (a clean
   `compare_metadata` drift test).** Resolved toward the drift test, which both
   reviews rank as the single highest-value test; the email invariant is enforced
   by a `CHECK` constraint plus service-layer normalisation, which alembic does
   compare cleanly.
