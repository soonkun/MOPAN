"""GPT-5.6 계열의 reasoning_effort 적응 - 실측 400 두 건을 고정한다."""
from app.llm.openai_provider import adapt_reasoning_effort


def test_gpt56_minimal_becomes_none_and_tools_force_none():
    assert adapt_reasoning_effort("gpt-5.6-terra", "minimal", has_tools=False) == "none"
    assert adapt_reasoning_effort("gpt-5.6-luna", "high", has_tools=False) == "high"
    assert adapt_reasoning_effort("gpt-5.6-terra", "high", has_tools=True) == "none"
    # 다른 계열은 손대지 않는다 - gpt-5는 minimal을 받고, gpt-4o는 호출부가 이미 버린다.
    assert adapt_reasoning_effort("gpt-5", "minimal", has_tools=True) == "minimal"
    assert adapt_reasoning_effort("gpt-5.6-terra", None, has_tools=True) == "none"
    assert adapt_reasoning_effort("gpt-5.6-terra", None, has_tools=False) is None


def test_local_non_reasoning_model_gets_effort_none_to_disable_thinking():
    """gemma4(Ollama)는 effort 없이 부르면 생각 토큰만 쓰다 끝난다 - none을 명시해 끈다(실측)."""
    from unittest.mock import AsyncMock

    from app.llm.base import ChatMessage
    from app.llm.openai_provider import OpenAIProvider

    p = OpenAIProvider(api_key="x", embedding_model="e", answer_model="gpt-4o", local_base_url="http://127.0.0.1:1/v1")
    p.local_model_names = {"gemma4:26b", "gpt-oss:120b"}
    p.reasoning_model_names = {"gpt-oss:120b"}
    captured = {}

    async def fake_create(**request):
        captured.update(request)
        raise RuntimeError("stop")

    p.local_client.chat.completions.create = AsyncMock(side_effect=fake_create)
    import asyncio, pytest

    with pytest.raises(Exception):
        asyncio.run(p.chat([ChatMessage(role="user", content="hi")], model="gemma4:26b"))
    assert captured["extra_body"]["reasoning_effort"] == "none"
    captured.clear()
    with pytest.raises(Exception):
        asyncio.run(p.chat([ChatMessage(role="user", content="hi")], model="gpt-oss:120b", reasoning_effort="low"))
    assert captured["extra_body"]["reasoning_effort"] == "low"
