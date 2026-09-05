"""후속 턴을 자립형 검색 질문으로 압축한다.

실사고에서 왔다: 되묻기("어떤 용도로 사용되는지...?")에 사용자가
"소셜네트워크용이야"라고 답하면, 검색은 그 여섯 글자로만 나간다. 분류표에
닿을 리 없고, 모델은 자기 지식(제9류/제42류)으로 근거 없이 메꾼다 - 인용이
하나도 없는 답이 그렇게 태어난다. 문서가 없어서가 아니라 검색이 대화를
몰라서다.

이력이 있는 검색 턴에서 한 번, 값싼 모델에게 묻는다: 직전 대화에 비추어
마지막 발화를 혼자 읽어도 되는 검색 질문으로 다시 써라("소셜네트워크용
어플 이름의 상표 등록은 몇 류로 출원하나요?"). 이미 자립형이면 "pass".
답변 프롬프트는 원문과 전체 이력을 그대로 받으므로 이 압축은 검색에만
쓰인다.

실패·타임아웃·이상 출력은 전부 "원문 그대로"로 강등된다 - 게이트·재작성과
같은 계약. FOLLOWUP_CONDENSE(env, 기본 켜짐)로 끈다.
"""

import asyncio
import logging

from app.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger("mopan.chat")

_SYSTEM = (
    "You rewrite the LAST user message of a conversation into ONE self-contained search "
    "question for a document-retrieval system, resolving every pronoun and elliptical answer "
    "from the conversation (e.g. after \"어떤 용도인가요?\" the reply \"소셜네트워크용이야\" "
    "becomes \"소셜네트워크용 어플 이름의 상표 등록은 몇 류로 출원하나요?\"). Keep the user's "
    "concrete nouns. One line, Korean, no quotes, no explanation. If the last message is "
    "already self-contained, OR is not asking for information at all (인사, 감사, 잡담, 감상), "
    "reply with exactly: pass"
)

# 재작성과 같은 상한: 넘으면 모델이 설명을 붙인 것이므로 버린다.
_MAX_CHARS = 200
_HISTORY_TURNS = 6
_HISTORY_CHARS = 500


async def condense_followup(
    llm_provider: LLMProvider,
    history: list[dict],
    question: str,
    *,
    model: str,
    timeout: float,
) -> str | None:
    """자립형 질문을, 또는(이미 자립형이거나 어떤 실패든) None을 돌려준다."""
    if not history:
        return None
    messages = [ChatMessage(role="system", content=_SYSTEM)]
    for turn in history[-_HISTORY_TURNS:]:
        role = turn.get("role")
        content = (turn.get("content") or "")[:_HISTORY_CHARS]
        if role in ("user", "assistant") and content:
            messages.append(ChatMessage(role=role, content=content))
    messages.append(ChatMessage(role="user", content=question))
    try:
        result = await asyncio.wait_for(
            llm_provider.chat(messages, temperature=0.0, model=model),
            timeout=timeout,
        )
    except Exception:
        logger.warning("follow-up condense failed; searching with the question as asked")
        return None

    text = (result.content or "").strip().strip('"')
    if not text or text.lower() == "pass" or "\n" in text or len(text) > _MAX_CHARS:
        return None
    return text
