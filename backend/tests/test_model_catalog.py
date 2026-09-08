"""모델 카탈로그 - .env 목록 위에 llm_models 행을 덧씌운다. 빈 표 = 예전 동작."""
import pytest

from app.core.config import Settings
from app.llm.catalog import load_catalog, upsert_row


def _settings(**over) -> Settings:
    return Settings(openai_api_key="x", answer_model="gpt-4o", answer_models=["gpt-4o-mini", "gpt-5.6-terra"], **over)


async def test_empty_table_behaves_like_env(db):
    catalog = await load_catalog(db, _settings())
    assert [m.id for m in catalog.enabled] == ["gpt-4o", "gpt-4o-mini", "gpt-5.6-terra"]
    assert catalog.default == "gpt-4o"
    assert catalog.supports_reasoning("gpt-5.6-terra") and not catalog.supports_reasoning("gpt-4o")
    assert catalog.supports_vision("gpt-4o")


async def test_rows_override_permission_default_and_flags(db):
    await upsert_row(db, "gpt-4o-mini", enabled=False)
    await upsert_row(db, "gpt-5.6-terra", is_default=True, supports_vision=False)
    await upsert_row(db, "gemma4:26b", provider="local", enabled=True)
    catalog = await load_catalog(db, _settings())
    assert not catalog.is_allowed("gpt-4o-mini")
    assert catalog.default == "gpt-5.6-terra"
    assert catalog.supports_vision("gpt-5.6-terra") is False
    local = catalog.get("gemma4:26b")
    assert local.provider == "local" and local.enabled and local.supports_vision and not local.from_env


async def test_default_falls_back_when_default_row_is_disabled(db):
    await upsert_row(db, "gpt-4o", enabled=False)
    catalog = await load_catalog(db, _settings())
    assert catalog.default == "gpt-4o-mini"


async def test_only_one_default_row(db):
    await upsert_row(db, "gpt-4o-mini", is_default=True)
    await upsert_row(db, "gpt-5.6-terra", is_default=True)
    catalog = await load_catalog(db, _settings())
    assert [m.id for m in catalog.models if m.is_default] == ["gpt-5.6-terra"]


def test_provider_routes_ollama_style_names_locally():
    from app.llm.openai_provider import OpenAIProvider

    p = OpenAIProvider(api_key="x", embedding_model="e", answer_model="gpt-4o", local_base_url="http://127.0.0.1:1/v1")
    assert p._client_for("gemma4:26b") is p.local_client
    assert p._client_for("gpt-4o") is p.client
    p.local_model_names = {"my-vllm-model"}
    assert p._client_for("my-vllm-model") is p.local_client
    p.reasoning_model_names = {"gpt-oss:120b"}
    assert p._is_reasoning("gpt-oss:120b") and not p._is_reasoning("gpt-5.6-terra")
