"""vLLM(두 번째 로컬 서버) 라우팅 - 그쪽이 내는 이름만 그쪽으로, 나머지 로컬은 Ollama로."""
import httpx

from app.llm import catalog as catalog_module
from app.llm.catalog import discover_vllm_models
from app.llm.openai_provider import OpenAIProvider


def _provider(vllm: str = "http://vllm:8001/v1") -> OpenAIProvider:
    return OpenAIProvider(
        api_key="k", embedding_model="e", answer_model="gpt-4o",
        local_base_url="http://ollama:11434/v1", vllm_base_url=vllm,
    )


def test_vllm_names_route_to_vllm_and_other_local_names_to_ollama():
    p = _provider()
    p.vllm_model_names = {"gemma4:26b"}
    assert p._client_for("gemma4:26b") is p.vllm_client
    assert p._client_for("gemma4:e4b") is p.local_client
    assert p._client_for("gpt-4o") is p.client
    assert p._is_local("gemma4:26b")


def test_without_a_vllm_url_everything_local_goes_to_ollama():
    p = _provider(vllm="")
    p.vllm_model_names = {"gemma4:26b"}  # 발견은 됐어도 클라이언트가 없으면 Ollama
    assert p.vllm_client is None
    assert p._client_for("gemma4:26b") is p.local_client


async def test_discovery_reads_v1_models_and_degrades_to_empty(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "dead":
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"id": "gemma4:26b"}, {"id": ""}]})

    real = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", fake_client)
    assert await discover_vllm_models("http://vllm:8001/v1") == {"gemma4:26b"}
    assert await discover_vllm_models("http://dead:8001/v1") == set()
    assert await discover_vllm_models("") == set()
