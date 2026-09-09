"""임베딩 프로필: 키 하나가 provider·model·dim을 정하고, 모르는 키는 거절한다."""
import pytest

from app.core.config import Settings
from app.llm.embedding_profiles import PROFILES, current_profile_key, ordered_profiles


def test_profile_sets_provider_model_dim():
    s = Settings(openai_api_key="x", embedding_profile="bge-m3", local_llm_base_url="http://127.0.0.1:11434/v1")
    assert (s.embedding_provider, s.embedding_model, s.embedding_dim) == ("local", "bge-m3", 1024)
    s = Settings(openai_api_key="x", embedding_profile="openai")
    assert (s.embedding_provider, s.embedding_model, s.embedding_dim) == ("openai", "text-embedding-3-large", 1536)
    with pytest.raises(ValueError):
        Settings(openai_api_key="x", embedding_profile="nope")


def test_profiles_are_ordered_and_recognisable():
    assert [p.key for p in ordered_profiles()] == ["qwen3", "openai", "bge-m3", "arctic"]
    assert current_profile_key("local", "qwen3-embedding:8b") == "qwen3"
    assert current_profile_key("openai", "text-embedding-3-small") is None
    assert all(("로컬 GPU" in p.badges) == (p.provider == "local") for p in PROFILES.values())
    assert "API 비용 발생" in PROFILES["openai"].badges
