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


def test_muse_takes_reasoning_strength_from_the_system_prompt():
    """Muse Glimmer는 reasoning_effort를 무시하고 시스템 글의 'Reasoning strength'만 본다. 고르지 않으면 low."""
    from app.llm.openai_provider import system_reasoning_strength, with_system_line

    assert system_reasoning_strength("gemma4:26b", "high") is None
    assert [system_reasoning_strength("muse-glimmer:30b", e) for e in (None, "none", "minimal", "low", "medium", "high")] == [
        "low", "low", "low", "low", "medium", "high"]
    original = [{"role": "system", "content": "규칙"}, {"role": "user", "content": "질문"}]
    assert with_system_line(original, "Reasoning strength: low")[0]["content"] == "Reasoning strength: low\n\n규칙"
    assert original[0]["content"] == "규칙"  # 원본은 그대로
    assert with_system_line([{"role": "user", "content": "질문"}], "L")[0] == {"role": "system", "content": "L"}
