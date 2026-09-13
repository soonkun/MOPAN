"""임베딩 전용 서버(vLLM pooling)로 갈 때의 세 가지: 클라이언트 선택, MRL 폴백(전체 폭이 오면 자르고
정규화), 모델 존재 확인이 Ollama가 아닌 서버에서도 된다."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

from app.llm import embedding_profiles as profiles_module
from app.llm.embedding_profiles import local_model_present
from app.llm.openai_provider import OpenAIProvider, _truncate_normalise


def test_truncate_normalise_is_prefix_then_unit_length():
    v = _truncate_normalise([3.0, 4.0, 100.0, 100.0], 2)
    assert v == [0.6, 0.8]
    assert _truncate_normalise([0.0, 0.0], 2) == [0.0, 0.0]


async def test_embeddings_go_to_the_embedding_server_and_are_cut_to_the_configured_width():
    p = OpenAIProvider(
        api_key="k", embedding_model="qwen3-embedding:8b", answer_model="gpt-4o", embedding_dim=2,
        local_base_url="http://ollama:11434/v1", embedding_base_url="http://vllm-embed:8003/v1", embedding_provider="local",
    )
    assert p.embedding_client is not None and p.embedding_client is not p.local_client
    fake = AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[3.0, 4.0, 9.0, 9.0])]))
    p.embedding_client.embeddings.create = fake  # type: ignore[method-assign]
    p.local_client.embeddings.create = AsyncMock(side_effect=AssertionError("Ollama로 가면 안 된다"))  # type: ignore[union-attr]
    vectors = await p.embed(["x"])
    assert vectors == [[0.6, 0.8]]
    assert "dimensions" not in fake.call_args.kwargs  # vLLM은 dimensions를 거절한다 - 자르기는 우리가


async def test_model_presence_falls_back_to_v1_models_when_the_server_is_not_ollama(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(404)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen3-embedding:8b"}]})
        return httpx.Response(500)

    real = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(profiles_module.httpx, "AsyncClient", fake_client)
    assert await local_model_present("http://vllm-embed:8003/v1", "qwen3-embedding:8b") is True
    assert await local_model_present("http://vllm-embed:8003/v1", "other") is False
