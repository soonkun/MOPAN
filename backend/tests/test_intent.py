"""의도 게이트: 판정 파싱과 강등 계약, 그리고 answer()의 chat 경로."""

import pytest

from app.chat.intent import classify_intent
from app.chat.service import answer
from app.core.config import get_settings
from app.llm.base import ChatResult


class FakeProvider:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return ChatResult(content=self.reply, model="fake")

    async def embed(self, texts):
        raise AssertionError("의도 분류는 임베딩을 부르지 않는다")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("chat", "chat"),
        ("Chat", "chat"),
        ("search", "search"),
        ("SEARCH.", "search"),
        # 계약: 알 수 없는 출력은 전부 검색으로 강등 - 게이트가 최악의 경우
        # 할 수 있는 일은 아무것도 바꾸지 않는 것이어야 한다.
        ("잡담입니다", "search"),
        ("", "search"),
    ],
)
async def test_verdict_parsing_degrades_unknown_output_to_search(reply, expected):
    provider = FakeProvider(reply=reply)
    verdict = await classify_intent(provider, "안녕?", model="fake-mini", timeout=5.0)
    assert verdict == expected


async def test_a_classifier_crash_degrades_to_search():
    provider = FakeProvider(error=RuntimeError("boom"))
    verdict = await classify_intent(provider, "안녕?", model="fake-mini", timeout=5.0)
    assert verdict == "search"


async def test_chat_intent_answers_without_clarify_and_with_the_smalltalk_prompt():
    """근거가 비어 있어도 되묻기로 새지 않는다 - "안녕?"이 심사기준 되묻기를
    받던 실측 실패의 회귀 방지. 기록되는 prompt_name이 곧 게이트의 흔적이다."""
    provider = FakeProvider(reply="안녕하세요! 무엇을 도와드릴까요?")
    settings = get_settings().model_copy(update={"clarify_on_weak_evidence": True})

    result = await answer(provider, "안녕?", [], [], settings=settings, intent="chat")

    assert result.prompt_name == "smalltalk_agent"
    assert result.citations == []
    system_text = provider.calls[0][0][0].content
    assert "conversational" in system_text
