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


def test_now_line_uses_the_client_clock_and_degrades_to_the_default():
    """"올해"가 학습 시점으로 풀리던 실사고의 계약: 브라우저 시간대가 먼저,
    이상한 값은 배포 기본값으로 강등, 어느 쪽이든 오늘 날짜가 실린다."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.core.localtime import now_line

    seoul = now_line("Asia/Seoul", "UTC")
    assert "Asia/Seoul" in seoul
    assert f"{datetime.now(ZoneInfo('Asia/Seoul')):%Y-%m-%d}" in seoul

    degraded = now_line("Not/AZone", "Asia/Seoul")
    assert "Asia/Seoul" in degraded


async def test_a_followup_reply_becomes_a_self_contained_search_question():
    """"소셜네트워크용이야"의 실사고: 되묻기의 답 여섯 글자로 검색이 나가
    분류표에 닿지 못하고 근거 0개 답이 나갔다. 압축은 이력을 읽는다."""
    from app.chat.condense import condense_followup

    p = provider_saying("소셜네트워크용 어플 이름의 상표 등록은 몇 류로 출원하나요?")
    out = await condense_followup(
        p,
        [
            {"role": "user", "content": "어플 이름을 상표로 등록하려는데 몇 류인가요?"},
            {"role": "assistant", "content": "어플이 어떤 용도로 사용되는지 알려주세요."},
        ],
        "소셜네트워크용이야.",
        model="m",
        timeout=5,
    )
    assert out == "소셜네트워크용 어플 이름의 상표 등록은 몇 류로 출원하나요?"
    # 이력이 모델에게 실제로 실렸는가.
    sent = p.chat.call_args.args[0]
    assert [m.role for m in sent] == ["system", "user", "assistant", "user"]


async def test_condense_degrades_to_the_question_as_asked():
    from app.chat.condense import condense_followup

    history = [{"role": "user", "content": "질문"}]
    assert await condense_followup(provider_saying("pass"), history, "자립형 질문", model="m", timeout=5) is None
    assert await condense_followup(provider_saying("두 줄\n설명"), history, "q", model="m", timeout=5) is None
    broken = AsyncMock()
    broken.chat = AsyncMock(side_effect=RuntimeError("down"))
    assert await condense_followup(broken, history, "q", model="m", timeout=5) is None
    # 첫 턴(이력 없음)은 모델 호출 자체가 없다 - 비용 0.
    fresh = provider_saying("무엇이든")
    assert await condense_followup(fresh, [], "q", model="m", timeout=5) is None
    fresh.chat.assert_not_called()
