"""Where does the 법 제36조제1항 chunk sit in the DENSE arm?

The one number this whole line of work is aimed at. `상표심사기준.pdf` p.89 carries
"3. 상표 / 4. 지정상품 및 산업통상자원부령으로 정하는 상품류" - the answer to the
owner's question - and it is a bare enumeration with no sentence in it. Measured
before ancestor context: sparse rank 1-2, dense rank 91-282 depending on phrasing.
CANDIDATE_LIMIT is 10, so a chunk outside the dense top 10 cannot be corroborated
by that arm, and an uncorroborated best candidate is what diverts the answer to
the clarification prompt.

RUN IT INSIDE THE BACKEND CONTAINER, which is where the API key lives:

    docker compose exec -T backend python /app/scripts/probe_36_rank.py

The target is found by CONTENT, not by a hardcoded chunk id, so it survives the
re-ingestion it exists to measure.
"""

import asyncio
import sys

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.llm.openai_provider import OpenAIProvider  # noqa: E402

# The owner's question, and the three parts it decomposes into. Each is asked of
# the dense arm exactly as the retrieval path would ask it.
QUERIES = [
    "내가 상표출원을 하려고 하는데, 어플 이름을 출원하려고해. 상표등록출원서에 등록대상은 뭘로 기재해? 류와 지정상품 알려줘",
    "상표등록출원서에 등록대상은 뭘로 기재해?",
    "어플 이름을 상표출원하려는데 몇 류로 출원해야 하나요?",
    "상표등록출원서에 지정상품은 어떻게 적나요?",
    "상표등록출원서에 적어야 하는 사항은 무엇인가요?",
]

NEEDLE = "%4. 지정상품 및 산업통상자원부령으로 정하는 상품류%"


async def main() -> None:
    settings = get_settings()
    engine = make_engine(settings)
    session = make_sessionmaker(engine)
    provider = OpenAIProvider(
        api_key=settings.openai_api_key,
        embedding_model=settings.embedding_model,
        answer_model=settings.answer_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        batch_size=settings.embedding_batch_size,
        batch_chars=settings.embedding_batch_chars,
        embedding_dim=settings.embedding_dim,
    )
    embeddings = await provider.embed(QUERIES)

    async with session() as db:
        targets = (
            await db.execute(
                text(
                    "select id::text, chunk_index, page, left(content, 90) as head "
                    "from chunks where content like :needle order by chunk_index"
                ),
                {"needle": NEEDLE},
            )
        ).all()
        if not targets:
            print("target chunk not found - has the document been re-ingested?")
            return
        total = await db.scalar(text("select count(*) from chunks where embedding is not null"))
        print(f"corpus: {total} embedded chunks")
        for row in targets:
            print(f"target: chunk {row.chunk_index} p.{row.page}  {row.head!r}")

        for query, embedding in zip(QUERIES, embeddings, strict=True):
            vector = "[" + ",".join(f"{value:.7g}" for value in embedding) + "]"
            ranks = []
            for row in targets:
                rank = await db.scalar(
                    text(
                        "select 1 + count(*) from chunks "
                        "where embedding is not null "
                        "and embedding <=> cast(:v as vector) < "
                        "    (select embedding <=> cast(:v as vector) from chunks where id = :t)"
                    ),
                    {"v": vector, "t": row.id},
                )
                ranks.append(rank)
            print(f"  dense rank {min(ranks):>6}  (of {len(targets)} target chunks)  {query[:52]}")

    await provider.aclose()
    await engine.dispose()


asyncio.run(main())
