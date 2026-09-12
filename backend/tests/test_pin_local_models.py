"""pin_local_models: 켜 둔 로컬 모델마다 keep_alive=-1 로드 요청을 보내고, 하나가 죽어도 나머지는 시도한다."""

import httpx
import pytest

from app.llm import catalog as catalog_module
from app.llm.catalog import ollama_root, pin_local_models


def test_ollama_root_strips_v1():
    assert ollama_root("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"
    assert ollama_root("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"
    assert ollama_root("") == ""


@pytest.mark.asyncio
async def test_pins_every_model_and_survives_one_failure(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.32.5"})
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if body["model"] == "broken:1b":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"done": True})

    real = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", fake_client)
    pinned = await pin_local_models("http://ollama:11434/v1", ["gemma4:e4b", "broken:1b", "gemma4:26b"], "qwen3-embedding:8b")
    assert pinned == ["gemma4:e4b", "gemma4:26b", "qwen3-embedding:8b"]
    assert [p for p, _ in seen] == ["/api/generate", "/api/generate", "/api/generate", "/api/embed"]
    assert all(b["keep_alive"] == -1 for _, b in seen)


@pytest.mark.asyncio
async def test_no_base_url_means_nothing_to_do():
    assert await pin_local_models("", ["gemma4:e4b"], "x") == []


@pytest.mark.asyncio
async def test_a_server_without_the_ollama_api_is_left_alone(monkeypatch):
    """vLLM/llama.cpp 서버: /api/version이 404 → keep_alive를 보낼 곳이 없다."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404)

    real = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_module.httpx, "AsyncClient", fake_client)
    assert await pin_local_models("http://vllm:8000/v1", ["google/gemma-4-26b-it"], None) == []
    assert calls == ["/api/version"]
