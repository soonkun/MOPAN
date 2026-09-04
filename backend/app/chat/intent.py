"""의도 게이트 - 이 발화가 검색을 원하는가, 대화를 원하는가.

실측 실패가 존재 이유다: "안녕?"이 RAG를 타서 "이 문서는 특허·실용신안
심사기준이라 인사말에 대한 내용은 다루지 않습니다"라는, 인용 달린 인사
응답이 나갔다. 검색 파이프라인의 어느 단계도 이것을 고칠 수 없다 - 검색이
아무리 좋아도 인사말의 정답 청크는 존재하지 않는다. 갈래는 검색 앞에서
갈라져야 한다.

분류는 값싼 completion 한 번이다 (query_expansion_model 재사용, 기본
gpt-4o-mini - 발화당 약 $0.00002, 수백 ms). 규칙 기반이 아닌 이유: "인사말
목록"은 도메인·언어 하드코딩이고, 이 저장소는 그것을 금지한다.

**모든 실패는 "search"로 강등된다.** 타임아웃, 예외, 알 수 없는 출력 -
전부 검색이다. 검색은 이 게이트가 생기기 전의 동작이므로, 게이트가 최악의
경우에 할 수 있는 일은 "아무것도 안 바꾸는 것"이다. 애매한 발화도 같은
방향으로 기울인다: 잡담을 검색하면 되묻기가 어색할 뿐이지만, 질문을 잡담
취급하면 답을 받을 기회 자체가 사라진다.

측정 (2026-09-05, scripts/probe_intent.py):
- 픽스처 질문 96개(86 + 구어체 10) 전부 "search" - 오탐 0
- 대화형 발화 14종(인사·감사·자기소개·기능 질문) 전부 "chat"
숫자가 바뀌면 이 주석도 바꿀 것.
"""

import asyncio
import logging

from app.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger("mopan.chat")

SEARCH = "search"
CHAT = "chat"

# 코드 상수이고 프롬프트 저장소가 아니다. expansion.py의 재작성 프롬프트와
# 같은 이유: 출력 라벨("chat"/"search")은 코드와의 계약이라, 화면에서 편집할
# 수 있게 두면 라벨이 표류하는 순간 게이트가 전부 "search"로 무너진다 (안전한
# 방향이긴 하지만, 편집이 조용히 무의미해지는 컨트롤은 두지 않는다).
_SYSTEM = (
    "You route messages for a document-grounded Q&A system. Decide whether the user's message "
    "needs a DOCUMENT SEARCH to answer, or is merely CONVERSATIONAL.\n"
    "\n"
    "Reply with exactly one word:\n"
    "- chat: greetings, thanks, goodbyes, small talk, jokes, test messages, or questions about "
    "the assistant/system itself (who are you, what can you do).\n"
    "- search: EVERYTHING else - any request for information, explanation, facts, procedures, "
    "opinions on a subject, or a follow-up to an earlier informational question.\n"
    "\n"
    "When in doubt, reply search. A search that finds nothing is handled gracefully; a real "
    "question dismissed as chat never gets its answer.\n"
    "\n"
    "One word only: chat or search."
)


async def classify_intent(
    llm_provider: LLMProvider,
    question: str,
    *,
    model: str,
    timeout: float,
) -> str:
    """`question`이 검색을 원하면 "search", 대화면 "chat". 절대 raise하지 않는다."""
    try:
        result = await asyncio.wait_for(
            llm_provider.chat(
                [
                    ChatMessage(role="system", content=_SYSTEM),
                    ChatMessage(role="user", content=question),
                ],
                temperature=0.0,
                model=model,
                max_tokens=3,
            ),
            timeout=timeout,
        )
        verdict = (result.content or "").strip().lower()
        if verdict.startswith(CHAT):
            return CHAT
        if verdict.startswith(SEARCH):
            return SEARCH
        logger.warning("intent classifier said %r; degrading to search", verdict[:40])
        return SEARCH
    except Exception:
        # 게이트는 최적화다. 죽어도 원래 동작(검색)으로 조용히 강등된다 -
        # expansion.expand_query와 같은 계약.
        logger.warning("intent classification failed; degrading to search", exc_info=True)
        return SEARCH
