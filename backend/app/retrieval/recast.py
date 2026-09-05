"""사례 서술 -> 전문 용어 검색 질의 재작성.

"내가 학회가서 발표를 했는데 그 내용으로 특허출원을 하려고 해. 문제가 될까?"의
실사고에서 왔다: 이 질문의 정답은 공지 예외 규정 쪽에 있는데, sparse 팔이
"특허출원"으로 출원서 기재사항 청크를 강하게 물어 와 근거가 약해 보이지 않았고,
그래서 약근거 재시도(expansion)도 되묻기도 발화하지 않았다. 문제는 어휘다 -
사용자는 자기 상황의 말로 묻고 코퍼스는 규정의 말로 답한다.

이 단계는 첫 검색 전에 한 번, 값싼 모델에게 묻는다: 상황 서술이면 코퍼스가
쓸 법한 용어의 질의로 다시 쓰고, 이미 용어형 질문이면 "pass". 도메인 지식은
프롬프트에 없다 - 무엇이 "그 상황을 다루는 용어"인지는 모델의 일반 지식이
답하고, 그래서 특허 코퍼스에도 농약 코퍼스에도 같은 코드가 돈다(모판 규칙:
도메인은 설정으로, 코드에는 없음).

**측정 결과: 일괄 적용은 기각, 기본 꺼짐.** 2026-09-05, 구어체 픽스처 10문항,
배포 설정(top_n 14 / limit 20):

    변형                          recall  anchor
    원문(기준선)                   0.500   0.600
    v1 재작성(용어로 대체)          0.400   0.300
    v2 재작성(명사 보존+용어 추가)  0.400   0.500

원인: 이 픽스처의 앵커는 분류표의 구체 명사(치킨·사료·게스트하우스)이고, 재작성이
더하는 일반 용어는 sparse 팔의 표를 분산시킨다. 명사를 보존해도(v2) 못 이겼다.
실사고("학회 발표")의 진짜 처방은 약근거 재시도의 확장 모델을 추론 모델로 올리는
것이었다 - QUERY_EXPANSION_RETRY_MODEL(config.py)의 실측 참조. 재도전 조건:
"대체"가 아니라 약근거일 때 "추가 질의로 union"하는 변형은 미측정이다.

RETRIEVAL_RECAST(env)로 켜고, 켜기 전에 scripts/eval_retrieval.py로 잰다.
실패·타임아웃·이상 출력은 전부 "원문 그대로"로 강등된다 - 게이트와 같은 계약.
"""

import asyncio
import logging

from app.llm.base import ChatMessage, LLMProvider

logger = logging.getLogger("mopan.retrieval")

_SYSTEM = (
    "You rewrite search queries for a document-retrieval system over regulations, statutes and "
    "reference manuals. If the user's message NARRATES THEIR OWN SITUATION and asks what it means "
    "for them (e.g. \"내가 ~를 했는데 ~해도 될까?\"), rewrite it as ONE search query: KEEP EVERY "
    "concrete noun from the message exactly as written (product names, places, actions - these are "
    "what the index matches), and APPEND the legal/technical terms of art such documents would use "
    "for that situation. Never replace a specific noun with a category word. One line, Korean, no "
    "quotes, no explanation. If the message is already phrased in the documents' own vocabulary, "
    "reply with exactly: pass"
)

# 재작성 질의의 상한. 이보다 길면 모델이 설명을 붙인 것이므로 버린다.
_MAX_QUERY_CHARS = 120


async def recast_query(
    llm_provider: LLMProvider,
    question: str,
    *,
    model: str,
    timeout: float,
) -> str | None:
    """상황 서술이면 용어 질의를, 아니면(또는 어떤 실패든) None을 돌려준다."""
    try:
        result = await asyncio.wait_for(
            llm_provider.chat(
                [
                    ChatMessage(role="system", content=_SYSTEM),
                    ChatMessage(role="user", content=question),
                ],
                temperature=0.0,
                model=model,
            ),
            timeout=timeout,
        )
    except Exception:
        logger.warning("query recast failed; searching with the question as asked", exc_info=True)
        return None

    text = (result.content or "").strip().strip('"')
    if not text or text.lower() == "pass" or "\n" in text or len(text) > _MAX_QUERY_CHARS:
        return None
    return text
