"""Slice 4 - agent management.

An agent is a SAVED CONFIGURATION: a name, a prompt from the prompt store, a set
of collections it may search, a set of MCP tools it may call, an answer model,
and whether the orchestrator runs. It is deliberately not code.

The property this whole file is arranged around:

    THE TWO LISTS ARE PERMISSION BOUNDARIES, NOT HINTS. A plan step naming a tool
    the agent does not carry is refused WHOLE - not filtered - and a retrieval
    restricted to the agent's collections cannot reach outside them even when the
    only answer is out there. Enforced in `load_available` and in `retrieve`,
    which is to say in the two functions that decide what a question may reach,
    not in the UI and not in the planner's prompt.

And the property that makes it deployable at all: an EMPTY `agents` table changes
nothing. Every "when there are no agents" test truncates the table in its own
body, because the database is session-scoped and a leftover row from another
module would let such a test pass with its guard removed.

NO TEST HERE MAKES A NETWORK CALL. The LLM is an AsyncMock; there is no MCP
server, only rows describing one.
"""

import json
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.agents.service import DEFAULT_AGENT, AgentScopeError, ResolvedAgent
from app.chat.prompt import ANSWER_SYSTEM_PROMPT
from app.chat.service import retrieve
from app.core.config import Settings
from app.llm.base import ChatResult
from app.models.agent import Agent
from app.models.chunk import EMBEDDING_DIM, Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.mcp import McpServer, McpTool
from app.models.message import Message
from app.models.prompt import Prompt
from app.models.user import User
from app.orchestrator.plan import PlanError, load_available, validate_plan
from app.retrieval.reranker import NoneReranker
from app.retrieval.vector_store import PgVectorStore

PUBLIC_URL = "http://93.184.216.34/mcp"

# The A chunk is about nitrogen fertiliser, the B chunk about a pesticide. They
# share no word, so "다이아지논" reaches B through BOTH the vector ranking (the
# stub embeds every query as B's vector) and the keyword ranking. A restriction
# that only happened to hide one of the two would still show the other.
A_TEXT = "질소 비료는 생육 초기에 나누어 준다"
B_TEXT = "다이아지논 유제는 수확 14일 전까지 살포한다"


def vec(*leading: float) -> list[float]:
    return list(leading) + [0.0] * (EMBEDDING_DIM - len(leading))


def settings_with(**overrides) -> Settings:
    return Settings().model_copy(update=overrides)


def parse_sse(body: str) -> list[dict]:
    return [json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")]


def make_fake_llm() -> AsyncMock:
    provider = AsyncMock()
    # Every query embeds to B's vector, so the vector ranking always prefers the
    # collection the agent is NOT allowed to reach. That is the point.
    provider.embed = AsyncMock(return_value=[vec(0.0, 1.0)])
    provider.chat = AsyncMock(
        return_value=ChatResult(content="답변입니다 [1].", usage={"total_tokens": 11}, model="gpt-4o")
    )
    return provider


@pytest.fixture
def fake_llm(app):
    provider = make_fake_llm()
    app.state.llm_provider = provider
    return provider


@pytest_asyncio.fixture
async def seeded_prompt(db):
    """0004's row, re-seeded. Every DB-touching test truncates `users` CASCADE,
    which takes `prompts` with it, so whether answer_agent exists depends on what
    ran before - exactly the note tests/test_prompts_admin.py already carries."""
    await db.execute(text("DELETE FROM prompts"))
    db.add(
        Prompt(
            name="answer_agent", version="1", text=ANSWER_SYSTEM_PROMPT, is_active=True, created_by=None
        )
    )
    await db.commit()


@pytest_asyncio.fixture
async def admin_client(client, fake_llm, seeded_prompt):
    """The first account to register is the bootstrap admin."""
    await client.post("/api/auth/register", json={"email": "agent-admin@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "agent-admin@example.com", "password": "pw123456"})
    return client


@pytest_asyncio.fixture
async def member_client(admin_client, app):
    """A second, non-admin account on its own cookie jar."""
    await admin_client.post(
        "/api/auth/register", json={"email": "agent-member@example.com", "password": "pw123456"}
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post(
            "/api/auth/login", json={"email": "agent-member@example.com", "password": "pw123456"}
        )
        yield ac


@pytest_asyncio.fixture
async def corpus(db, admin_client):
    """Two collections whose contents do not overlap, and one MCP server with a
    read tool and a write tool. Returned as plain ids, detached from the session
    the API's own requests use.

    Depends on `admin_client` rather than creating its own owner, and that is not
    incidental: the first account to REGISTER is the bootstrap admin, so a user
    row inserted here first would silently demote the admin fixture to a member
    in whichever tests happened to resolve this one earlier. Stating the
    dependency makes the order a fact instead of a signature convention."""
    await db.execute(text("TRUNCATE TABLE agents, agent_collections, agent_tools CASCADE"))
    user = await db.scalar(select(User).where(User.role == "admin"))
    collection_a = Collection(name="비료", created_by=user.id)
    collection_b = Collection(name="농약", created_by=user.id)
    db.add_all([collection_a, collection_b])
    await db.flush()

    def _doc(collection, name):
        return Document(
            collection_id=collection.id,
            filename=name,
            file_type="txt",
            size_bytes=1,
            storage_path="x",
            status="indexed",
            uploaded_by=user.id,
        )

    doc_a, doc_b = _doc(collection_a, "비료.pdf"), _doc(collection_b, "농약.pdf")
    db.add_all([doc_a, doc_b])
    await db.flush()
    db.add_all(
        [
            Chunk(
                document_id=doc_a.id,
                chunk_index=0,
                content=A_TEXT,
                token_count=8,
                char_count=len(A_TEXT),
                page=1,
                section=None,
                chunk_metadata={},
                embedding=vec(1.0, 0.0),
            ),
            Chunk(
                document_id=doc_b.id,
                chunk_index=0,
                content=B_TEXT,
                token_count=8,
                char_count=len(B_TEXT),
                page=1,
                section=None,
                chunk_metadata={},
                embedding=vec(0.0, 1.0),
            ),
        ]
    )
    server = McpServer(name="현장", base_url=PUBLIC_URL, created_by=user.id)
    db.add(server)
    await db.flush()
    read_tool = McpTool(server_id=server.id, name="lookup", input_schema={}, risk_level="read")
    write_tool = McpTool(server_id=server.id, name="record", input_schema={}, risk_level="write")
    db.add_all([read_tool, write_tool])
    await db.commit()
    return {
        "collection_a": collection_a.id,
        "collection_b": collection_b.id,
        "read_tool": read_tool.id,
        "write_tool": write_tool.id,
    }


async def create_agent(client, **overrides) -> dict:
    body = {"name": "테스트 에이전트"} | overrides
    response = await client.post("/api/agents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Admin only. Picking one is not.
# ---------------------------------------------------------------------------


async def test_a_non_admin_cannot_create_or_edit_an_agent(admin_client, member_client, corpus):
    """An agent decides which prompt answers and which corpus and tools a question
    may reach. That is the same authority Slice 1 put behind require_admin for
    documents, and for the same reason: it changes every other user's answers."""
    created = await create_agent(admin_client, name="관리자만")

    refused_create = await member_client.post("/api/agents", json={"name": "몰래"})
    assert refused_create.status_code == 403
    assert "관리자" in refused_create.json()["detail"]

    refused_edit = await member_client.patch(
        f"/api/agents/{created['id']}", json={"name": "바꿔치기"}
    )
    assert refused_edit.status_code == 403

    refused_delete = await member_client.delete(f"/api/agents/{created['id']}")
    assert refused_delete.status_code == 403

    refused_list = await member_client.get("/api/agents")
    assert refused_list.status_code == 403

    # And nothing actually changed.
    unchanged = (await admin_client.get("/api/agents")).json()
    assert [a["name"] for a in unchanged] == ["관리자만"]


async def test_any_authenticated_user_may_list_and_pick_a_selectable_agent(
    admin_client, member_client, corpus
):
    """The same argument GET /api/models makes: it returns exactly what
    POST /api/chat accepts, so it discloses nothing a user could not learn by
    picking one and being answered. It carries no collection or tool list -
    enumerating a boundary is how you tell somebody what to try next."""
    await create_agent(admin_client, name="현장 도우미", description="현장 질문용")

    listed = await member_client.get("/api/agents/selectable")
    assert listed.status_code == 200
    assert [a["name"] for a in listed.json()] == ["현장 도우미"]
    assert set(listed.json()[0]) == {"id", "name", "description", "answer_model", "orchestrator"}

    answered = await member_client.post(
        "/api/chat", json={"message": "질문", "agent_id": listed.json()[0]["id"]}
    )
    assert answered.status_code == 200


async def test_a_disabled_agent_can_neither_be_listed_nor_selected(admin_client, corpus):
    """Unlistable AND unnameable, the rule a disabled MCP tool already follows.
    The 409 is for the race - an admin turning it off while somebody is typing -
    not for the UI."""
    created = await create_agent(admin_client, name="중지된 에이전트")
    await admin_client.patch(f"/api/agents/{created['id']}", json={"enabled": False})

    assert (await admin_client.get("/api/agents/selectable")).json() == []
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": created["id"]}
    )
    assert refused.status_code == 409
    assert "중지" in refused.json()["detail"]


async def test_an_unknown_agent_id_is_a_404_before_anything_is_written(admin_client, corpus, db):
    """Resolved before the conversation exists, like the model and the attachment
    ids: a refusal after the StreamingResponse has begun would be an error frame
    inside a 200, and a titled empty conversation would be left in the sidebar."""
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": str(uuid.uuid4())}
    )
    assert refused.status_code == 404
    assert "에이전트" in refused.json()["detail"]
    assert (await db.scalar(text("SELECT count(*) FROM conversations"))) == 0


async def test_the_admin_form_refuses_what_the_chat_would_refuse(admin_client, corpus):
    """Every one of these is a Korean 400 on the form the admin is filling in
    rather than a refusal on somebody else's question three days later."""
    unknown_prompt = await admin_client.post(
        "/api/agents", json={"name": "a", "prompt_name": "없는_프롬프트"}
    )
    assert unknown_prompt.status_code == 400
    assert "프롬프트" in unknown_prompt.json()["detail"]

    unknown_model = await admin_client.post(
        "/api/agents", json={"name": "b", "answer_model": "gpt-9-ultra"}
    )
    assert unknown_model.status_code == 400
    assert "답변 모델" in unknown_model.json()["detail"]

    unknown_collection = await admin_client.post(
        "/api/agents", json={"name": "c", "collection_ids": [str(uuid.uuid4())]}
    )
    assert unknown_collection.status_code == 400

    unknown_tool = await admin_client.post(
        "/api/agents", json={"name": "d", "tool_ids": [str(uuid.uuid4())]}
    )
    assert unknown_tool.status_code == 400

    await create_agent(admin_client, name="중복")
    duplicate = await admin_client.post("/api/agents", json={"name": "중복"})
    assert duplicate.status_code == 409


# ---------------------------------------------------------------------------
# The collection boundary
# ---------------------------------------------------------------------------


async def test_retrieve_restricted_to_one_collection_cannot_reach_another(db, fake_llm, corpus):
    """THE test of this slice, at the level that cannot be bypassed.

    The question is answerable ONLY from 농약 - the stub embeds it as 농약's own
    vector and the words appear nowhere else - and the agent may only see 비료.
    It comes back with 비료's chunk or with nothing, never with 농약's.

    Against `retrieve` directly rather than only through the API, because
    `retrieve` is where the narrowing lives: a caller that forgets to pass a
    scope still gets one.
    """
    restricted = ResolvedAgent(
        id=uuid.uuid4(), name="비료 전용", collection_ids=frozenset({corpus["collection_a"]})
    )
    settings = settings_with(retrieval_top_n=5)

    unrestricted_hits = await retrieve(
        db,
        PgVectorStore(db),
        fake_llm,
        NoneReranker(),
        "다이아지논 살포 기준",
        settings=settings,
        agent=DEFAULT_AGENT,
    )
    # The premise: without the agent the answer IS reachable, so a restricted run
    # that finds nothing is the restriction and not an empty corpus.
    assert any(B_TEXT in hit.content for hit in unrestricted_hits)

    hits = await retrieve(
        db,
        PgVectorStore(db),
        fake_llm,
        NoneReranker(),
        "다이아지논 살포 기준",
        settings=settings,
        agent=restricted,
    )
    assert all(B_TEXT not in hit.content for hit in hits)


async def test_an_answer_from_a_restricted_agent_cites_no_evidence_from_outside(
    admin_client, corpus, db
):
    """The same property end to end, through the SSE path an actual question
    takes, asserted on the TRACE - which records every retrieved item including
    the ones the token budget cut, so a leak cannot hide in the gap between
    "retrieved" and "cited"."""
    agent = await create_agent(
        admin_client, name="비료 담당", collection_ids=[str(corpus["collection_a"])]
    )
    response = await admin_client.post(
        "/api/chat", json={"message": "다이아지논 살포 기준", "agent_id": agent["id"]}
    )
    assert response.status_code == 200
    message_id = parse_sse(response.text)[-1]["message_id"]

    trace = (await admin_client.get(f"/api/messages/{message_id}/trace")).json()
    assert trace["evidence"], "nothing was retrieved at all; the assertion below would be vacuous"
    assert {item["filename"] for item in trace["evidence"]} == {"비료.pdf"}


async def test_a_question_scoped_outside_the_agent_is_refused_rather_than_emptied(
    admin_client, corpus, db
):
    """Refused, not silently narrowed to nothing. An answer built from no evidence
    reads as "the corpus does not say", which is a different and false claim.

    And refused BEFORE the conversation is written, which is the second half of
    the assertion and the half a status code alone would not catch: every check
    in this router runs before the row, so a refusal never leaves a titled empty
    conversation in the sidebar for the user to clean up."""
    agent = await create_agent(
        admin_client, name="비료만", collection_ids=[str(corpus["collection_a"])]
    )
    refused = await admin_client.post(
        "/api/chat",
        json={
            "message": "질문",
            "agent_id": agent["id"],
            "collection_ids": [str(corpus["collection_b"])],
        },
    )
    assert refused.status_code == 400
    assert "분류" in refused.json()["detail"]
    assert (await db.scalar(text("SELECT count(*) FROM conversations"))) == 0


def test_scope_collections_never_widens_and_never_silently_empties():
    """The three cases, stated once so the reasoning is not spread over the API
    tests: unrestricted passes through, unscoped narrows to the agent, and a
    disjoint request raises instead of returning []."""
    a, b = uuid.uuid4(), uuid.uuid4()
    unrestricted = ResolvedAgent()
    assert unrestricted.scope_collections(None) is None
    assert unrestricted.scope_collections([b]) == [b]

    restricted = ResolvedAgent(collection_ids=frozenset({a}))
    assert restricted.scope_collections(None) == [a]
    assert restricted.scope_collections([a, b]) == [a]
    with pytest.raises(AgentScopeError):
        restricted.scope_collections([b])


# ---------------------------------------------------------------------------
# The tool boundary
# ---------------------------------------------------------------------------


async def test_a_plan_naming_a_tool_outside_the_agents_list_is_refused_whole(db, corpus):
    """Refused WHOLE, not filtered down to the steps that were allowed.

    The plan below has a legitimate search step beside the forbidden tool step. A
    filtering executor would run the search and quietly drop the tool call, and
    the user would get a plausible answer with no sign that the plan they paid a
    planner call for was rewritten. A model that named one thing it may not touch
    has told you what its other choices are worth, so the whole plan goes and the
    direct RAG path answers instead.
    """
    limited = ResolvedAgent(id=uuid.uuid4(), name="읽기 전용", tool_ids=frozenset({corpus["read_tool"]}))

    everything = await load_available(db)
    assert {t.tool_name for t in everything.tools} == {"lookup", "record"}

    available = await load_available(db, None, limited)
    # Unnameable, not merely un-runnable: it is absent from the catalogue the
    # planner is shown, so there is no second rule to keep in step.
    assert [t.tool_name for t in available.tools] == ["lookup"]

    plan = {
        "steps": [
            {"id": "s1", "kind": "rag", "query": "비료"},
            {"id": "s2", "kind": "tool", "tool": "현장/record"},
        ]
    }
    with pytest.raises(PlanError) as refused:
        validate_plan(plan, available, settings=settings_with())
    assert "현장/record" in str(refused.value)


async def test_a_hand_picked_tool_outside_the_agents_list_is_refused_too(admin_client, corpus):
    """The manual half of the same boundary. Fencing the planner while leaving the
    composer's own tool picker open would fence the machine and not the human,
    which is the wrong way round."""
    agent = await create_agent(
        admin_client, name="읽기만", tool_ids=[str(corpus["read_tool"])]
    )
    refused = await admin_client.post(
        "/api/chat",
        json={
            "message": "질문",
            "agent_id": agent["id"],
            "tool_calls": [{"tool_id": str(corpus["write_tool"]), "arguments": {}}],
        },
    )
    assert refused.status_code == 403
    assert "도구" in refused.json()["detail"]


async def test_a_plan_step_naming_no_collections_cannot_search_outside_the_agent(db, corpus):
    """The hole that was actually there: `collections: []` meant "everything".

    The planner is entitled to omit the field - "search the whole catalogue" is a
    normal plan - and the executor used to turn the resulting empty tuple back
    into `collection_ids=None`, which is every collection in the DATABASE rather
    than every collection in the catalogue. So an agent's restriction was one
    omitted JSON key away from gone. validate_plan now writes the catalogue out.
    """
    restricted = ResolvedAgent(
        id=uuid.uuid4(), name="비료 전용", collection_ids=frozenset({corpus["collection_a"]})
    )
    available = await load_available(db, None, restricted)
    plan = validate_plan(
        {"steps": [{"id": "s1", "kind": "rag", "query": "다이아지논"}]},
        available,
        settings=settings_with(),
    )
    assert plan.steps[0].collection_ids == (corpus["collection_a"],)
    assert corpus["collection_b"] not in plan.steps[0].collection_ids


# ---------------------------------------------------------------------------
# What answered, and what happens when it is gone
# ---------------------------------------------------------------------------


async def test_the_agent_that_answered_survives_a_reload_and_appears_in_the_trace(
    admin_client, corpus
):
    agent = await create_agent(admin_client, name="기록 대상")
    response = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"]}
    )
    done = parse_sse(response.text)[-1]
    assert done["agent_name"] == "기록 대상"

    conversation_id = done["conversation_id"]
    reloaded = (await admin_client.get(f"/api/conversations/{conversation_id}/messages")).json()
    assistant = [m for m in reloaded if m["role"] == "assistant"][0]
    assert assistant["agent_name"] == "기록 대상"

    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert trace["agent_name"] == "기록 대상"


async def test_deleting_an_agent_does_not_orphan_the_messages_that_name_it(
    admin_client, corpus, db
):
    """`messages.agent_name` is a string, not a foreign key, exactly so this can
    be true. An admin retiring an agent must not be able to delete - or cascade
    away - answers other people are still reading, and "which agent said this"
    has to stay answerable afterwards."""
    agent = await create_agent(admin_client, name="폐기 예정")
    done = parse_sse(
        (await admin_client.post("/api/chat", json={"message": "질문", "agent_id": agent["id"]})).text
    )[-1]

    assert (await admin_client.delete(f"/api/agents/{agent['id']}")).status_code == 204

    reloaded = (
        await admin_client.get(f"/api/conversations/{done['conversation_id']}/messages")
    ).json()
    assistant = [m for m in reloaded if m["role"] == "assistant"][0]
    assert assistant["agent_name"] == "폐기 예정"
    assert assistant["content"]
    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert trace["agent_name"] == "폐기 예정"
    assert (await db.scalar(text("SELECT count(*) FROM agents"))) == 0


async def test_deleting_an_agent_removes_its_join_rows_but_not_the_collection(
    admin_client, corpus, db
):
    agent = await create_agent(
        admin_client,
        name="연결 확인",
        collection_ids=[str(corpus["collection_a"])],
        tool_ids=[str(corpus["read_tool"])],
    )
    await admin_client.delete(f"/api/agents/{agent['id']}")
    assert (await db.scalar(text("SELECT count(*) FROM agent_collections"))) == 0
    assert (await db.scalar(text("SELECT count(*) FROM agent_tools"))) == 0
    # The CASCADE runs from `agents` towards the join rows and stops there. The
    # 비료 collection and the lookup tool are shared resources that other agents,
    # other answers and the documents screen all still point at.
    remaining = set((await db.scalars(select(Collection.name))).all())
    assert {"비료", "농약"} <= remaining
    assert (await db.scalar(text("SELECT count(*) FROM mcp_tools"))) == 2


# ---------------------------------------------------------------------------
# The default agent: an empty table changes nothing
# ---------------------------------------------------------------------------


async def test_an_empty_agents_table_behaves_exactly_as_before(admin_client, corpus, db):
    """The deployment claim, checked rather than asserted.

    TRUNCATED IN THE BODY. The database is session-scoped and `corpus` seeds
    rows, so a version of this test that trusted the fixture ordering would pass
    with every guard in this module removed - the trap tests/conftest.py already
    documents for app_settings.
    """
    await db.execute(text("TRUNCATE TABLE agents CASCADE"))
    # COMMITTED, not merely executed. TRUNCATE takes an ACCESS EXCLUSIVE lock, and
    # this session is not the one the API requests below use: leaving the
    # transaction open makes the very first `select(Agent)` inside the app block
    # on it until the test times out.
    await db.commit()
    assert (await db.scalar(text("SELECT count(*) FROM agents"))) == 0
    assert (await admin_client.get("/api/agents/selectable")).json() == []

    response = await admin_client.post("/api/chat", json={"message": "다이아지논 살포 기준"})
    assert response.status_code == 200
    done = parse_sse(response.text)[-1]
    assert done["agent_name"] is None

    row = await db.scalar(select(Message).where(Message.id == uuid.UUID(done["message_id"])))
    assert row.agent_name is None
    assert row.prompt_name == "answer_agent"
    assert row.model == "gpt-4o"
    # Unrestricted: the whole corpus is still reachable, both collections included.
    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert {item["filename"] for item in trace["evidence"]} == {"비료.pdf", "농약.pdf"}


async def test_the_default_agent_narrows_nothing(db, corpus):
    """DEFAULT_AGENT is not a special case handled somewhere - it is a
    ResolvedAgent whose two sets are empty, and empty means unrestricted."""
    available = await load_available(db, None, DEFAULT_AGENT)
    # A superset: registration creates the 일반 collection, and "unrestricted"
    # means every collection there is, whoever made it.
    assert {"비료", "농약"} <= {c.name for c in available.collections}
    assert {t.tool_name for t in available.tools} == {"lookup", "record"}


# ---------------------------------------------------------------------------
# The configuration an agent actually carries
# ---------------------------------------------------------------------------


async def test_the_agents_model_is_the_default_and_an_explicit_model_still_wins(
    admin_client, corpus, fake_llm, app
):
    """The agent supplies the default, never the ceiling. The composer's own
    picker keeps working when an agent is selected, and the allowlist is still
    the only thing deciding what reaches the provider.

    The conftest pins `answer_models` to [] so a deployment cannot change what the
    suite asserts; a second selectable model is added here because that is exactly
    what this test is about."""
    app.state.settings = app.state.settings.model_copy(update={"answer_models": ["gpt-4o-mini"]})
    agent = await create_agent(admin_client, name="모델 지정", answer_model="gpt-4o-mini")

    await admin_client.post("/api/chat", json={"message": "질문", "agent_id": agent["id"]})
    assert fake_llm.chat.await_args.kwargs["model"] == "gpt-4o-mini"

    await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"], "model": "gpt-4o"}
    )
    assert fake_llm.chat.await_args.kwargs["model"] == "gpt-4o"


async def test_an_agents_model_is_still_checked_against_the_allowlist(admin_client, corpus, app, db):
    """An operator can drop a model from ANSWER_MODELS long after an admin picked
    it. The row must not be able to smuggle it past the gate, so the check on the
    admin form is not the only one."""
    agent = await create_agent(admin_client, name="사라진 모델", answer_model="gpt-4o")
    await db.execute(
        text("UPDATE agents SET answer_model = 'gpt-4o-mini' WHERE id = :id"), {"id": agent["id"]}
    )
    await db.commit()

    app.state.settings = app.state.settings.model_copy(
        update={"answer_model": "gpt-4o", "answer_models": []}
    )
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"]}
    )
    assert refused.status_code == 400
    assert "gpt-4o-mini" in refused.json()["detail"]


async def test_an_agent_can_carry_its_own_prompt_from_the_store(admin_client, corpus, fake_llm, db):
    """"prompt from the prompt store", which is why POST /api/prompts exists: an
    agent that could only ever name the deployment's own system prompt would be
    missing the field the feature is about."""
    created = await admin_client.post(
        "/api/prompts", json={"name": "field_agent", "text": "너는 현장 담당자다. 짧게 답한다."}
    )
    assert created.status_code == 201
    agent = await create_agent(admin_client, name="현장", prompt_name="field_agent")

    response = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"]}
    )
    done = parse_sse(response.text)[-1]
    system_message = fake_llm.chat.await_args.args[0][0]
    assert system_message.role == "system"
    assert "현장 담당자" in system_message.content

    row = await db.scalar(select(Message).where(Message.id == uuid.UUID(done["message_id"])))
    assert row.prompt_name == "field_agent"
    assert row.prompt_version == "1"


async def test_creating_a_prompt_is_admin_only_and_cannot_overwrite_one(
    admin_client, member_client, corpus
):
    refused = await member_client.post("/api/prompts", json={"name": "sneaky", "text": "x"})
    assert refused.status_code == 403

    duplicate = await admin_client.post("/api/prompts", json={"name": "answer_agent", "text": "x"})
    assert duplicate.status_code == 409
    assert "이미" in duplicate.json()["detail"]

    blank = await admin_client.post("/api/prompts", json={"name": "blank_agent", "text": "   "})
    assert blank.status_code == 400


async def test_an_agent_that_carries_the_orchestrator_turns_it_on(admin_client, corpus, fake_llm):
    """The agent's configuration, not a per-question default it can be talked out
    of. The composer shows the toggle forced on and says why."""
    agent = await create_agent(admin_client, name="계획형", orchestrator=True)
    fake_llm.chat = AsyncMock(
        side_effect=[
            ChatResult(content='{"steps": []}', usage={}, model="gpt-4o"),
            ChatResult(content="답변입니다.", usage={"total_tokens": 5}, model="gpt-4o"),
        ]
    )
    response = await admin_client.post(
        "/api/chat", json={"message": "질문", "agent_id": agent["id"], "orchestrator": False}
    )

    assert response.status_code == 200
    assert "planning" in [e.get("status") for e in parse_sse(response.text)]


async def test_updating_an_agent_replaces_its_lists_and_can_clear_them(admin_client, corpus):
    """An empty list is a real state - it is what "unrestricted" is - so the two
    lists are replaced wholesale when present rather than merged."""
    agent = await create_agent(
        admin_client,
        name="편집 대상",
        collection_ids=[str(corpus["collection_a"])],
        tool_ids=[str(corpus["read_tool"])],
    )
    assert [c["name"] for c in agent["collections"]] == ["비료"]

    swapped = await admin_client.patch(
        f"/api/agents/{agent['id']}", json={"collection_ids": [str(corpus["collection_b"])]}
    )
    assert [c["name"] for c in swapped.json()["collections"]] == ["농약"]
    # Omitted, so untouched.
    assert [t["name"] for t in swapped.json()["tools"]] == ["lookup"]

    cleared = await admin_client.patch(f"/api/agents/{agent['id']}", json={"tool_ids": []})
    assert cleared.json()["tools"] == []


async def test_patching_one_field_leaves_every_other_field_alone(admin_client, corpus, app):
    """FOUND BY DRIVING IT. The row's 중지 button sends `{"enabled": false}` and
    nothing else, and an `is not None` read of a NULLABLE field cannot tell that
    from `{"answer_model": null}` - so pausing an agent silently cleared the
    model an admin had chosen for it. `model_fields_set` is what tells omitted
    from null, and an explicit null still clears, because "back to the
    deployment default" has to be reachable."""
    app.state.settings = app.state.settings.model_copy(update={"answer_models": ["gpt-4o-mini"]})
    agent = await create_agent(
        admin_client,
        name="부분 수정",
        description="설명",
        answer_model="gpt-4o-mini",
        collection_ids=[str(corpus["collection_a"])],
    )

    paused = (await admin_client.patch(f"/api/agents/{agent['id']}", json={"enabled": False})).json()
    assert paused["enabled"] is False
    assert paused["answer_model"] == "gpt-4o-mini"
    assert paused["description"] == "설명"
    assert [c["name"] for c in paused["collections"]] == ["비료"]

    # An EXPLICIT null still clears, or an admin could never get back to the
    # deployment default.
    cleared = (
        await admin_client.patch(f"/api/agents/{agent['id']}", json={"answer_model": None})
    ).json()
    assert cleared["answer_model"] is None


async def test_an_agent_row_names_its_creator_and_its_tools_by_server(admin_client, corpus, db):
    agent = await create_agent(admin_client, name="표시", tool_ids=[str(corpus["read_tool"])])
    assert agent["created_by_email"] == "agent-admin@example.com"
    assert agent["tools"][0]["server_name"] == "현장"
    assert agent["tools"][0]["risk_level"] == "read"
    stored = await db.scalar(select(Agent).where(Agent.id == uuid.UUID(agent["id"])))
    assert stored.prompt_name == "answer_agent"
    assert stored.answer_model is None
