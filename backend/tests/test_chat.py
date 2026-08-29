import json
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.config import Settings
from app.llm.base import ChatResult, LLMError
from app.models.chunk import EMBEDDING_DIM
from app.models.message import Message
from app.models.user import User

IDLE_IN_TRANSACTION = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND state = 'idle in transaction'"
)


def vec(*leading: float) -> list[float]:
    return list(leading) + [0.0] * (EMBEDDING_DIM - len(leading))


def make_fake_llm() -> AsyncMock:
    provider = AsyncMock()
    provider.embed = AsyncMock(return_value=[vec(1.0)])
    provider.chat = AsyncMock(
        return_value=ChatResult(content="Here is the answer.", usage={"total_tokens": 42}, model="gpt-4o")
    )
    return provider


def parse_sse(text_body: str) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in text_body.splitlines() if line.startswith("data: ")]


@pytest.fixture
def fake_llm(app):
    provider = make_fake_llm()
    app.state.llm_provider = provider
    return provider


@pytest_asyncio.fixture
async def logged_in(client, fake_llm):
    await client.post("/api/auth/register", json={"email": "chat@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "chat@example.com", "password": "pw123456"})
    return client


async def start_conversation(client, message: str = "hello") -> str:
    response = await client.post("/api/chat", json={"message": message})
    return parse_sse(response.text)[-1]["conversation_id"]


# --- The SSE contract --------------------------------------------------------


async def test_chat_streams_status_then_done(logged_in):
    response = await logged_in.post("/api/chat", json={"message": "What is MOPAN?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types[0] == "status" and events[0]["status"] == "searching"
    assert "answering" in [e.get("status") for e in events]
    assert types[-1] == "done"
    assert events[-1]["content"] == "Here is the answer."
    assert uuid.UUID(events[-1]["conversation_id"])


async def test_the_stream_carries_nothing_the_client_did_not_ask_for(logged_in):
    """Status and sources are visible; the prompt, the evidence fence and any
    reasoning the model was told not to produce are not."""
    response = await logged_in.post("/api/chat", json={"message": "What is MOPAN?"})

    events = parse_sse(response.text)
    assert {e["type"] for e in events} <= {"status", "token", "citations", "done", "error"}
    assert "<<EVIDENCE" not in response.text
    assert "You are MOPAN's assistant" not in response.text


async def test_chat_persists_the_turn_with_trace_fields(logged_in, db):
    conversation_id = await start_conversation(logged_in)

    rows = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == uuid.UUID(conversation_id))
            .order_by(Message.created_at)
        )
    ).all()
    assert [m.role for m in rows] == ["user", "assistant"]
    assistant = rows[1]
    assert assistant.model == "gpt-4o"
    assert assistant.usage == {"total_tokens": 42}
    assert assistant.latency_ms is not None
    assert assistant.retrieval_ms is not None
    assert assistant.prompt_name == "answer_agent"


async def test_message_order_is_stable_across_turns(logged_in):
    conversation_id = await start_conversation(logged_in, "first question")
    await logged_in.post("/api/chat", json={"conversation_id": conversation_id, "message": "second question"})

    messages = (await logged_in.get(f"/api/conversations/{conversation_id}/messages")).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "first question"


async def test_conversations_list_is_ordered_by_recent_use(logged_in):
    first = await start_conversation(logged_in, "old")
    second = await start_conversation(logged_in, "new")
    await logged_in.post("/api/chat", json={"conversation_id": first, "message": "revived"})

    conversations = (await logged_in.get("/api/conversations")).json()
    assert conversations[0]["id"] == first
    assert conversations[1]["id"] == second


# --- Authentication and authorization ----------------------------------------


async def test_chat_requires_auth(client):
    assert (await client.post("/api/chat", json={"message": "hi"})).status_code == 401


async def test_search_requires_auth(client):
    assert (await client.post("/api/search", json={"query": "x"})).status_code == 401


async def test_every_conversation_route_requires_auth(client):
    conversation_id = uuid.uuid4()
    assert (await client.get("/api/conversations")).status_code == 401
    assert (await client.get(f"/api/conversations/{conversation_id}/messages")).status_code == 401
    assert (await client.delete(f"/api/conversations/{conversation_id}")).status_code == 401


async def test_another_users_conversation_is_404_on_every_route(logged_in, app, fake_llm):
    """404, not 403: a 403 would confirm the conversation id exists."""
    conversation_id = await start_conversation(logged_in, "private")

    await logged_in.post("/api/auth/register", json={"email": "other@example.com", "password": "pw123456"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        await other.post("/api/auth/login", json={"email": "other@example.com", "password": "pw123456"})
        assert (await other.get(f"/api/conversations/{conversation_id}/messages")).status_code == 404
        assert (await other.delete(f"/api/conversations/{conversation_id}")).status_code == 404
        # 404 rather than a 200 carrying an error frame: the ownership check runs
        # before the response starts, so the status code is still ours to set.
        posted = await other.post("/api/chat", json={"conversation_id": conversation_id, "message": "hijack"})
        assert posted.status_code == 404
        assert (await other.get("/api/conversations")).json() == []


async def test_chat_with_an_unknown_conversation_id_is_404(logged_in):
    """Indistinguishable from someone else's id, which is the point."""
    response = await logged_in.post("/api/chat", json={"conversation_id": str(uuid.uuid4()), "message": "hi"})
    assert response.status_code == 404


async def test_deleting_a_conversation_takes_its_messages_with_it(logged_in, db):
    conversation_id = await start_conversation(logged_in)

    assert (await logged_in.delete(f"/api/conversations/{conversation_id}")).status_code == 204

    assert (await logged_in.get("/api/conversations")).json() == []
    assert (await logged_in.get(f"/api/conversations/{conversation_id}/messages")).status_code == 404
    rows = (
        await db.scalars(select(Message).where(Message.conversation_id == uuid.UUID(conversation_id)))
    ).all()
    assert rows == []


# --- Failure and disconnection -----------------------------------------------


async def test_llm_failure_is_reported_as_an_error_event(logged_in, fake_llm, db):
    fake_llm.chat = AsyncMock(side_effect=LLMError("boom"))
    response = await logged_in.post("/api/chat", json={"message": "hi"})

    last = parse_sse(response.text)[-1]
    assert last["type"] == "error"
    assert "boom" not in last["detail"]  # internals never reach the client
    assert "Traceback" not in response.text
    # The conversation exists and is empty rather than half-written.
    conversation_id = (await logged_in.get("/api/conversations")).json()[0]["id"]
    assert (await logged_in.get(f"/api/conversations/{conversation_id}/messages")).json() == []


async def test_no_connection_is_idle_in_transaction_across_the_llm_call(logged_in, fake_llm, test_engine):
    """Nothing may sit idle-in-transaction across the LLM round trip: not a
    session the generator opens (each is its own `async with`), and not the
    request's own auth session, whose SELECT in get_current_user autobegan one.
    Instrumented at the server rather than reasoned about - pg_stat_activity read
    from a second connection at the moment chat() is entered - because what saves
    the auth session is a FastAPI lifecycle detail (since 0.106 a yield-dependency
    exits before the response body is sent), and a version bump could move it."""
    observed = {}

    async def spy_chat(messages, **kwargs):
        async with test_engine.connect() as probe:
            observed["idle_in_transaction"] = await probe.scalar(IDLE_IN_TRANSACTION)
        return ChatResult(content="ok", usage={"total_tokens": 1}, model="gpt-4o")

    fake_llm.chat = spy_chat
    response = await logged_in.post("/api/chat", json={"message": "hi"})

    assert parse_sse(response.text)[-1]["type"] == "done"
    assert observed["idle_in_transaction"] == 0


async def test_a_client_disconnect_mid_stream_leaves_no_connection_and_no_half_turn(
    db, test_sessionmaker, test_engine
):
    """Closing the generator is exactly what Starlette does to it when the client
    goes away. Every session lives inside an `async with`, so the connection goes
    back whatever happens, and nothing is persisted before the LLM call."""
    from app.chat.router import chat
    from app.schemas.chat import ChatRequest

    user = User(email="disconnect@example.com", password_hash="x", role="user")
    db.add(user)
    await db.commit()

    response = await chat(
        payload=ChatRequest(message="hi"),
        user=user,
        db=db,
        llm_provider=make_fake_llm(),
        sessionmaker=test_sessionmaker,
        settings=Settings(),
    )
    events = response.body_iterator
    assert "searching" in await events.__anext__()
    assert "answering" in await events.__anext__()
    await events.aclose()

    await db.close()
    async with test_engine.connect() as probe:
        assert await probe.scalar(IDLE_IN_TRANSACTION) == 0
    assert (await db.scalars(select(Message))).all() == []


# --- Search ------------------------------------------------------------------


async def test_search_endpoint_returns_evidence(logged_in):
    response = await logged_in.post("/api/search", json={"query": "tomato"})
    assert response.status_code == 200
    assert response.json()["query"] == "tomato"
    assert isinstance(response.json()["results"], list)


async def test_search_top_n_is_bounded(logged_in):
    assert (await logged_in.post("/api/search", json={"query": "x", "top_n": 0})).status_code == 422
    assert (await logged_in.post("/api/search", json={"query": "x", "top_n": 51})).status_code == 422


async def test_search_passes_its_collection_scope_to_retrieval(logged_in, monkeypatch):
    """Collection scoping is not decoration: without it /api/search answers from
    the whole corpus while /api/chat answers from the requested collections."""
    seen = {}

    async def spy_retrieve(db, vector_store, llm_provider, reranker, question, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr("app.chat.router.retrieve", spy_retrieve)
    collection_id = str(uuid.uuid4())
    response = await logged_in.post(
        "/api/search", json={"query": "x", "collection_ids": [collection_id], "top_n": 3}
    )

    assert response.status_code == 200
    assert [str(c) for c in seen["collection_ids"]] == [collection_id]
    assert seen["settings"].retrieval_top_n == 3
