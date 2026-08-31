"""Slice 6 - workflow management.

A workflow is a PROCEDURE A PERSON AUTHORED, saved: a name, a prompt from the
prompt store, a set of collections it may search, a set of MCP tools it may call,
an answer model, and - new in this slice - a versioned GRAPH. It is deliberately
not code, and it is deliberately not "an agent": 워크플로우 is a graph a person
drew, 슈퍼 에이전트 is the mode where the model draws one per question, and the word
에이전트 is retired from the table, the columns, the API paths and this file.

The property this whole file is arranged around:

    THE TWO LISTS ARE PERMISSION BOUNDARIES, NOT HINTS. A graph naming a tool the
    workflow does not carry is refused WHOLE - not filtered - and AT SAVE, so the
    admin who drew it reads a Korean sentence rather than somebody else getting a
    quietly rewritten answer three days later. A retrieval restricted to the
    workflow's collections cannot reach outside them even when the only answer is
    out there. Enforced in `load_available`, in `validate_graph` and in
    `retrieve`, which is to say in the functions that decide what a question may
    reach - not in the UI and not in a prompt.

    AND NESTING DOES NOT WIDEN IT. `AvailableResources.narrow` intersects rather
    than replaces, so a workflow restricted to A cannot reach B by calling a
    workflow that carries B.

And the property that makes it deployable at all: an EMPTY `workflows` table
changes nothing. Every "when there are no workflows" test truncates the table in
its own body, because the database is session-scoped and a leftover row from
another module would let such a test pass with its guard removed.

`orchestrator` IS GONE, and there is a test for its absence rather than a silence:
that column is what let "a fixed procedure" switch on "autonomous planning", and
a field the API quietly accepted again would put it back.

NO TEST HERE MAKES A NETWORK CALL. The LLM is an AsyncMock; there is no MCP
server, only rows describing one.
"""

import importlib.util
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.chat.prompt import ANSWER_SYSTEM_PROMPT
from app.chat.service import retrieve
from app.core.config import Settings
from app.llm.base import ChatResult
from app.models.chunk import EMBEDDING_DIM, Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.mcp import McpServer, McpTool
from app.models.message import Message
from app.models.prompt import Prompt
from app.models.user import User
from app.models.workflow import Workflow
from app.retrieval.vector_store import PgVectorStore
from app.workflow.catalogue import (
    DEFAULT_WORKFLOW,
    AvailableCollection,
    AvailableResources,
    AvailableWorkflow,
    ResolvedWorkflow,
    WorkflowScopeError,
    load_available,
)
from app.workflow.graph import GraphError, validate_graph

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
    # collection the workflow is NOT allowed to reach. That is the point.
    provider.embed = AsyncMock(return_value=[vec(0.0, 1.0)])
    provider.chat = AsyncMock(
        return_value=ChatResult(content="답변입니다 [1].", usage={"total_tokens": 11}, model="gpt-4o")
    )
    return provider


def graph_of(nodes: list[dict], edges: list[tuple[str, str]]) -> dict:
    """`{"nodes": [...], "edges": [...]}` in the shape the canvas POSTs, so a test
    can say what it is actually about instead of retyping the envelope."""
    return {"nodes": nodes, "edges": [{"from": source, "to": target} for source, target in edges]}


def rag_node(node_id: str = "search", *, query: str = "{{input.text}}", **extra) -> dict:
    return {"id": node_id, "kind": "tool", "tool": "rag", "arguments": {"query": query}, **extra}


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
    await client.post(
        "/api/auth/register", json={"email": "workflow-admin@example.com", "password": "pw123456"}
    )
    await client.post(
        "/api/auth/login", json={"email": "workflow-admin@example.com", "password": "pw123456"}
    )
    return client


@pytest_asyncio.fixture
async def member_client(admin_client, app):
    """A second, non-admin account on its own cookie jar."""
    await admin_client.post(
        "/api/auth/register", json={"email": "workflow-member@example.com", "password": "pw123456"}
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post(
            "/api/auth/login", json={"email": "workflow-member@example.com", "password": "pw123456"}
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
    dependency makes the order a fact instead of a signature convention.

    `workflow_versions` is truncated with the rest: it cascades from `workflows`,
    and a leftover version row is a graph that would still be listed in the `@`
    menu by a test that believes the table is empty."""
    await db.execute(
        text("TRUNCATE TABLE workflows, workflow_collections, workflow_tools, workflow_versions CASCADE")
    )
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


async def create_workflow(client, **overrides) -> dict:
    body = {"name": "테스트 워크플로우"} | overrides
    response = await client.post("/api/workflows", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Admin only. Picking one is not.
# ---------------------------------------------------------------------------


async def test_a_non_admin_cannot_create_or_edit_a_workflow(admin_client, member_client, corpus):
    """A workflow decides which prompt answers, which corpus and tools a question
    may reach, and now which procedure runs. That is the same authority Slice 1
    put behind require_admin for documents, and for the same reason: it changes
    every other user's answers."""
    created = await create_workflow(admin_client, name="관리자만")

    refused_create = await member_client.post("/api/workflows", json={"name": "몰래"})
    assert refused_create.status_code == 403
    assert "관리자" in refused_create.json()["detail"]

    refused_edit = await member_client.patch(f"/api/workflows/{created['id']}", json={"name": "바꿔치기"})
    assert refused_edit.status_code == 403

    refused_delete = await member_client.delete(f"/api/workflows/{created['id']}")
    assert refused_delete.status_code == 403

    refused_list = await member_client.get("/api/workflows")
    assert refused_list.status_code == 403

    refused_versions = await member_client.get(f"/api/workflows/{created['id']}/versions")
    assert refused_versions.status_code == 403

    refused_save = await member_client.post(
        f"/api/workflows/{created['id']}/versions", json={"graph": {"nodes": [], "edges": []}}
    )
    assert refused_save.status_code == 403

    # And nothing actually changed.
    unchanged = (await admin_client.get("/api/workflows")).json()
    assert [w["name"] for w in unchanged] == ["관리자만"]


async def test_any_authenticated_user_may_list_and_pick_a_selectable_workflow(
    admin_client, member_client, corpus
):
    """The same argument GET /api/models makes: it returns exactly what
    POST /api/chat accepts, so it discloses nothing a user could not learn by
    picking one and being answered. It carries no collection or tool list -
    enumerating a boundary is how you tell somebody what to try next - and
    `node_count` says "this is a procedure" without naming what it reaches."""
    await create_workflow(admin_client, name="현장 도우미", description="현장 질문용")

    listed = await member_client.get("/api/workflows/selectable")
    assert listed.status_code == 200
    assert [w["name"] for w in listed.json()] == ["현장 도우미"]
    assert set(listed.json()[0]) == {"id", "name", "description", "answer_model", "node_count"}
    # The seeded starter graph, so a brand-new workflow is immediately callable
    # rather than a row that appears in the menu and cannot run.
    assert listed.json()[0]["node_count"] == 3

    answered = await member_client.post(
        "/api/chat", json={"message": "질문", "workflow_id": listed.json()[0]["id"]}
    )
    assert answered.status_code == 200


async def test_a_disabled_workflow_can_neither_be_listed_nor_selected(admin_client, corpus):
    """Unlistable AND unnameable, the rule a disabled MCP tool already follows.
    The 409 is for the race - an admin turning it off while somebody is typing -
    not for the UI."""
    created = await create_workflow(admin_client, name="중지된 워크플로우")
    await admin_client.patch(f"/api/workflows/{created['id']}", json={"enabled": False})

    assert (await admin_client.get("/api/workflows/selectable")).json() == []
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "workflow_id": created["id"]}
    )
    assert refused.status_code == 409
    assert "중지" in refused.json()["detail"]


async def test_an_unknown_workflow_id_is_a_404_before_anything_is_written(admin_client, corpus, db):
    """Resolved before the conversation exists, like the model and the attachment
    ids: a refusal after the StreamingResponse has begun would be an error frame
    inside a 200, and a titled empty conversation would be left in the sidebar."""
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "workflow_id": str(uuid.uuid4())}
    )
    assert refused.status_code == 404
    assert "워크플로우" in refused.json()["detail"]
    assert (await db.scalar(text("SELECT count(*) FROM conversations"))) == 0


async def test_the_admin_form_refuses_what_the_chat_would_refuse(admin_client, corpus):
    """Every one of these is a Korean 400 on the form the admin is filling in
    rather than a refusal on somebody else's question three days later."""
    unknown_prompt = await admin_client.post(
        "/api/workflows", json={"name": "a", "prompt_name": "없는_프롬프트"}
    )
    assert unknown_prompt.status_code == 400
    assert "프롬프트" in unknown_prompt.json()["detail"]

    unknown_model = await admin_client.post(
        "/api/workflows", json={"name": "b", "answer_model": "gpt-9-ultra"}
    )
    assert unknown_model.status_code == 400
    assert "답변 모델" in unknown_model.json()["detail"]

    unknown_collection = await admin_client.post(
        "/api/workflows", json={"name": "c", "collection_ids": [str(uuid.uuid4())]}
    )
    assert unknown_collection.status_code == 400

    unknown_tool = await admin_client.post(
        "/api/workflows", json={"name": "d", "tool_ids": [str(uuid.uuid4())]}
    )
    assert unknown_tool.status_code == 400

    await create_workflow(admin_client, name="중복")
    duplicate = await admin_client.post("/api/workflows", json={"name": "중복"})
    assert duplicate.status_code == 409


async def test_the_orchestrator_field_is_gone_from_the_row_and_from_the_api(admin_client, corpus, db):
    """REPLACES the old `an agent that carries the orchestrator turns it on`.

    That column is what mixed the two layers - a saved procedure switching on
    autonomous planning - and 0010 dropped it. A test that merely stopped
    exercising it would leave nothing to notice somebody adding it back, so this
    asserts the absence in all three places it could return: the ORM class, the
    table, and the create form, which ignores the unknown key rather than
    silently storing it.
    """
    assert not hasattr(Workflow, "orchestrator")

    created = await admin_client.post("/api/workflows", json={"name": "구식 폼", "orchestrator": True})
    assert created.status_code == 201, created.text
    assert "orchestrator" not in created.json()

    columns = set(
        (
            await db.scalars(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'workflows'")
            )
        ).all()
    )
    assert "orchestrator" not in columns


# ---------------------------------------------------------------------------
# The collection boundary
# ---------------------------------------------------------------------------


async def test_retrieve_restricted_to_one_collection_cannot_reach_another(db, fake_llm, corpus):
    """THE test of this slice, at the level that cannot be bypassed.

    The question is answerable ONLY from 농약 - the stub embeds it as 농약's own
    vector and the words appear nowhere else - and the workflow may only see 비료.
    It comes back with 비료's chunk or with nothing, never with 농약's.

    Against `retrieve` directly rather than only through the API, because
    `retrieve` is where the narrowing lives: a caller that forgets to pass a
    scope still gets one.
    """
    restricted = ResolvedWorkflow(
        id=uuid.uuid4(), name="비료 전용", collection_ids=frozenset({corpus["collection_a"]})
    )
    settings = settings_with(retrieval_top_n=5)

    unrestricted_hits = await retrieve(
        db,
        PgVectorStore(db),
        fake_llm,
        None,
        "다이아지논 살포 기준",
        settings=settings,
        workflow=DEFAULT_WORKFLOW,
    )
    # The premise: without the workflow the answer IS reachable, so a restricted
    # run that finds nothing is the restriction and not an empty corpus.
    assert any(B_TEXT in hit.content for hit in unrestricted_hits)

    hits = await retrieve(
        db,
        PgVectorStore(db),
        fake_llm,
        None,
        "다이아지논 살포 기준",
        settings=settings,
        workflow=restricted,
    )
    assert all(B_TEXT not in hit.content for hit in hits)


async def test_a_restricted_workflows_graph_cites_no_evidence_from_outside(admin_client, corpus, db):
    """THE MIGRATION'S BEHAVIOUR-PRESERVING CLAIM, checked end to end.

    0010 turned every agent into `input -> rag -> answer`, and `POST /api/workflows`
    seeds the identical graph. So this is the assertion the old
    `an answer from a restricted agent cites no evidence from outside` made -
    same question, same corpus, same expected evidence - now going through the
    GRAPH EXECUTOR rather than the direct RAG path. If the conversion had changed
    behaviour, this is where it would show.

    Asserted on the TRACE, which records every retrieved item including the ones
    the token budget cut, so a leak cannot hide in the gap between "retrieved"
    and "cited" - and on `fell_back_to_direct_rag`, because a graph that produced
    nothing falls back to `retrieve`, and this test would then be asserting the
    fallback's boundary rather than the executor's.
    """
    workflow = await create_workflow(
        admin_client, name="비료 담당", collection_ids=[str(corpus["collection_a"])]
    )
    response = await admin_client.post(
        "/api/chat", json={"message": "다이아지논 살포 기준", "workflow_id": workflow["id"]}
    )
    assert response.status_code == 200
    message_id = parse_sse(response.text)[-1]["message_id"]

    trace = (await admin_client.get(f"/api/messages/{message_id}/trace")).json()
    assert trace["evidence"], "nothing was retrieved at all; the assertion below would be vacuous"
    assert {item["filename"] for item in trace["evidence"]} == {"비료.pdf"}
    assert trace["plan"] is not None, "the seeded graph did not run; this asserts the wrong path"
    assert trace["plan"]["author"] == "사람"
    assert trace["plan"]["fell_back_to_direct_rag"] is False


def test_migration_0010_writes_a_behaviour_preserving_graph():
    """Read out of the migration BY FILE PATH, the same technique
    tests/test_workflow_engine.py uses on 0007, and for the same reason: a
    migration is a historical record, so what it wrote must be assertable without
    importing anything the application could later change underneath it.

    The claim being checked is the one section 6 of the design rests on - each
    converted agent becomes `input -> tool:rag -> answer` with
    `arguments.query = {{input.text}}`, which is exactly the search `retrieve()`
    ran for that agent. Coordinates on every node, because the canvas has to open
    on a readable row rather than three boxes piled at the origin.

    And the empty case is asserted beside the restricted one: an unrestricted
    agent had an empty `agent_collections`, and `collections: []` has to keep
    meaning the whole catalogue rather than nothing.
    """
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0010_workflows.py"
    spec = importlib.util.spec_from_file_location("migration_0010", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    graph = module._graph(["비료"])
    assert {node["id"]: node["kind"] for node in graph["nodes"]} == {
        "input": "input",
        "search": "tool",
        "answer": "answer",
    }
    assert [(edge["from"], edge["to"]) for edge in graph["edges"]] == [
        ("input", "search"),
        ("search", "answer"),
    ]
    search = next(node for node in graph["nodes"] if node["id"] == "search")
    assert search["tool"] == "rag"
    assert search["collections"] == ["비료"]
    assert search["arguments"]["query"] == "{{input.text}}"
    assert all("x" in node and "y" in node for node in graph["nodes"])
    # Laid out left to right rather than stacked, which is the difference between
    # a converted workflow that opens as a picture and one that opens as a pile.
    assert [node["x"] for node in graph["nodes"]] == [0, 260, 520]

    assert module._graph([])["nodes"][1]["collections"] == []


async def test_a_question_scoped_outside_the_workflow_is_refused_rather_than_emptied(
    admin_client, corpus, db
):
    """Refused, not silently narrowed to nothing. An answer built from no evidence
    reads as "the corpus does not say", which is a different and false claim.

    And refused BEFORE the conversation is written, which is the second half of
    the assertion and the half a status code alone would not catch: every check
    in this router runs before the row, so a refusal never leaves a titled empty
    conversation in the sidebar for the user to clean up."""
    workflow = await create_workflow(
        admin_client, name="비료만", collection_ids=[str(corpus["collection_a"])]
    )
    refused = await admin_client.post(
        "/api/chat",
        json={
            "message": "질문",
            "workflow_id": workflow["id"],
            "collection_ids": [str(corpus["collection_b"])],
        },
    )
    assert refused.status_code == 400
    assert "분류" in refused.json()["detail"]
    assert (await db.scalar(text("SELECT count(*) FROM conversations"))) == 0


def test_scope_collections_never_widens_and_never_silently_empties():
    """The three cases, stated once so the reasoning is not spread over the API
    tests: unrestricted passes through, unscoped narrows to the workflow, and a
    disjoint request raises instead of returning []."""
    a, b = uuid.uuid4(), uuid.uuid4()
    unrestricted = ResolvedWorkflow()
    assert unrestricted.scope_collections(None) is None
    assert unrestricted.scope_collections([b]) == [b]

    restricted = ResolvedWorkflow(collection_ids=frozenset({a}))
    assert restricted.scope_collections(None) == [a]
    assert restricted.scope_collections([a, b]) == [a]
    with pytest.raises(WorkflowScopeError):
        restricted.scope_collections([b])


def test_narrowing_for_a_nested_workflow_never_widens_the_boundary():
    """NESTING IS THE ONE WAY A BOUNDARY COULD HAVE GROWN, and `narrow`
    intersects rather than replaces so it cannot.

    A caller that may only reach A calls a workflow that carries B. Neither B nor
    "everything" is a legal outcome; the honest answer is nothing, and the
    executor's RAG node reads an empty tuple as an IN () predicate rather than as
    "no restriction". A callee with no lists of its own inherits the caller's,
    which is what makes EMPTY = UNRESTRICTED consistent on both sides.
    """
    a, b = uuid.uuid4(), uuid.uuid4()
    caller = AvailableResources(
        collections=(AvailableCollection(id=a, name="비료"),),
    )

    def callee(name: str, **lists) -> AvailableWorkflow:
        return AvailableWorkflow(id=uuid.uuid4(), name=name, description=None, version=1, graph={}, **lists)

    carries_b = caller.narrow(callee("농약 전용", collection_ids=frozenset({b})))
    assert carries_b.collections == (), "a callee's list must intersect, never replace"
    assert b not in {c.id for c in carries_b.collections}

    carries_both = caller.narrow(callee("둘 다", collection_ids=frozenset({a, b})))
    assert {c.id for c in carries_both.collections} == {a}

    unrestricted = caller.narrow(callee("제한 없음"))
    assert {c.id for c in unrestricted.collections} == {a}


# ---------------------------------------------------------------------------
# The tool boundary, enforced AT SAVE
# ---------------------------------------------------------------------------


async def test_a_graph_naming_a_tool_outside_the_workflows_list_is_refused_whole(db, corpus):
    """Refused WHOLE, not filtered down to the nodes that were allowed.

    The graph below has a legitimate search node beside the forbidden tool node.
    A filtering validator would keep the search and quietly drop the tool call,
    and the picture on the canvas would stop matching what runs - which for an
    authored procedure is the one failure mode worse than a refusal.

    At the level under the API, because that is where it cannot be bypassed: the
    forbidden tool never enters the catalogue at all, so `validate_graph` cannot
    resolve the name and there is no second rule to keep in step.
    """
    limited = ResolvedWorkflow(
        id=uuid.uuid4(), name="읽기 전용", tool_ids=frozenset({corpus["read_tool"]})
    )

    everything = await load_available(db)
    assert {t.tool_name for t in everything.tools} == {"lookup", "record"}

    available = await load_available(db, None, limited)
    # Unnameable, not merely un-runnable: it is absent from the catalogue the
    # graph is validated against.
    assert [t.tool_name for t in available.tools] == ["lookup"]

    graph = graph_of(
        [
            {"id": "input", "kind": "input"},
            rag_node(),
            {"id": "call", "kind": "tool", "tool": "mcp:현장/record", "arguments": {}},
            {"id": "answer", "kind": "answer"},
        ],
        [("input", "search"), ("search", "call"), ("call", "answer")],
    )
    with pytest.raises(GraphError) as refused:
        validate_graph(graph, available, settings=settings_with())
    assert "현장/record" in str(refused.value)


async def test_saving_a_graph_that_names_a_forbidden_tool_is_a_400_and_writes_nothing(
    admin_client, corpus, db
):
    """The fourth acceptance criterion, over HTTP: refused AT SAVE.

    The admin who drew it reads a Korean sentence while the canvas is still open,
    instead of somebody else's question being answered three days later from a
    graph that was quietly rewritten. And NOTHING IS WRITTEN - the version count
    is unchanged - because a refused save that still bumped the version would
    make the 되돌리기 list describe a graph that never ran.
    """
    workflow = await create_workflow(
        admin_client, name="읽기만 저장", tool_ids=[str(corpus["read_tool"])]
    )
    before = await db.scalar(text("SELECT count(*) FROM workflow_versions"))
    assert before == 1

    refused = await admin_client.post(
        f"/api/workflows/{workflow['id']}/versions",
        json={
            "graph": graph_of(
                [
                    {"id": "input", "kind": "input"},
                    {"id": "call", "kind": "tool", "tool": "mcp:현장/record", "arguments": {}},
                    {"id": "answer", "kind": "answer"},
                ],
                [("input", "call"), ("call", "answer")],
            )
        },
    )
    assert refused.status_code == 400
    assert "등록되지 않은 도구" in refused.json()["detail"]
    assert "현장/record" in refused.json()["detail"]

    assert (await db.scalar(text("SELECT count(*) FROM workflow_versions"))) == 1
    listed = (await admin_client.get(f"/api/workflows/{workflow['id']}/versions")).json()
    assert [row["version"] for row in listed] == [1]


async def test_a_cycle_is_refused_at_save(admin_client, corpus, db):
    """A graph whose edges loop has no execution order at all, so it is not a
    procedure that behaves oddly - it is not a procedure. Refused here rather
    than discovered by the executor's depth counter, which exists for the cases
    static analysis cannot see (a graph edited in the database, a callee changed
    after this was saved) and not as the first line of defence."""
    workflow = await create_workflow(admin_client, name="순환")

    refused = await admin_client.post(
        f"/api/workflows/{workflow['id']}/versions",
        json={
            "graph": graph_of(
                [
                    {"id": "input", "kind": "input"},
                    rag_node("s1"),
                    rag_node("s2", query="다이아지논"),
                    {"id": "answer", "kind": "answer"},
                ],
                [("input", "s1"), ("s1", "s2"), ("s2", "s1"), ("s1", "answer")],
            )
        },
    )
    assert refused.status_code == 400
    assert "순환" in refused.json()["detail"]
    assert (await db.scalar(text("SELECT count(*) FROM workflow_versions"))) == 1


async def test_a_hand_picked_tool_outside_the_workflows_list_is_refused_too(admin_client, corpus):
    """The manual half of the same boundary. Fencing the graph while leaving the
    composer's own tool picker open would fence the machine and not the human,
    which is the wrong way round."""
    workflow = await create_workflow(admin_client, name="읽기만", tool_ids=[str(corpus["read_tool"])])
    refused = await admin_client.post(
        "/api/chat",
        json={
            "message": "질문",
            "workflow_id": workflow["id"],
            "tool_calls": [{"tool_id": str(corpus["write_tool"]), "arguments": {}}],
        },
    )
    assert refused.status_code == 403
    assert "도구" in refused.json()["detail"]


async def test_a_rag_node_naming_no_collections_cannot_search_outside_the_workflow(db, corpus):
    """The hole that was actually there: `collections: []` meant "everything".

    An author is entitled to omit the field - "search everything this workflow
    may see" is a normal node - and the executor used to turn the resulting empty
    tuple back into `collection_ids=None`, which is every collection in the
    DATABASE rather than every collection in the catalogue. So a workflow's
    restriction was one omitted JSON key away from gone. `validate_graph` now
    writes the catalogue out at save time, where every other name is resolved.
    """
    restricted = ResolvedWorkflow(
        id=uuid.uuid4(), name="비료 전용", collection_ids=frozenset({corpus["collection_a"]})
    )
    available = await load_available(db, None, restricted)
    graph = validate_graph(
        graph_of(
            [
                {"id": "input", "kind": "input"},
                rag_node(query="다이아지논"),
                {"id": "answer", "kind": "answer"},
            ],
            [("input", "search"), ("search", "answer")],
        ),
        available,
        settings=settings_with(),
    )
    search = graph.by_id()["search"]
    assert search.rag_collection_ids == (corpus["collection_a"],)
    assert corpus["collection_b"] not in search.rag_collection_ids


# ---------------------------------------------------------------------------
# Versions: every save is one, and every one is reachable again
# ---------------------------------------------------------------------------


async def test_every_save_is_a_version_and_exactly_one_is_active(admin_client, corpus, db):
    """1, 2, 3 - the create seeds version 1 and each save adds the next, active.

    "Exactly one active" is a partial unique index rather than app code, so this
    reads the database directly: a half-finished activation that left two rows
    active would be an IntegrityError there, and `load_available` could otherwise
    silently run whichever one it saw first."""
    workflow = await create_workflow(admin_client, name="버전 대상")
    for query in ("비료", "농약"):
        saved = await admin_client.post(
            f"/api/workflows/{workflow['id']}/versions",
            json={
                "graph": graph_of(
                    [
                        {"id": "input", "kind": "input"},
                        rag_node(query=query),
                        {"id": "answer", "kind": "answer"},
                    ],
                    [("input", "search"), ("search", "answer")],
                ),
                "note": f"{query} 검색",
            },
        )
        assert saved.status_code == 201, saved.text

    listed = (await admin_client.get(f"/api/workflows/{workflow['id']}/versions")).json()
    # Newest first: this is the 되돌리기 list, and the thing a person wants back is
    # almost always the one they just replaced.
    assert [row["version"] for row in listed] == [3, 2, 1]
    assert [row["is_active"] for row in listed] == [True, False, False]
    assert listed[0]["note"] == "농약 검색"
    assert listed[0]["created_by_email"] == "workflow-admin@example.com"

    active = await db.scalar(
        text("SELECT count(*) FROM workflow_versions WHERE is_active AND workflow_id = :id"),
        {"id": workflow["id"]},
    )
    assert active == 1


async def test_activating_an_older_version_rolls_the_workflow_back(admin_client, corpus, db):
    """되돌리기 activates the existing row rather than copying it forward, so the
    history stays a history instead of growing a duplicate on every rollback -
    and `GET /api/workflows/{id}` immediately serves the older graph, because
    that is the one request the canvas makes."""
    workflow = await create_workflow(admin_client, name="되돌리기 대상")
    await admin_client.post(
        f"/api/workflows/{workflow['id']}/versions",
        json={
            "graph": graph_of(
                [
                    {"id": "input", "kind": "input"},
                    rag_node(query="바뀐 검색어"),
                    {"id": "answer", "kind": "answer"},
                ],
                [("input", "search"), ("search", "answer")],
            )
        },
    )
    reopened = (await admin_client.get(f"/api/workflows/{workflow['id']}")).json()
    assert reopened["active_version"] == 2

    rolled_back = await admin_client.post(f"/api/workflows/{workflow['id']}/versions/1/activate")
    assert rolled_back.status_code == 200
    assert rolled_back.json()["version"] == 1
    assert rolled_back.json()["is_active"] is True

    after = (await admin_client.get(f"/api/workflows/{workflow['id']}")).json()
    assert after["active_version"] == 1
    # No new row: the rollback is a state change, not a save.
    assert (await db.scalar(text("SELECT count(*) FROM workflow_versions"))) == 2
    assert (
        await db.scalar(text("SELECT count(*) FROM workflow_versions WHERE is_active"))
    ) == 1


async def test_activating_a_version_that_does_not_exist_is_a_404(admin_client, corpus):
    workflow = await create_workflow(admin_client, name="없는 버전")
    refused = await admin_client.post(f"/api/workflows/{workflow['id']}/versions/99/activate")
    assert refused.status_code == 404
    assert "버전" in refused.json()["detail"]


async def test_node_coordinates_survive_a_save_and_a_reload(admin_client, corpus):
    """The coordinates are why `workflow_versions.graph` exists at all: a person
    ARRANGED these boxes, and reopening the canvas has to show the same picture.
    They had nowhere to live while this was `agents`, which is the whole reason
    the old screen had no free layout.

    Round-tripped through the API rather than asserted on the model, because the
    thing that has to be true is what the canvas GETs back."""
    workflow = await create_workflow(admin_client, name="좌표")
    placed = graph_of(
        [
            {"id": "input", "kind": "input", "label": "질문", "x": 12.5, "y": -40},
            rag_node(label="문서 검색", x=300, y=125.5),
            {"id": "answer", "kind": "answer", "label": "답변", "x": 640, "y": -40},
        ],
        [("input", "search"), ("search", "answer")],
    )
    saved = await admin_client.post(f"/api/workflows/{workflow['id']}/versions", json={"graph": placed})
    assert saved.status_code == 201, saved.text

    reopened = (await admin_client.get(f"/api/workflows/{workflow['id']}")).json()
    assert {node["id"]: (node["x"], node["y"]) for node in reopened["graph"]["nodes"]} == {
        "input": (12.5, -40),
        "search": (300, 125.5),
        "answer": (640, -40),
    }


# ---------------------------------------------------------------------------
# GET /api/tools - one menu, three kinds
# ---------------------------------------------------------------------------


async def test_the_tool_menu_lists_rag_mcp_and_workflows_in_one_list(
    admin_client, member_client, corpus
):
    """ONE list, because there is one Tool interface. This is what `@` opens in
    the composer and what the canvas offers on a node, and the three kinds share
    one namespace: `rag`, `mcp:서버/도구`, `workflow:이름`.

    Any authenticated user, exactly as GET /api/mcp/tools is: it lists what a
    question may already reach.

    A disabled MCP tool and a disabled workflow are ABSENT, not merely
    un-runnable - the same rule everywhere else in this file - and a workflow
    that wraps an MCP tool inherits the maximum risk, so a wrapper can never
    present itself as safer than what it calls.
    """
    await create_workflow(admin_client, name="검색만")
    await create_workflow(
        admin_client,
        name="도구 래퍼",
        tool_ids=[str(corpus["write_tool"])],
        graph=graph_of(
            [
                {"id": "input", "kind": "input"},
                {"id": "call", "kind": "tool", "tool": "mcp:현장/record", "arguments": {}},
                {"id": "answer", "kind": "answer"},
            ],
            [("input", "call"), ("call", "answer")],
        ),
    )
    hidden = await create_workflow(admin_client, name="중지됨")
    await admin_client.patch(f"/api/workflows/{hidden['id']}", json={"enabled": False})
    await admin_client.patch(f"/api/mcp/tools/{corpus['read_tool']}", json={"enabled": False})

    listed = await member_client.get("/api/tools")
    assert listed.status_code == 200
    entries = {entry["ref"]: entry for entry in listed.json()}
    assert set(entries) == {"rag", "mcp:현장/record", "workflow:검색만", "workflow:도구 래퍼"}
    assert {entry["kind"] for entry in listed.json()} == {"rag", "mcp", "workflow"}

    # The RAG entry carries the collections, because a search node has to offer
    # them; every other kind carries none. A SUPERSET: registration creates the
    # 일반 collection, and this endpoint lists every collection there is.
    assert {"비료", "농약"} <= {c["name"] for c in entries["rag"]["collections"]}
    assert entries["mcp:현장/record"]["collections"] == []

    # Inherited, computed off the stored graph rather than a column, so an admin
    # reclassifying the tool underneath re-gates the wrapper without a re-save.
    assert entries["workflow:검색만"]["risk_level"] == "read"
    assert entries["workflow:도구 래퍼"]["risk_level"] == "write"


# ---------------------------------------------------------------------------
# What answered, and what happens when it is gone
# ---------------------------------------------------------------------------


async def test_the_workflow_that_answered_survives_a_reload_and_appears_in_the_trace(
    admin_client, corpus
):
    """WHICH workflow and WHICH VERSION of it. The version is half the answer:
    "안전모드가 답했다" is not enough to reproduce anything once somebody has
    edited 안전모드."""
    workflow = await create_workflow(admin_client, name="기록 대상")
    response = await admin_client.post(
        "/api/chat", json={"message": "질문", "workflow_id": workflow["id"]}
    )
    done = parse_sse(response.text)[-1]
    assert done["workflow_name"] == "기록 대상"
    assert done["workflow_version"] == 1

    conversation_id = done["conversation_id"]
    reloaded = (await admin_client.get(f"/api/conversations/{conversation_id}/messages")).json()
    assistant = [m for m in reloaded if m["role"] == "assistant"][0]
    assert assistant["workflow_name"] == "기록 대상"
    assert assistant["workflow_version"] == 1

    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert trace["workflow_name"] == "기록 대상"
    assert trace["workflow_version"] == 1


async def test_deleting_a_workflow_does_not_orphan_the_messages_that_name_it(
    admin_client, corpus, db
):
    """`messages.workflow_name` is a string and `messages.workflow_version` an
    integer - neither a foreign key - exactly so this can be true. An admin
    retiring a workflow must not be able to delete, or cascade away, answers
    other people are still reading, and BOTH halves of "which procedure said
    this" have to stay answerable afterwards.

    The version is the half that is new and the half that would have been easy to
    get wrong: `workflow_versions` cascades from `workflows`, so a foreign key
    there would have taken the number with it."""
    workflow = await create_workflow(admin_client, name="폐기 예정")
    done = parse_sse(
        (
            await admin_client.post("/api/chat", json={"message": "질문", "workflow_id": workflow["id"]})
        ).text
    )[-1]

    assert (await admin_client.delete(f"/api/workflows/{workflow['id']}")).status_code == 204

    reloaded = (
        await admin_client.get(f"/api/conversations/{done['conversation_id']}/messages")
    ).json()
    assistant = [m for m in reloaded if m["role"] == "assistant"][0]
    assert assistant["workflow_name"] == "폐기 예정"
    assert assistant["workflow_version"] == 1
    assert assistant["content"]
    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert trace["workflow_name"] == "폐기 예정"
    assert trace["workflow_version"] == 1

    assert (await db.scalar(text("SELECT count(*) FROM workflows"))) == 0
    # The versions DID go, which is the other side of the same statement: a graph
    # belonging to no workflow is not a historical record, it is a leak.
    assert (await db.scalar(text("SELECT count(*) FROM workflow_versions"))) == 0


async def test_deleting_a_workflow_removes_its_join_rows_but_not_the_collection(
    admin_client, corpus, db
):
    workflow = await create_workflow(
        admin_client,
        name="연결 확인",
        collection_ids=[str(corpus["collection_a"])],
        tool_ids=[str(corpus["read_tool"])],
    )
    await admin_client.delete(f"/api/workflows/{workflow['id']}")
    assert (await db.scalar(text("SELECT count(*) FROM workflow_collections"))) == 0
    assert (await db.scalar(text("SELECT count(*) FROM workflow_tools"))) == 0
    # The CASCADE runs from `workflows` towards the join rows and the versions and
    # stops there. The 비료 collection and the lookup tool are shared resources
    # that other workflows, other answers and the documents screen all still
    # point at.
    remaining = set((await db.scalars(select(Collection.name))).all())
    assert {"비료", "농약"} <= remaining
    assert (await db.scalar(text("SELECT count(*) FROM mcp_tools"))) == 2


# ---------------------------------------------------------------------------
# The default workflow: an empty table changes nothing
# ---------------------------------------------------------------------------


async def test_an_empty_workflows_table_behaves_exactly_as_before(admin_client, corpus, db):
    """The deployment claim, checked rather than asserted.

    TRUNCATED IN THE BODY. The database is session-scoped and `corpus` seeds
    rows, so a version of this test that trusted the fixture ordering would pass
    with every guard in this module removed - the trap tests/conftest.py already
    documents for app_settings, and the trap four agents on this project have
    already fallen into.
    """
    await db.execute(text("TRUNCATE TABLE workflows CASCADE"))
    # COMMITTED, not merely executed. TRUNCATE takes an ACCESS EXCLUSIVE lock, and
    # this session is not the one the API requests below use: leaving the
    # transaction open makes the very first `select(Workflow)` inside the app
    # block on it until the test times out.
    await db.commit()
    assert (await db.scalar(text("SELECT count(*) FROM workflows"))) == 0
    assert (await db.scalar(text("SELECT count(*) FROM workflow_versions"))) == 0
    assert (await admin_client.get("/api/workflows/selectable")).json() == []

    response = await admin_client.post("/api/chat", json={"message": "다이아지논 살포 기준"})
    assert response.status_code == 200
    done = parse_sse(response.text)[-1]
    assert done["workflow_name"] is None
    assert done["workflow_version"] is None

    row = await db.scalar(select(Message).where(Message.id == uuid.UUID(done["message_id"])))
    assert row.workflow_name is None
    assert row.workflow_version is None
    assert row.prompt_name == "answer_agent"
    assert row.model == "gpt-4o"
    # Unrestricted: the whole corpus is still reachable, both collections
    # included, and no graph ran at all.
    trace = (await admin_client.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert {item["filename"] for item in trace["evidence"]} == {"비료.pdf", "농약.pdf"}
    assert trace["plan"] is None


async def test_the_default_workflow_narrows_nothing(db, corpus):
    """DEFAULT_WORKFLOW is not a special case handled somewhere - it is a
    ResolvedWorkflow whose two sets are empty, and empty means unrestricted."""
    available = await load_available(db, None, DEFAULT_WORKFLOW)
    # A superset: registration creates the 일반 collection, and "unrestricted"
    # means every collection there is, whoever made it.
    assert {"비료", "농약"} <= {c.name for c in available.collections}
    assert {t.tool_name for t in available.tools} == {"lookup", "record"}


# ---------------------------------------------------------------------------
# The configuration a workflow actually carries
# ---------------------------------------------------------------------------


async def test_the_workflows_model_is_the_default_and_an_explicit_model_still_wins(
    admin_client, corpus, fake_llm, app
):
    """The workflow supplies the default, never the ceiling. The composer's own
    picker keeps working when a workflow is selected, and the allowlist is still
    the only thing deciding what reaches the provider.

    The conftest pins `answer_models` to [] so a deployment cannot change what the
    suite asserts; a second selectable model is added here because that is exactly
    what this test is about."""
    app.state.settings = app.state.settings.model_copy(update={"answer_models": ["gpt-4o-mini"]})
    workflow = await create_workflow(admin_client, name="모델 지정", answer_model="gpt-4o-mini")

    await admin_client.post("/api/chat", json={"message": "질문", "workflow_id": workflow["id"]})
    assert fake_llm.chat.await_args.kwargs["model"] == "gpt-4o-mini"

    await admin_client.post(
        "/api/chat", json={"message": "질문", "workflow_id": workflow["id"], "model": "gpt-4o"}
    )
    assert fake_llm.chat.await_args.kwargs["model"] == "gpt-4o"


async def test_a_workflows_model_is_still_checked_against_the_allowlist(admin_client, corpus, app, db):
    """An operator can drop a model from ANSWER_MODELS long after an admin picked
    it. The row must not be able to smuggle it past the gate, so the check on the
    admin form is not the only one."""
    workflow = await create_workflow(admin_client, name="사라진 모델", answer_model="gpt-4o")
    await db.execute(
        text("UPDATE workflows SET answer_model = 'gpt-4o-mini' WHERE id = :id"),
        {"id": workflow["id"]},
    )
    await db.commit()

    app.state.settings = app.state.settings.model_copy(
        update={"answer_model": "gpt-4o", "answer_models": []}
    )
    refused = await admin_client.post(
        "/api/chat", json={"message": "질문", "workflow_id": workflow["id"]}
    )
    assert refused.status_code == 400
    assert "gpt-4o-mini" in refused.json()["detail"]


async def test_a_workflow_can_carry_its_own_prompt_from_the_store(admin_client, corpus, fake_llm, db):
    """"prompt from the prompt store", which is why POST /api/prompts exists: a
    workflow that could only ever name the deployment's own system prompt would
    be missing the field the feature is about."""
    created = await admin_client.post(
        "/api/prompts", json={"name": "field_agent", "text": "너는 현장 담당자다. 짧게 답한다."}
    )
    assert created.status_code == 201
    workflow = await create_workflow(admin_client, name="현장", prompt_name="field_agent")

    response = await admin_client.post(
        "/api/chat", json={"message": "질문", "workflow_id": workflow["id"]}
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


async def test_updating_a_workflow_replaces_its_lists_and_can_clear_them(admin_client, corpus):
    """An empty list is a real state - it is what "unrestricted" is - so the two
    lists are replaced wholesale when present rather than merged."""
    workflow = await create_workflow(
        admin_client,
        name="편집 대상",
        collection_ids=[str(corpus["collection_a"])],
        tool_ids=[str(corpus["read_tool"])],
    )
    assert [c["name"] for c in workflow["collections"]] == ["비료"]

    swapped = await admin_client.patch(
        f"/api/workflows/{workflow['id']}", json={"collection_ids": [str(corpus["collection_b"])]}
    )
    assert [c["name"] for c in swapped.json()["collections"]] == ["농약"]
    # Omitted, so untouched.
    assert [t["name"] for t in swapped.json()["tools"]] == ["lookup"]

    cleared = await admin_client.patch(f"/api/workflows/{workflow['id']}", json={"tool_ids": []})
    assert cleared.json()["tools"] == []


async def test_patching_one_field_leaves_every_other_field_alone(admin_client, corpus, app):
    """FOUND BY DRIVING IT. The row's 중지 button sends `{"enabled": false}` and
    nothing else, and an `is not None` read of a NULLABLE field cannot tell that
    from `{"answer_model": null}` - so pausing a workflow silently cleared the
    model an admin had chosen for it. `model_fields_set` is what tells omitted
    from null, and an explicit null still clears, because "back to the
    deployment default" has to be reachable.

    The active graph is left alone too: a PATCH carries no graph at all, because
    every graph save is a version and a PATCH that quietly made one would hide
    that from the 되돌리기 list."""
    app.state.settings = app.state.settings.model_copy(update={"answer_models": ["gpt-4o-mini"]})
    workflow = await create_workflow(
        admin_client,
        name="부분 수정",
        description="설명",
        answer_model="gpt-4o-mini",
        collection_ids=[str(corpus["collection_a"])],
    )

    paused = (
        await admin_client.patch(f"/api/workflows/{workflow['id']}", json={"enabled": False})
    ).json()
    assert paused["enabled"] is False
    assert paused["answer_model"] == "gpt-4o-mini"
    assert paused["description"] == "설명"
    assert [c["name"] for c in paused["collections"]] == ["비료"]
    assert paused["active_version"] == 1
    assert paused["graph"] == workflow["graph"]

    # An EXPLICIT null still clears, or an admin could never get back to the
    # deployment default.
    cleared = (
        await admin_client.patch(f"/api/workflows/{workflow['id']}", json={"answer_model": None})
    ).json()
    assert cleared["answer_model"] is None


async def test_a_workflow_row_names_its_creator_its_tools_and_its_starter_graph(
    admin_client, corpus, db
):
    """The starter graph is part of the row an admin gets back, not something the
    canvas has to ask for separately: a workflow that saved and could not run
    would be the state `input`/`answer` being undeletable exists to prevent."""
    workflow = await create_workflow(admin_client, name="표시", tool_ids=[str(corpus["read_tool"])])
    assert workflow["created_by_email"] == "workflow-admin@example.com"
    assert workflow["tools"][0]["server_name"] == "현장"
    assert workflow["tools"][0]["risk_level"] == "read"
    assert workflow["active_version"] == 1
    assert [node["kind"] for node in workflow["graph"]["nodes"]] == ["input", "tool", "answer"]

    stored = await db.scalar(select(Workflow).where(Workflow.id == uuid.UUID(workflow["id"])))
    assert stored.prompt_name == "answer_agent"
    assert stored.answer_model is None
