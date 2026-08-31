"""Parse and cut a stored document with NO embedding call, and print what came out.

    docker compose exec -T backend python /tmp/probe_dry_chunk.py <document-uuid> [needle]

A re-ingest costs real money in embeddings; this costs none. It runs the exact
parser and the exact chunker the worker would - same collection configuration,
same settings - and swaps only `llm_provider.embed` for a zero vector, so what it
prints is what would be stored.
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.core.settings_store import effective_settings  # noqa: E402
from app.rag.chunking import detect, get_chunking_strategy, resolve, resolve_scheme  # noqa: E402
from app.rag.parsers import get_parser  # noqa: E402


async def zero_embed(texts: list[str]) -> list[list[float]]:
    """The semantic strategy only uses these to decide whether two neighbours are
    similar. Identical vectors make every pair maximally similar, which is the
    WRONG merge decision - so the prose half of this dry run is not what would
    ship. The hierarchy half, which is what is being inspected, does not embed at
    all and is exact."""
    return [[0.0] * 1536 for _ in texts]


async def main(document_id: str, needle: str | None) -> None:
    settings = get_settings()
    engine = make_engine(settings)
    session = make_sessionmaker(engine)
    async with session() as db:
        settings = await effective_settings(db, settings)
        row = (
            await db.execute(
                text(
                    "select d.storage_path, d.file_type, d.filename, c.chunking::text as chunking "
                    "  from documents d join collections c on c.id = d.collection_id "
                    " where d.id = :id"
                ),
                {"id": document_id},
            )
        ).one()
    import json

    chunking = json.loads(row.chunking)
    markers = resolve(chunking)
    scheme = resolve_scheme(chunking)
    print(f"{row.filename}  chunking={chunking}")

    parsed = get_parser(row.file_type).parse(
        row.storage_path, markers.marker if markers else None
    )
    print(f"blocks: {len(parsed.blocks)}")
    if scheme is not None:
        print("detect:", detect(parsed.blocks, scheme).as_json())

    strategy = get_chunking_strategy(settings, chunking)
    candidates = await strategy.chunk(parsed.blocks, zero_embed)
    print(f"candidates: {len(candidates)}")
    structured = [c for c in candidates if c.metadata.get("path")]
    print(f"  with a hierarchy path: {len(structured)}")
    print(f"  mean chars: {sum(c.char_count for c in candidates) / max(len(candidates), 1):.0f}")

    if needle:
        for index, candidate in enumerate(candidates):
            if needle in candidate.content:
                print(f"\n--- candidate {index}  section={candidate.section!r} {candidate.metadata}")
                print(candidate.content[:1200])

    await engine.dispose()


asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
