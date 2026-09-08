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

from app.chat.prompt import get_prompt
from app.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger("mopan.chat")

SEARCH = "search"
CHAT = "chat"

# 판정 프롬프트는 prompt.py의 INTENT_SYSTEM_PROMPT가 기본값이고, 프롬프트 관리의
# intent_agent가 있으면 그것이 우선한다(get_prompt). 라벨 계약은 파싱이 지킨다:
# chat/search 외의 출력은 전부 search다.
INTENT_PROMPT_NAME = "intent_agent"

# 이미지가 붙은 발화에만 덧붙는다. 게이트는 글자만 보므로 "자료의 문구를 확인해줘"가
# 첨부 그림을 읽어 달라는 말인지 코퍼스를 뒤져 달라는 말인지 알 길이 없었다(실사고:
# 포스터 사진의 문구 확인이 도구·문서 검색을 타고 "근거 없음" 경고까지 달렸다).
# 그림 자체에 대한 요청은 검색할 정답 청크가 없으니 chat과 같은 갈래다.
_IMAGE_HINT = (
    "\n\nThe user ATTACHED AN IMAGE to this message. If the message is about the image itself "
    "- read its text, describe it, check or transcribe its wording, translate it - reply chat: "
    "the image is answered directly and no document search can help. If it asks how the image "
    "relates to the documents (its classification, legality, a procedure, a rule), reply search."
)


async def classify_intent(
    llm_provider: LLMProvider,
    question: str,
    *,
    model: str,
    timeout: float,
    has_images: bool = False,
) -> str:
    """`question`이 검색을 원하면 "search", 대화면 "chat". 절대 raise하지 않는다."""
    system = (await get_prompt(INTENT_PROMPT_NAME)).text + (_IMAGE_HINT if has_images else "")
    user = f"[image attached]\n{question}" if has_images else question
    try:
        result = await asyncio.wait_for(
            llm_provider.chat(
                [
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user),
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
