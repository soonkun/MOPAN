"""Re-tokenise every chunk's content_tsv with the configured sparse tokenizer.

Run this after migration 0012, and again after any change to SPARSE_TOKENIZER or
to app/retrieval/tokenize.py. Until it has run for the tokenizer the query side
uses, the sparse arm retrieves nothing: content_tsv holds whatever tokenizer
wrote it and a query built by a different one asks for lexemes no row stored.

    docker compose exec api python scripts/backfill_tsv.py     # inside the stack
    python scripts/backfill_tsv.py                             # from the host

UPDATE only. It never inserts, never deletes, and never touches `documents`, so
it is safe to re-run and safe to interrupt - a half-finished run leaves the rows
it already wrote correct and the rest holding their previous value, and running
it again finishes the job. Measured at ~20s for 2578 chunks under bigram.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select, update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.chunk import Chunk, sparse_tsvector  # noqa: E402


async def backfill(tokenizer: str, batch_size: int) -> int:
    settings = get_settings()
    # Same host rewrite scripts/eval_retrieval.py uses: 'postgres' resolves inside
    # the compose network, 127.0.0.1 is the published port from the host. Running
    # inside the container leaves it a no-op.
    db_url = settings.database_url
    from pathlib import Path
    if not Path("/.dockerenv").exists():
        db_url = db_url.replace("@postgres:", "@127.0.0.1:")
    engine = create_async_engine(db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    done = 0
    started = time.perf_counter()
    try:
        async with maker() as session:
            total = await session.scalar(select(func.count()).select_from(Chunk))
            print(f"{total} chunks, tokenizer={tokenizer}, batch={batch_size}")
            # Keyset pagination on the primary key, not OFFSET: OFFSET re-scans
            # everything it skips, and this loop writes to the very table it is
            # paging over.
            after = None
            while True:
                rows = select(Chunk.id, Chunk.content).order_by(Chunk.id).limit(batch_size)
                if after is not None:
                    rows = rows.where(Chunk.id > after)
                batch = (await session.execute(rows)).all()
                if not batch:
                    break
                # ponytail: one UPDATE per row - 2578 round trips on a local
                # socket, a couple of seconds. Batch into an
                # `UPDATE ... FROM (VALUES ...)` if this ever runs against a
                # remote database or a 10x corpus.
                for chunk_id, content in batch:
                    # `sparse_tsvector` is THE expression, shared with
                    # PgVectorStore.upsert, so a backfilled chunk and a freshly
                    # ingested one are byte-identical. Never a tsvector literal:
                    # that carries no positions, and ts_rank reads term frequency
                    # out of positions - measured on this database, a token
                    # appearing three times ranks 0.0828 with positions and 0.0608
                    # without.
                    await session.execute(
                        update(Chunk)
                        .where(Chunk.id == chunk_id)
                        .values(content_tsv=sparse_tsvector(content, tokenizer))
                    )
                await session.commit()
                after = batch[-1][0]
                done += len(batch)
                print(f"  {done}/{total}", flush=True)
    finally:
        await engine.dispose()
    print(f"backfilled {done} chunks in {time.perf_counter() - started:.1f}s")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tokenizer",
        default=None,
        help="override SPARSE_TOKENIZER (the query side must be told the same thing)",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    tokenizer = args.tokenizer or get_settings().sparse_tokenizer
    asyncio.run(backfill(tokenizer, args.batch_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
