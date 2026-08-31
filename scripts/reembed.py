"""Re-embed every stored chunk with a named model. The operation EMBEDDING_MODEL
cannot be changed without.

    python scripts/reembed.py --model text-embedding-3-large --dim 1536
    python scripts/reembed.py --model text-embedding-3-large --dim 1536 --apply

Dry run by default. `--apply` is the only thing that writes.

WHY THIS EXISTS AS A SCRIPT AND NOT A SETTING. `EMBEDDING_MODEL` and
`EMBEDDING_DIM` are env-only, deliberately: changing either invalidates every
vector already stored, and a settings screen that let someone flip a dropdown
would leave the corpus embedded by one model and the questions by another. The
symptom is not an error, it is silently terrible retrieval - cosine similarity
between two different models' spaces is noise. So the change is a deployment
step with a script attached, and this is the script.

RUN THIS BEFORE FLIPPING `.env`, not after. While the corpus is embedded by the
old model and queries by the new one, retrieval is broken; doing it in this order
keeps that window to the duration of this script's own writes rather than to the
gap between a restart and someone remembering to re-index.

Cost, measured on the live corpus (2578 chunks of 특허·실용신안 심사기준, ~933k
tokens): text-embedding-3-small $0.019, text-embedding-3-large $0.121. Printed
before anything is written, so `--apply` is a decision made with the number in
front of you.

`--dim` is passed to the API as `dimensions`. text-embedding-3-* are Matryoshka
models, so 3-large at 1536 is the full model truncated and renormalised - it
measured IDENTICALLY to the full 3072 on this corpus (anchor@14 0.904, recall
1.000) while fitting the existing vector(1536) column. That is what makes the
dense-arm upgrade a config change rather than a migration.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# USD per 1M input tokens.
PRICES = {"text-embedding-3-small": 0.02, "text-embedding-3-large": 0.13}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--dim", type=int, required=True)
    parser.add_argument("--batch", type=int, default=256)
    parser.add_argument("--apply", action="store_true", help="actually write")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from sqlalchemy import select, update
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings
    from app.core.tokens import count_tokens
    from app.llm.openai_provider import OpenAIProvider
    from app.models.chunk import Chunk

    settings = get_settings()
    engine = create_async_engine(settings.database_url.replace("@postgres:", "@127.0.0.1:"))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        rows = (await session.execute(select(Chunk.id, Chunk.content))).all()
        tokens = sum(count_tokens(r.content) for r in rows)
        rate = PRICES.get(args.model)
        cost = f"${tokens / 1e6 * rate:.4f}" if rate else "unknown (model not in PRICES)"
        print(f"{len(rows)} chunks, ~{tokens} tokens, {args.model} @ {args.dim}d -> {cost}")
        if not rows:
            print("nothing to do")
            await engine.dispose()
            return 0
        if not args.apply:
            print("dry run. re-run with --apply to write.")
            await engine.dispose()
            return 0

        # The provider is built with the model and width from ARGV, never from
        # settings: this script runs while .env still names the OLD model, and
        # reading settings here would re-embed with the model being replaced.
        provider = OpenAIProvider(
            settings.openai_api_key,
            args.model,
            settings.answer_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            embedding_dim=args.dim,
        )

        started = time.perf_counter()
        done = 0
        for start in range(0, len(rows), args.batch):
            batch = rows[start : start + args.batch]
            vectors = await provider.embed([r.content for r in batch])
            for row, vector in zip(batch, vectors, strict=True):
                await session.execute(
                    update(Chunk).where(Chunk.id == row.id).values(embedding=vector)
                )
            # Commit per batch rather than once at the end. A failure halfway
            # then leaves a corpus that is PART re-embedded, which is bad - but
            # the alternative is a single transaction holding 2578 row locks
            # across minutes of network calls, and a rollback that throws away
            # money already spent. Re-running the script is idempotent; the
            # partial state is recoverable and the wasted spend is not.
            await session.commit()
            done += len(batch)
            print(f"  {done}/{len(rows)}", end="\r")

        print(f"\nre-embedded {done} chunks in {time.perf_counter() - started:.1f}s")
        print(f"NOW set EMBEDDING_MODEL={args.model} and EMBEDDING_DIM={args.dim} in .env,")
        print("then: docker compose build backend worker")
        print("      docker compose up -d --force-recreate --no-deps backend worker")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
