"""Queue documents for re-processing, by id.

    docker compose exec -T backend python /app/scripts/reingest.py <uuid> [<uuid> ...]

A document is re-cut only when it is re-ingested - that is the contract migration
0013 set and 0014 keeps - so changing a collection's `chunking` configuration
changes nothing until this runs. It enqueues exactly the job the upload endpoint
enqueues; the pipeline deletes and re-inserts inside one transaction, so a failure
leaves the old chunks in place rather than an empty index.

IDS, NEVER NAMES. An agent working in this repository once deleted a real document
during cleanup. Nothing here deletes anything, and the ids are printed back with
their filenames and current chunk counts before the jobs are queued.
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.documents.service import enqueue_document_processing, make_arq_pool  # noqa: E402


async def main(ids: list[str]) -> int:
    settings = get_settings()
    engine = make_engine(settings)
    session = make_sessionmaker(engine)
    async with session() as db:
        rows = (
            await db.execute(
                text(
                    "select d.id::text as id, d.filename, d.status, c.name as collection, "
                    "       c.chunking::text as chunking, "
                    "       (select count(*) from chunks where document_id = d.id) as chunks "
                    "  from documents d join collections c on c.id = d.collection_id "
                    " where d.id::text = any(:ids)"
                ),
                {"ids": ids},
            )
        ).all()
    found = {row.id for row in rows}
    missing = [value for value in ids if value not in found]
    for row in rows:
        print(f"{row.id}  {row.filename}  [{row.collection}] {row.chunks} chunks  {row.chunking}")
    if missing:
        print(f"NOT FOUND: {missing}")
        await engine.dispose()
        return 1

    pool = await make_arq_pool(settings)
    for value in ids:
        await enqueue_document_processing(pool, value)
        print("queued", value)
    await pool.aclose()
    await engine.dispose()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
