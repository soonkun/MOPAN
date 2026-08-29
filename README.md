# MOPAN

MOPAN is the **base system** of a general-purpose RAG · MCP · LLM · Agent platform — not a chatbot for one domain. Users register and combine their own RAG collections, MCP servers, LLMs and agents, and a Super Agent decides per question which of them to use. The name is the Korean 모판, the seedling tray: one base, transplanted into many fields.

**Slice 1, this repository's current state, delivers one complete vertical path:** login → document ingestion → semantic chunking → embedding → hybrid retrieval → RRF fusion → a cited answer. Everything is behind an interface (`VectorStore`, `Parser`, `ChunkingStrategy`, `Reranker`, `LLMProvider`) so the later slices add implementations rather than rewrite this one.

## Architecture

A browser talks to **one origin**, the Next.js server on port 3000. Next serves the React app and proxies `/api/*` to **FastAPI** on port 8000 through `rewrites()`, so the API is same-origin: no CORS in normal operation, and a session cookie that needs no cross-site handling. FastAPI owns **Postgres 16 + pgvector** (users, collections, documents, chunks, conversations, and both retrieval indexes) and **Redis 7** (sessions, and the arq job queue). An **arq worker** runs off that same Redis and owns the ingestion pipeline — parse → chunk → embed → index — so an upload returns `202` immediately and the browser polls for status. Retrieval runs dense (pgvector HNSW, cosine) and sparse (Postgres full-text, GIN) in parallel and fuses them with Reciprocal Rank Fusion before the answer is composed.

## Quick start (Docker)

```
git clone <repo> && cd MOPAN
cp .env.example .env      # then set OPENAI_API_KEY
docker compose up -d
```

Open <http://localhost:3000> and register. **The first account to register becomes the admin.**

Database migrations run automatically: the `migrate` service runs `alembic upgrade head` to completion before `backend` and `worker` are allowed to start. You never run migrations by hand for a Docker deployment.

Only port 3000 is published for the app. The backend is reachable on 8000 for debugging, and Postgres and Redis are bound to `127.0.0.1` only.

## Prerequisites

- **Docker**: Docker Desktop (or any Docker Engine with Compose v2).
- **Local development**: Python 3.13, Node 20, Postgres 16 with the `vector` extension available, Redis 7.

## Local development without Docker

```
docker compose up -d postgres redis      # or run them natively
pip install -r backend/requirements-dev.txt

cd backend
alembic upgrade head
uvicorn app.main:app --reload            # terminal 1
arq app.worker.WorkerSettings            # terminal 2 — the same command Docker uses

cd ../frontend
npm install
API_INTERNAL_URL=http://localhost:8000 npm run dev
```

`.env` is read from the **repository root**, anchored by path, regardless of which directory you run from. There is no second `.env` under `backend/`.

`API_INTERNAL_URL` is read at **build time only** — `next build` bakes `rewrites()` into `.next/routes-manifest.json` and `next start` ignores the variable. Set it before `npm run build`, not before `npm start`. Compose passes it as a build argument.

## Seeding an admin without the UI

```
python scripts/create_admin.py admin@example.com
```

The password comes from `MOPAN_ADMIN_PASSWORD` or an interactive prompt. There is deliberately no default: an unattended run with neither set exits non-zero rather than creating a guessable account.

## Tests

```
cd backend
pytest                # needs Postgres running
ruff check .
```

The suite creates and migrates its own `mopan_test` database and **never touches `mopan`**. It makes no network calls and no OpenAI API calls — every external boundary is stubbed.

Run **one pytest session at a time**. The database fixture does `downgrade base`, so two concurrent sessions corrupt each other. Do not use `-n auto`.

Frontend:

```
cd frontend
npm run typecheck
npm test              # node --test, no test framework dependency
npm run build
```

## Smoke test

Against a running stack:

```
python scripts/smoke_test.py                          # default http://localhost:3000
python scripts/smoke_test.py https://your.tunnel.url
```

It runs against the **frontend** origin by default, which is what a browser talks to, so it also proves the rewrite proxy works. Pure Python + httpx — no bash, no curl, identical on Windows and Linux.

It registers a fresh account, so it needs no setup. But only the *first* account on a deployment is an admin, and the ingestion half - upload, wait for indexing, then assert the new document comes back from search - needs one. Point it at an existing admin to exercise the whole path:

    MOPAN_SMOKE_EMAIL=admin@example.com MOPAN_SMOKE_PASSWORD=... python scripts/smoke_test.py

## Configuration

All settings live in `.env` at the repository root. `docker-compose.yml` overrides `DATABASE_URL` and `REDIS_URL` per service with container hostnames, so you do not edit those two for Docker.

| Key | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | `production` forbids self-registration unless `ALLOW_SELF_REGISTRATION=true` |
| `DATABASE_URL` | `postgresql+asyncpg://mopan:mopan@127.0.0.1:5432/mopan` | `127.0.0.1`, not `localhost` — see below |
| `REDIS_URL` | `redis://:mopan@127.0.0.1:6379/0` | Sessions and the job queue |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `10` / `10` | |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Only for direct backend access; the browser uses the same-origin proxy |
| `SESSION_TTL_SECONDS` | `86400` | Redis-backed sessions, not JWT — logout revokes server-side |
| `ALLOW_SELF_REGISTRATION` | unset | Unset means: allowed outside production, forbidden in production |
| `OPENAI_API_KEY` | — | Required for ingestion and answering |
| `ANSWER_MODEL` | `gpt-4o` | |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `EMBEDDING_DIM` | `1536` | **See the warning below** |
| `EMBEDDING_BATCH_SIZE` | `128` | 1–2048, the endpoint's array cap |
| `EMBEDDING_BATCH_CHARS` | `200000` | Character proxy for the per-request token cap; script-dependent |
| `LLM_TIMEOUT_SECONDS` | `30.0` | Without it a hung completion holds a worker slot for ten minutes |
| `LLM_MAX_RETRIES` | `3` | |
| `RRF_K` | `60` | Reciprocal Rank Fusion constant |
| `RETRIEVAL_TOP_N` | `6` | Chunks that reach the prompt |
| `RETRIEVAL_CANDIDATE_LIMIT` | `20` | Per retriever, before fusion |
| `CHUNKING_STRATEGY` | `semantic` | `semantic` (structure + embedding merge) or `fixed` (character windows) |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `800` / `100` | Characters; `fixed` strategy only; `0 <= overlap < size` |
| `MAX_CHUNK_TOKENS` | `500` | 1–4095; bounds every strategy |
| `SEMANTIC_SIMILARITY_THRESHOLD` | `0.75` | Cosine, −1.0 to 1.0. Higher merges less. `1.0` is not "never" — float noise puts identical vectors at or above it |
| `ANSWER_CONTEXT_TOKEN_BUDGET` | `6000` | Evidence tokens allowed into the prompt |
| `UPLOAD_DIR` | `./data/uploads` | |
| `MAX_UPLOAD_SIZE_MB` | `50` | Enforced server-side; the Next proxy body cap is raised to match |
| `API_INTERNAL_URL` | `http://localhost:8000` | Build-time only, see above |

> **Changing `EMBEDDING_MODEL` or `EMBEDDING_DIM` requires a new Alembic migration to alter the `chunks.embedding` column width AND a full re-index of every document.** Existing vectors are not convertible. The app refuses to start if `EMBEDDING_DIM` disagrees with the column, which turns a silent retrieval failure into a boot failure.

**Why `127.0.0.1` and not `localhost`:** Compose publishes Postgres and Redis on IPv4 only, while on Windows `localhost` resolves to `::1` first. Every connection then pays a failed IPv6 attempt before falling back — measured at 2076 ms against 31 ms. The test suite opens a fresh connection per checkout, so that fallback alone took a 52-second suite to 13 minutes.

## External access

```
cloudflared tunnel --url http://localhost:3000
```

**One tunnel exposes the whole app.** Because `/api/*` is proxied same-origin by Next.js, the API needs no tunnel of its own, no CORS entry, and no cookie `SameSite` relaxation. Nothing in the app depends on the tunnel; it is purely a way to let someone outside your network reach a local stack.

The chat endpoint streams Server-Sent Events and sets `Cache-Control: no-transform` so intermediaries do not buffer it into a single response. Without that header, compression at the proxy collapses every progress frame into one read after the answer is already finished.

## Frontend / backend type correspondence

`frontend/lib/types.ts` mirrors `backend/app/schemas/*` by hand. It is small enough that this is cheaper than a build step. If the two drift, generate the TypeScript instead of patching it:

```
npx openapi-typescript http://localhost:8000/openapi.json -o frontend/lib/api-types.ts
```

## Troubleshooting

**`extension "vector" is not available`** — Postgres is running without pgvector. The Compose file uses `pgvector/pgvector:pg16`; a native Postgres needs the extension installed separately.

**Documents always end at `failed`** — check `OPENAI_API_KEY`. The app boots fine without a valid key: only ingestion and answering fail, and the failure reason is shown in the document table.

**Settings look ignored** — `.env` is read from the repository root, not the current directory. Confirm the file is at the root and not under `backend/`.

**A document is stuck at `parsing` or `chunking`** — the worker is not running, or Redis restarted and lost the queue. Check `docker compose logs worker`, then re-upload. Redis runs with AOF persistence to make queue loss rare. The document list shows how long a document has been in its current state.

**`migrate` failed and backend will not start** — `docker compose logs migrate`. The backend deliberately refuses to start against an unmigrated database rather than failing later on a missing column.

**Port already in use** — 3000, 8000, 5432 and 6379. Change the published port in `docker-compose.yml`; the internal ports are fixed.

**A document exists in the list but its detail page says `원본 파일을 더 이상 찾을 수 없습니다.`** - it was uploaded in a different run mode. Local development and Docker share the same Postgres, but `UPLOAD_DIR=./data/uploads` is a host directory for a local run and a named volume inside Docker. So a document uploaded outside Docker has a database row the Docker stack can see and a file it cannot open. Re-upload it, or bind-mount the host directory instead of the `uploaddata` volume if you want one corpus across both modes.

**`npm run build` fails with `PageNotFoundError: Cannot find module for page: /_not-found`** — a stale `.next`. Delete `frontend/.next` and build again.
