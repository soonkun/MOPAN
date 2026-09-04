"""의도 게이트 오탐률 측정.

    docker compose exec -T backend python /app/scripts/probe_intent.py

두 방향을 잰다:
1. 픽스처 질문 전부(86 + 구어체 10)가 "search"로 판정되는가 - 진짜 질문을
   잡담 취급하면 답을 받을 기회가 사라지므로 여기의 허용 오탐은 0이다.
2. 대화형 발화 표본이 "chat"으로 판정되는가 - 이쪽 오탐은 어색할 뿐
   치명적이지 않지만, 게이트의 존재 이유이므로 같이 본다.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app.chat.intent import classify_intent  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.llm.openai_provider import OpenAIProvider  # noqa: E402

SMALLTALK = [
    "안녕?",
    "안녕하세요!",
    "고마워",
    "감사합니다 큰 도움이 됐어요",
    "너는 누구야?",
    "너 뭘 할 수 있어?",
    "이 시스템은 어떻게 쓰는 거야?",
    "ㅋㅋㅋㅋ",
    "잘자~",
    "테스트",
    "오 대단한데?",
    "심심하다",
    "좋은 아침!",
    "수고했어 내일 보자",
]


async def main() -> None:
    settings = get_settings()
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

    questions: list[str] = []
    for fixture in ("eval_questions_ko.json", "eval_questions_ko_colloquial.json"):
        data = json.loads((Path("/app/scripts") / fixture).read_text(encoding="utf-8"))
        questions += [q["question"] for q in data["questions"]]

    semaphore = asyncio.Semaphore(8)

    async def judge(text: str) -> str:
        async with semaphore:
            return await classify_intent(
                provider,
                text,
                model=settings.query_expansion_model,
                timeout=settings.query_expansion_timeout_seconds,
            )

    verdicts = await asyncio.gather(*(judge(q) for q in questions))
    wrong = [q for q, v in zip(questions, verdicts) if v != "search"]
    print(f"픽스처 질문 {len(questions)}개 중 search 아님: {len(wrong)}")
    for q in wrong:
        print("  !", q)

    verdicts = await asyncio.gather(*(judge(u) for u in SMALLTALK))
    missed = [(u, v) for u, v in zip(SMALLTALK, verdicts) if v != "chat"]
    print(f"대화형 발화 {len(SMALLTALK)}개 중 chat 아님: {len(missed)}")
    for u, v in missed:
        print("  !", u, "->", v)


asyncio.run(main())
