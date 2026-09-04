"""분류표에 구어체 어휘 다리를 놓는다 - sparse 전용, 임베딩은 건드리지 않는다.

    docker compose exec -T backend python /app/scripts/expand_table_sparse.py           # 미리보기 5개 섹션
    docker compose exec -T backend python /app/scripts/expand_table_sparse.py --apply   # 전체 적용

측정된 문제 (scripts/eval_questions_ko_colloquial.json, anchor 0.100):
사람은 "치킨집", "어플", "동네 마트"라고 치고 분류표는 "한식점업",
"애플리케이션 소프트웨어", "슈퍼마켓업"이라고 인쇄한다. 어느 토크나이저도
그 사이를 잇지 못하고, 유일한 다리였던 LLM 재작성은 되묻기 게이트 뒤의
비결정적 1회 호출이었다 (배포형 재시도로도 anchor 0.200).

처방은 Doc2Query의 sparse 절반이다 (arXiv 2510.09557가 측정한 dual-index
원칙): 섹션마다 "이 섹션이 답이 될 구어체 표현"을 오프라인에서 한 번
생성해서 그 섹션 모든 청크의 **content_tsv에만** 넣는다.

- content는 그대로다. 인용에 보이는 원문이 바뀌지 않는다.
- embedding도 그대로다. 확장문을 dense에 섞으면 원문 신호가 희석된다는
  것이 위 논문의 측정이고, 우리는 아예 섞을 방법이 없는 자리(tsv)에 넣는다.
- 생성한 어휘는 chunk_metadata.sparse_expansion에 그대로 남는다 - 추론된
  것은 보여야 한다는 이 저장소의 규칙. 문서 화면이 아직 안 읽지만 psql로
  감사할 수 있다.
- 섹션의 모든 청크에 같은 어휘를 넣는 이유는 head_line과 같다: 어느 행
  청크가 미래의 질문에 답할지 미리 알 수 없고, 접기 융합(collapse)이 섹션
  형제의 표 겹침을 이미 처리한다.

재적재하면 사라진다. 이 문서(분류표)를 다시 적재한 뒤에는 이 스크립트를
다시 돌려야 한다 - 파이프라인에 넣는 것은 이것이 값을 한다는 측정이 나온
다음의 일이다.

**측정 결과: 이 형태로는 값을 못 했고, 적용분은 되돌렸다 (2026-09-05).**
931 섹션 전체에 적용($0.124)해도 구어체 픽스처가 0.500/0.100 그대로였다.
원인은 커버리지가 아니라 랭킹이다: 정답 섹션에 '할인마트' 어휘가 들어가도
IDF 없는 ts_rank 아래에서 흔한 질의 bigram(상표·등록·이름)에 익사한다
(정답 청크 sparse 300위 밖 실측). 구어체를 실제로 움직인 것은 검색이 아니라
전달이었다 - attach_section_heads(neighbors.py)가 0.100 -> 0.500. 이 스크립트는
"문서측 확장을 다시 시도할 때 어디서부터 틀렸는지"의 기록으로 남는다.
살릴 길이 있다면 sparse 점수 함수가 IDF를 갖게 된 다음이다.
"""

import argparse
import asyncio
import sys
import uuid
from collections import defaultdict

sys.path.insert(0, "/app")

from sqlalchemy import cast, text, update  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine, make_sessionmaker  # noqa: E402
from app.llm.base import ChatMessage  # noqa: E402
from app.llm.openai_provider import OpenAIProvider  # noqa: E402
from app.models.chunk import Chunk, sparse_tsvector  # noqa: E402

DOCUMENT = "유사상품 심사기준.pdf"
MODEL = "gpt-4o-mini"
CONCURRENCY = 8

PROMPT = """너는 상표 출원을 도우려는 검색 색인 작성자다. 아래는 니스분류 유사상품 심사기준의 한 섹션이다.

머리글: {head}
수록 상품·서비스 표본: {sample}

일반인이 자기 가게·제품·앱을 두고 실제로 칠 법한 **구어체 한국어 표현**을 6~14개 만들어라.
- 예: 치킨집, 빵집, 어플, 앱, 동네 마트, 옷 가게, 강아지 사료
- 위 표본에 이미 그대로 있는 격식 용어는 다시 쓰지 마라.
- 이 섹션과 무관하게 넓은 말(가게, 회사, 브랜드 단독)은 쓰지 마라.
- 한 줄에 하나, 다른 말은 붙이지 마라."""


def parse_terms(raw: str) -> list[str]:
    terms = []
    for line in raw.splitlines():
        term = line.strip().strip("-•◦*·").strip()
        if not term or len(term) > 40 or not any("가" <= ch <= "힣" for ch in term):
            continue
        if term not in terms:
            terms.append(term)
    return terms[:14]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="쓴다. 없으면 미리보기 5개 섹션")
    parser.add_argument("--document", default=DOCUMENT)
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings)
    maker = make_sessionmaker(engine)
    provider = OpenAIProvider(
        api_key=settings.openai_api_key,
        embedding_model=settings.embedding_model,
        answer_model=MODEL,
        timeout=30.0,
        max_retries=2,
        batch_size=settings.embedding_batch_size,
        batch_chars=settings.embedding_batch_chars,
        embedding_dim=settings.embedding_dim,
    )

    async with maker() as db:
        rows = (
            await db.execute(
                text(
                    "select c.id::text as id, c.section, c.content "
                    "from chunks c join documents d on d.id = c.document_id "
                    "where d.filename = :f and c.chunk_metadata->>'strategy' = 'classification_table' "
                    "order by c.chunk_index"
                ),
                {"f": args.document},
            )
        ).all()
    sections: dict[str, list] = defaultdict(list)
    for row in rows:
        sections[row.section].append(row)
    print(f"{args.document}: {len(rows)} chunks, {len(sections)} sections")

    picked = list(sections.items()) if args.apply else list(sections.items())[:5]
    semaphore = asyncio.Semaphore(CONCURRENCY)
    total_tokens = {"prompt": 0, "completion": 0}

    async def generate(section: str, members: list) -> tuple[str, list[str]]:
        # 머리글(head_line + 마커)과 상품명 표본 - 각 청크의 앞 두 줄은 머리라
        # 세 번째 줄부터가 본문이다.
        bodies = []
        for member in members[:6]:
            lines = member.content.split("\n")
            bodies.append(" ".join(lines[2:])[:160])
        head = "\n".join(members[0].content.split("\n")[:2])
        prompt = PROMPT.format(head=head, sample=" / ".join(bodies)[:900])
        async with semaphore:
            try:
                result = await provider.chat(
                    [ChatMessage(role="user", content=prompt)], temperature=0.0
                )
            except Exception as exc:  # 한 섹션의 실패가 전체를 멈추지 않는다.
                print(f"  ! {section[:40]}: {exc}")
                return section, []
        usage = result.usage or {}
        total_tokens["prompt"] += usage.get("prompt_tokens", 0)
        total_tokens["completion"] += usage.get("completion_tokens", 0)
        return section, parse_terms(result.content)

    results = await asyncio.gather(*(generate(s, m) for s, m in picked))
    generated = {s: t for s, t in results if t}

    cost = total_tokens["prompt"] * 0.15 / 1e6 + total_tokens["completion"] * 0.60 / 1e6
    print(f"생성: {len(generated)}/{len(picked)} 섹션, 토큰 {total_tokens}, 비용 ${cost:.4f}")

    if not args.apply:
        for section, terms in list(generated.items())[:5]:
            print(f"\n{section}\n  -> {', '.join(terms)}")
        print("\n--apply 로 쓴다.")
        await engine.dispose()
        return

    updated = 0
    async with maker() as db:
        for section, terms in generated.items():
            expansion = " ".join(terms)
            # dict를 그대로 바인딩한다. json.dumps로 문자열을 넘기면 JSONB가
            # 그것을 JSON "문자열 스칼라"로 직렬화해서 object || string 이
            # 배열이 된다 - 실제로 8,167행이 그렇게 됐고 psql로 복구했다.
            meta = cast({"sparse_expansion": terms}, JSONB)
            for member in sections[section]:
                await db.execute(
                    update(Chunk)
                    .where(Chunk.id == uuid.UUID(member.id))
                    .values(
                        chunk_metadata=Chunk.chunk_metadata.op("||")(meta),
                        content_tsv=sparse_tsvector(member.content + "\n" + expansion),
                    )
                )
                updated += 1
        await db.commit()
    print(f"청크 {updated}개의 content_tsv 갱신 (content·embedding은 그대로)")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
