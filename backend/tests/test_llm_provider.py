import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import APITimeoutError

from app.llm.base import ChatMessage, LLMError
from app.llm.openai_provider import OpenAIProvider


def _provider(**kwargs) -> OpenAIProvider:
    return OpenAIProvider(
        api_key="test-key",
        embedding_model="text-embedding-3-small",
        answer_model="gpt-4o",
        **kwargs,
    )


def _embedding_response(vectors, indices=None):
    """A stand-in for CreateEmbeddingResponse.

    `index` is set explicitly because the provider sorts on it: OpenAI's wire
    format carries a per-item index precisely because array order is not part of
    the contract, and openai 1.47.0 hands back response.data in whatever order
    the server sent.
    """
    response = MagicMock()
    if indices is None:
        indices = range(len(vectors))
    response.data = [MagicMock(embedding=v, index=i) for v, i in zip(vectors, indices, strict=True)]
    return response


async def test_embed_returns_vectors_from_the_response():
    provider = _provider()
    provider.client.embeddings.create = AsyncMock(return_value=_embedding_response([[0.1, 0.2], [0.3, 0.4]]))

    assert await provider.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]
    provider.client.embeddings.create.assert_awaited_once_with(
        model="text-embedding-3-small", input=["a", "b"]
    )


async def test_embed_splits_into_batches_by_item_count():
    """A 300-page PDF exceeds the endpoint's per-request array limit."""
    provider = _provider(batch_size=2)
    provider.client.embeddings.create = AsyncMock(
        side_effect=[_embedding_response([[0.0], [0.0]]), _embedding_response([[0.0]])]
    )

    result = await provider.embed(["a", "b", "c"])

    assert len(result) == 3
    assert provider.client.embeddings.create.await_count == 2


async def test_embed_splits_into_batches_by_character_budget():
    provider = _provider(batch_size=100, batch_chars=10)
    provider.client.embeddings.create = AsyncMock(
        side_effect=[_embedding_response([[0.0]]), _embedding_response([[0.0]])]
    )

    await provider.embed(["x" * 8, "y" * 8])
    assert provider.client.embeddings.create.await_count == 2


async def test_a_single_input_over_the_character_budget_still_goes_out():
    """The batcher cannot split one text without breaking the caller's
    text/vector correspondence, so an over-budget input is sent alone rather
    than dropped or looped on. MAX_CHUNK_TOKENS caps every real chunk at half
    the 8191-token per-input limit, so this is a guard, not a hot path."""
    provider = _provider(batch_chars=10)
    provider.client.embeddings.create = AsyncMock(
        side_effect=[_embedding_response([[0.0]]), _embedding_response([[1.0]])]
    )

    assert await provider.embed(["x" * 50, "y"]) == [[0.0], [1.0]]
    assert [c.kwargs["input"] for c in provider.client.embeddings.create.await_args_list] == [
        ["x" * 50],
        ["y"],
    ]


async def test_embed_keeps_vectors_aligned_with_their_inputs_across_batches():
    """The single failure this module cannot afford.

    Every vector is written to the chunk at the same list position, so any
    reorder pairs each chunk with another chunk's embedding - retrieval then
    returns confidently wrong citations, and nothing downstream can detect it.
    Two independent reorder sources are covered: batch boundaries (the provider
    must concatenate batches in request order) and within a batch (openai 1.47.0
    returns response.data in the server's array order, verified by feeding a
    reversed array through httpx.MockTransport and observing indices [2, 1, 0]).
    """
    texts = [f"chunk-{i}" for i in range(7)]
    expected = {t: [float(i)] for i, t in enumerate(texts)}

    async def reversing_create(*, model, input):
        # Correct vectors, correct indices, deliberately reversed array order.
        pairs = [(i, expected[t]) for i, t in enumerate(input)]
        pairs.reverse()
        return _embedding_response([v for _, v in pairs], [i for i, _ in pairs])

    provider = _provider(batch_size=3)
    provider.client.embeddings.create = AsyncMock(side_effect=reversing_create)

    assert provider.client.embeddings.create.await_count == 0
    result = await provider.embed(texts)

    assert provider.client.embeddings.create.await_count == 3  # 3 + 3 + 1
    assert result == [expected[t] for t in texts]


async def test_embed_rejects_a_response_that_does_not_cover_every_input():
    """openai 1.47.0 does not check the array length itself: three inputs and a
    one-item response parse without error. Unchecked, one short batch shifts
    every later batch's vectors by one relative to the chunk list."""
    provider = _provider()
    provider.client.embeddings.create = AsyncMock(return_value=_embedding_response([[0.0]]))

    with pytest.raises(LLMError, match="0..2"):
        await provider.embed(["a", "b", "c"])


async def test_embed_rejects_a_response_with_duplicate_indices():
    provider = _provider()
    provider.client.embeddings.create = AsyncMock(
        return_value=_embedding_response([[0.0], [1.0]], indices=[0, 0])
    )

    with pytest.raises(LLMError):
        await provider.embed(["a", "b"])


async def test_embed_rejects_vectors_of_the_wrong_width():
    """EMBEDDING_MODEL and EMBEDDING_DIM are independent settings. Pointing the
    model at text-embedding-3-large while EMBEDDING_DIM stays 1536 otherwise
    fails at the pgvector insert, after paying to embed the whole document."""
    provider = _provider(embedding_dim=3)
    provider.client.embeddings.create = AsyncMock(
        return_value=_embedding_response([[0.1, 0.2, 0.3], [0.4, 0.5]])
    )

    with pytest.raises(LLMError, match="EMBEDDING_DIM"):
        await provider.embed(["a", "b"])


async def test_embed_of_nothing_makes_no_request():
    provider = _provider()
    provider.client.embeddings.create = AsyncMock()
    assert await provider.embed([]) == []
    provider.client.embeddings.create.assert_not_awaited()


async def test_sdk_errors_are_wrapped_in_llm_error():
    provider = _provider()
    provider.client.embeddings.create = AsyncMock(side_effect=APITimeoutError(request=MagicMock()))
    with pytest.raises(LLMError):
        await provider.embed(["a"])


async def test_the_configured_timeout_fires():
    """No network: a loopback server that accepts the connection and never
    answers. httpx.MockTransport cannot stand in here - it bypasses the timeout
    entirely, so a mocked test would pass against a provider with no timeout at
    all, which is exactly the 600s-default regression this guards."""
    stop = asyncio.Event()

    async def blackhole(reader, writer):
        await stop.wait()

    server = await asyncio.start_server(blackhole, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    provider = _provider(timeout=0.3, max_retries=0)
    provider.client.base_url = f"http://127.0.0.1:{port}/v1"

    started = time.perf_counter()
    try:
        with pytest.raises(LLMError):
            await provider.embed(["a"])
        assert time.perf_counter() - started < 5.0
    finally:
        stop.set()
        server.close()
        await server.wait_closed()
        await provider.aclose()


async def test_retries_are_bounded_and_skip_non_retryable_statuses():
    """A 429 or 5xx is worth another attempt; a 401 is a bad key and retrying it
    just multiplies the failure. openai 1.47.0 draws that line itself - this
    pins that the provider hands it the retry budget from Settings."""
    for status, expected_attempts in ((429, 3), (500, 3), (401, 1), (400, 1)):
        attempts: list[str] = []

        def handler(request, attempts=attempts, status=status):
            attempts.append(request.url.path)
            return httpx.Response(status, json={"error": {"message": "nope"}})

        provider = _provider(max_retries=2)
        assert provider.client.max_retries == 2
        provider.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        with pytest.raises(LLMError):
            await provider.embed(["a"])
        assert len(attempts) == expected_attempts, status
        await provider.aclose()


async def test_settings_values_reach_the_sdk_client():
    provider = _provider(timeout=12.5, max_retries=7)
    assert provider.client.timeout == 12.5
    assert provider.client.max_retries == 7
    await provider.aclose()


async def test_chat_returns_content_usage_and_model():
    provider = _provider()
    message = MagicMock(content="hello there", tool_calls=None)
    usage = MagicMock()
    usage.model_dump.return_value = {"total_tokens": 42}
    provider.client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=message)], usage=usage, model="gpt-4o")
    )

    result = await provider.chat([ChatMessage(role="user", content="hi")])

    assert result.content == "hello there"
    assert result.usage == {"total_tokens": 42}
    assert result.model == "gpt-4o"
    assert result.tool_calls is None


async def test_chat_omits_the_tools_key_when_none_are_passed():
    provider = _provider()
    message = MagicMock(content="ok", tool_calls=None)
    provider.client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=message)], usage=None, model="gpt-4o")
    )

    await provider.chat([ChatMessage(role="user", content="hi")])

    kwargs = provider.client.chat.completions.create.await_args.kwargs
    assert "tools" not in kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


async def test_chat_surfaces_tool_calls_when_the_model_requests_one():
    """Unused in Slice 1; proves the Slice 2 seam actually works."""
    provider = _provider()
    tool_call = MagicMock(id="call_1")
    tool_call.function.name = "search"
    tool_call.function.arguments = '{"q": "x"}'
    message = MagicMock(content=None, tool_calls=[tool_call])
    provider.client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=message)], usage=None, model="gpt-4o")
    )

    result = await provider.chat(
        [ChatMessage(role="user", content="hi")],
        tools=[{"type": "function", "function": {"name": "search"}}],
    )

    assert result.tool_calls is not None
    assert result.tool_calls[0].name == "search"
