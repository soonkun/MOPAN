"""사례 질의 재작성(recast_query)의 강등 계약과, 추론 모델 판별."""

from unittest.mock import AsyncMock

from app.core.config import model_supports_reasoning
from app.llm.base import ChatResult
from app.retrieval.recast import recast_query


def provider_saying(content: str):
    p = AsyncMock()
    p.chat = AsyncMock(return_value=ChatResult(content=content))
    return p


async def test_a_case_narration_becomes_a_terminology_query():
    p = provider_saying("학회 발표 특허출원 공지예외적용 신규성 상실")
    out = await recast_query(p, "내가 학회가서 발표를 했는데...", model="m", timeout=5)
    assert out == "학회 발표 특허출원 공지예외적용 신규성 상실"


async def test_pass_and_every_failure_mean_the_question_as_asked():
    # 계약: 어떤 실패도 None(원문 사용)으로 강등된다.
    assert await recast_query(provider_saying("pass"), "질문", model="m", timeout=5) is None
    assert await recast_query(provider_saying(""), "질문", model="m", timeout=5) is None
    assert (
        await recast_query(provider_saying("두 줄이면\n설명을 붙인 것"), "질문", model="m", timeout=5)
        is None
    )
    broken = AsyncMock()
    broken.chat = AsyncMock(side_effect=RuntimeError("down"))
    assert await recast_query(broken, "질문", model="m", timeout=5) is None


def test_reasoning_family_detection():
    """gpt-5 계열은 추론, -chat- 변형은 비추론. 거짓 양성(비추론에 effort 전송)이
    원문 400 사고이므로 chat 배제가 계약이다."""
    assert model_supports_reasoning("gpt-5.6-terra")
    assert model_supports_reasoning("gpt-5.6-luna")
    assert model_supports_reasoning("o3-mini")
    assert not model_supports_reasoning("gpt-4o")
    assert not model_supports_reasoning("gpt-4o-mini")
    assert not model_supports_reasoning("gpt-5-chat-latest")
