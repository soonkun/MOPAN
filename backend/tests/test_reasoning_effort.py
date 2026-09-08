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
