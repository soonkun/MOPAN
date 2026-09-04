"""sparse_lexeme_df - 렉심별 문서빈도(DF) 표를 만든다.

    docker compose exec -T backend python /app/scripts/build_lexeme_df.py

SPARSE_DF_TRIM(가난한 IDF - keyword_search._trim_common_tokens)이 읽는 표다.
ts_stat 이 GIN 인덱스가 아니라 tsvector 전체를 훑으므로 코퍼스 크기에
비례해 느리다 - 2만 청크에서 수십 초. 오프라인 1회이고, 코퍼스가 크게
바뀌면(대량 적재·재적재) 다시 돌린다. 갱신을 미뤄도 안전한 방향으로만
틀린다: 새 문서의 렉심은 표에 없어 df 0 = 희귀로 취급된다.

alembic 마이그레이션이 아니라 스크립트인 이유: 이 표는 파생 데이터다.
원본(content_tsv)에서 언제든 다시 만들 수 있고, 스키마가 아니라 통계이며,
SPARSE_DF_TRIM 이 측정에서 지면 표째 지운다. 이긴다면 그때 마이그레이션과
적재 파이프라인 훅으로 승격한다.
"""

import asyncio
import sys
import time

sys.path.insert(0, "/app")

from sqlalchemy import text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402


async def main() -> None:
    engine = make_engine(get_settings())
    maker = make_sessionmaker(engine)
    started = time.perf_counter()
    async with maker() as db:
        await db.execute(
            text(
                "CREATE TABLE IF NOT EXISTS sparse_lexeme_df ("
                "lexeme text PRIMARY KEY, df integer NOT NULL)"
            )
        )
        await db.execute(text("TRUNCATE sparse_lexeme_df"))
        await db.execute(
            text(
                "INSERT INTO sparse_lexeme_df (lexeme, df) "
                "SELECT word, ndoc FROM ts_stat('SELECT content_tsv FROM chunks')"
            )
        )
        # '__total__' = 전체 청크 수. 진짜 bigram 렉심은 전부 한글 조각이라
        # 이 마커와 충돌하지 않는다.
        await db.execute(
            text(
                "INSERT INTO sparse_lexeme_df (lexeme, df) "
                "SELECT '__total__', count(*) FROM chunks"
            )
        )
        await db.commit()
        rows = (await db.execute(text("SELECT count(*) FROM sparse_lexeme_df"))).scalar_one()
        total = (
            await db.execute(
                text("SELECT df FROM sparse_lexeme_df WHERE lexeme = '__total__'")
            )
        ).scalar_one()
    print(
        f"sparse_lexeme_df: 렉심 {rows - 1:,}개, 청크 {total:,}개, "
        f"{time.perf_counter() - started:.1f}s"
    )
    await engine.dispose()


asyncio.run(main())
