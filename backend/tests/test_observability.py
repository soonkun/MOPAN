"""Slice 5: conversation trace, feedback, and runtime settings.

Every test here is a guard that was made to fail before it was kept. The three
that are easiest to write and useless are called out where they appear: an
"empty table" test that runs against a table somebody else seeded, a "404 not
403" test that never checks the code, and a "the override applied" test that
reads back the value it just wrote instead of the behaviour it changed.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from test_chat import make_fake_llm, parse_sse, vec

from app.chat.prompt import (
    ANSWER_SYSTEM_PROMPT,
    MANDATORY_TOKEN_ALLOWANCE,
    PromptTemplate,
)
from app.chat.service import answer, build_trace
from app.core.settings_store import (
    RUNTIME_SAFE_SETTINGS,
    SettingSpec,
    apply_overrides,
    effective_settings,
    validated_settings,
)
from app.core.tokens import count_tokens
from app.models.app_setting import AppSetting
from app.models.chunk import Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.feedback import MessageFeedback
from app.models.message import Message
from app.models.user import User
from app.retrieval.evidence import Evidence

pytestmark = pytest.mark.integration


# The words are in every chunk, so BOTH halves of hybrid retrieval find all three
# and every trace row carries a vector_rank AND a keyword_rank. A question whose
# words are absent from the corpus leaves keyword_rank null, which is legitimate
# and would make the "every stage is recorded" assertion vacuous.
QUESTION = "tomato blight"
CHUNK_TEXTS = [
    "tomato blight spreads through infected soil " * 12,
    "tomato blight is controlled by crop rotation " * 12,
    "tomato blight leaves brown lesions on fruit " * 12,
]


@pytest.fixture
def fake_llm(app):
    provider = make_fake_llm()
    app.state.llm_provider = provider
    return provider


@pytest_asyncio.fixture
async def owner(client, fake_llm, db):
    """The first account bootstraps admin, which is what the 고급 설정 tests need,
    and owns the conversations the 404 tests probe."""
    await client.post("/api/auth/register", json={"email": "owner@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "owner@example.com", "password": "pw123456"})

    user = await db.scalar(select(User).where(User.email == "owner@example.com"))
    collection = Collection(name="관측", created_by=user.id)
    db.add(collection)
    await db.flush()
    document = Document(
        collection_id=collection.id,
        filename="역병 방제.pdf",
        file_type="pdf",
        size_bytes=1,
        storage_path="x",
        status="indexed",
        uploaded_by=user.id,
    )
    db.add(document)
    await db.flush()
    db.add_all(
        [
            Chunk(
                document_id=document.id,
                chunk_index=index,
                content=content,
                token_count=count_tokens(content),
                char_count=len(content),
                page=index + 1,
                section=None,
                chunk_metadata={},
                # Identical vectors: this suite is about what happens AFTER
                # retrieval, and an ordering that depended on cosine noise would
                # make the cut-evidence assertions flap.
                embedding=vec(1.0),
            )
            for index, content in enumerate(CHUNK_TEXTS)
        ]
    )
    await db.commit()
    return client


@pytest_asyncio.fixture
async def other_client(app, client):
    """A second logged-in account. `client` first, so the account this one gets
    is never the bootstrap admin."""
    await client.post("/api/auth/register", json={"email": "other@example.com", "password": "pw123456"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as other:
        await other.post("/api/auth/login", json={"email": "other@example.com", "password": "pw123456"})
        yield other


async def ask(client, message: str = QUESTION) -> dict:
    """The `done` frame, which now carries the assistant message id."""
    response = await client.post("/api/chat", json={"message": message})
    assert response.status_code == 200, response.text
    return parse_sse(response.text)[-1]


# --- The trace ---------------------------------------------------------------


async def test_done_frame_carries_the_real_assistant_message_id(owner, db):
    done = await ask(owner)
    message = await db.get(Message, uuid.UUID(done["message_id"]))
    assert message is not None
    assert message.role == "assistant"


async def test_trace_shows_every_retrieval_stage_and_the_answer_metadata(owner):
    done = await ask(owner)
    trace = (await owner.get(f"/api/messages/{done['message_id']}/trace")).json()

    assert trace["has_trace"] is True
    assert trace["model"] == "gpt-4o"
    assert trace["prompt_name"] == "answer_agent"
    assert trace["prompt_version"] == "1"
    assert trace["latency_ms"] is not None and trace["retrieval_ms"] is not None
    assert trace["usage"] == {"total_tokens": 42}

    assert trace["retrieval"]["rrf_k"] == 60
    assert trace["retrieval"]["evidence_count"] == len(CHUNK_TEXTS)
    assert trace["retrieval"]["token_budget"] > 0
    # What the prompt cost and what it was allowed - the pair that answers "did
    # the prompt take my evidence", which the budget alone cannot.
    # >=: 시스템 프롬프트 끝에 사용자의 "지금" 한 줄이 덧붙는다
    # (app/core/localtime.py). 날짜·시각이라 길이가 조금씩 달라 정확일치는
    # 시계에 대한 단언이 되고 만다 - 여기가 지키는 것은 "프롬프트 비용이
    # 기록된다"이다.
    assert trace["retrieval"]["prompt_tokens"] >= count_tokens(ANSWER_SYSTEM_PROMPT)
    assert trace["retrieval"]["mandatory_allowance"] == MANDATORY_TOKEN_ALLOWANCE

    item = trace["evidence"][0]
    # The four Slice 1 kept separate rather than collapsing into one score. If any
    # of them is missing here, the screen can show a total and nothing else.
    assert item["vector_rank"] is not None
    assert item["keyword_rank"] is not None
    assert item["rrf_score"] is not None
    assert "rerank_score" in item
    assert item["filename"] == "역병 방제.pdf"
    assert item["tokens"] > 0


async def test_the_trace_records_evidence_the_token_budget_cut(owner, db):
    """THE test of this slice. "Why did it not answer from the document I
    uploaded" is almost always "it was rank 3 and the budget stopped at 2", and
    before this nothing in the product recorded that at all.

    Self-calibrating rather than hard-coding a budget: the first answer reports
    what each item actually cost, and the budget is then set to fit exactly one
    of them. A hard-coded number would silently stop cutting anything the day the
    system prompt is edited, and this test would keep passing.
    """
    first = await ask(owner)
    trace = (await owner.get(f"/api/messages/{first['message_id']}/trace")).json()
    assert [item["included"] for item in trace["evidence"]] == [True] * len(CHUNK_TEXTS)

    # The system prompt is NOT added in: the budget bounds the evidence and the
    # history, and the prompt is charged against MANDATORY_TOKEN_ALLOWANCE
    # instead (app/chat/prompt.py). Adding it here would have made this budget
    # grow with the prose and quietly stop cutting anything at all.
    budget = trace["evidence"][0]["tokens"] + 60
    put = await owner.put("/api/settings/ANSWER_CONTEXT_TOKEN_BUDGET", json={"value": str(budget)})
    assert put.status_code == 200, put.text

    second = await ask(owner)
    trace = (await owner.get(f"/api/messages/{second['message_id']}/trace")).json()
    included = [item for item in trace["evidence"] if item["included"]]
    cut = [item for item in trace["evidence"] if not item["included"]]
    assert included, "at least one item must still have reached the prompt"
    assert cut, "the budget was set to fit one item; the rest must be recorded as cut"
    assert trace["retrieval"]["included_count"] == len(included)
    # The cut item is still fully described - filename, page and the ranks that
    # explain why it was ordered where it was. Recording only that "something was
    # dropped" would answer none of the questions this screen exists for.
    assert cut[0]["filename"] == "역병 방제.pdf"
    assert cut[0]["page"] is not None
    assert cut[0]["snippet"]

    stored = await db.get(Message, uuid.UUID(second["message_id"]))
    assert stored.trace["retrieval"]["token_budget"] == budget


def test_build_trace_marks_cut_items_by_identity_not_by_position():
    """`build_prompt` happens to return a prefix, so `index < len(used)` would
    agree today. It is not asserted anywhere that it will keep doing so, and if
    it ever skips-and-continues the per-item scores would be attached to the
    wrong rows in silence."""
    evidence = [Evidence(source_type="rag", ref=f"chunk:{i}", content="x", score=0.1) for i in range(3)]
    trace = build_trace(
        evidence,
        [evidence[0], evidence[2]],
        settings=AsyncMock(
            retrieval_top_n=6,
            retrieval_candidate_limit=20,
            rrf_k=60,
            sparse_weight=1.0,
            answer_context_token_budget=8000,
        ),
        # A real template, not a mock: the trace records what the prompt COSTS,
        # and a Mock attribute is not something tiktoken can count.
        prompt=PromptTemplate(name="p", version="1", text="지침"),
    )
    assert [item["included"] for item in trace["evidence"]] == [True, False, True]


async def test_an_answer_written_before_the_trace_column_is_not_an_error(owner, db):
    """`{}` is what every message written before migration 0005 carries. The
    screen has to say "no trace" rather than 500."""
    done = await ask(owner)
    message = await db.get(Message, uuid.UUID(done["message_id"]))
    message.trace = {}
    await db.commit()

    trace = (await owner.get(f"/api/messages/{done['message_id']}/trace")).json()
    assert trace["has_trace"] is False
    assert trace["evidence"] == []
    # The columns are real even when the JSON is not.
    assert trace["model"] == "gpt-4o"


async def test_another_users_trace_is_404_not_403(owner, other_client):
    """A 403 would confirm that the message id exists, which is the whole reason
    get_owned_conversation answers 404. Both halves are asserted: the OWNER gets
    200 on the same id, so a route that 404s for everybody would fail this."""
    done = await ask(owner)
    assert (await owner.get(f"/api/messages/{done['message_id']}/trace")).status_code == 200

    stolen = await other_client.get(f"/api/messages/{done['message_id']}/trace")
    assert stolen.status_code == 404
    assert stolen.json()["detail"] == "답변을 찾을 수 없습니다."
    # The same status for an id that never existed, so the two are indistinguishable.
    unknown = await other_client.get(f"/api/messages/{uuid.uuid4()}/trace")
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == stolen.json()["detail"]


async def test_a_user_turn_has_no_trace(owner, db):
    """Only an assistant answer has one, and asking for a user turn's must not
    leak that the id resolved to a real row."""
    done = await ask(owner)
    user_message = await db.scalar(
        select(Message).where(
            Message.conversation_id == uuid.UUID(done["conversation_id"]), Message.role == "user"
        )
    )
    assert (await owner.get(f"/api/messages/{user_message.id}/trace")).status_code == 404


async def test_trace_requires_auth(client):
    assert (await client.get(f"/api/messages/{uuid.uuid4()}/trace")).status_code == 401


# --- Feedback ----------------------------------------------------------------


async def test_feedback_is_one_per_user_per_message_and_changeable(owner, db):
    done = await ask(owner)
    url = f"/api/messages/{done['message_id']}/feedback"

    up = await owner.put(url, json={"rating": "up"})
    assert up.status_code == 200
    assert up.json()["rating"] == "up"

    down = await owner.put(url, json={"rating": "down", "comment": "근거가 엉뚱합니다."})
    assert down.status_code == 200
    assert down.json()["rating"] == "down"
    assert down.json()["comment"] == "근거가 엉뚱합니다."

    rows = (
        await db.scalars(
            select(MessageFeedback).where(MessageFeedback.message_id == uuid.UUID(done["message_id"]))
        )
    ).all()
    assert len(rows) == 1, "a changed rating must UPDATE, never insert a second row"
    assert rows[0].rating == "down"
    assert rows[0].updated_at > rows[0].created_at


async def test_feedback_rides_the_transcript_so_a_reload_still_shows_it(owner):
    done = await ask(owner)
    await owner.put(f"/api/messages/{done['message_id']}/feedback", json={"rating": "up"})

    messages = (await owner.get(f"/api/conversations/{done['conversation_id']}/messages")).json()
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["feedback"]["rating"] == "up"
    assert next(m for m in messages if m["role"] == "user")["feedback"] is None


async def test_feedback_joins_to_the_trace(owner, db):
    """The reason this table exists: "every down-vote since Tuesday, with the
    evidence its budget cut" has to be one query, not a log grep."""
    done = await ask(owner)
    await owner.put(f"/api/messages/{done['message_id']}/feedback", json={"rating": "down"})

    row = (
        await db.execute(
            select(MessageFeedback.rating, Message.trace)
            .join(Message, Message.id == MessageFeedback.message_id)
            .where(MessageFeedback.rating == "down")
        )
    ).one()
    assert row.rating == "down"
    assert row.trace["evidence"]


async def test_feedback_on_another_users_message_is_404(owner, other_client):
    done = await ask(owner)
    stolen = await other_client.put(f"/api/messages/{done['message_id']}/feedback", json={"rating": "up"})
    assert stolen.status_code == 404


async def test_feedback_rejects_a_rating_that_is_not_up_or_down(owner):
    done = await ask(owner)
    bad = await owner.put(f"/api/messages/{done['message_id']}/feedback", json={"rating": "meh"})
    assert bad.status_code == 422


async def test_feedback_requires_auth(client):
    posted = await client.put(f"/api/messages/{uuid.uuid4()}/feedback", json={"rating": "up"})
    assert posted.status_code == 401


# --- Runtime settings --------------------------------------------------------


async def _clear_overrides(db) -> None:
    """Explicitly, in the test. `clean_db` truncates app_settings between tests,
    but a "when the table is empty" test that relies on a fixture to empty it
    passes just as happily with its own guard deleted - which is how a
    prompt-admin test in this project went green over a hole. This is the line
    that makes the precondition the test's own."""
    await db.execute(delete(AppSetting))
    await db.commit()


async def test_an_empty_settings_table_behaves_exactly_like_the_environment(owner, db, app):
    await _clear_overrides(db)
    assert await db.scalar(text("SELECT count(*) FROM app_settings")) == 0

    base = app.state.settings
    listed = (await owner.get("/api/settings")).json()
    assert [s["key"] for s in listed["settings"]] == list(RUNTIME_SAFE_SETTINGS)
    for entry in listed["settings"]:
        spec = RUNTIME_SAFE_SETTINGS[entry["key"]]
        assert entry["overridden"] is False
        assert entry["value"] == entry["env_value"] == getattr(base, spec.field)

    # And the behaviour, not just the report: retrieval with no rows in the table
    # returns what RETRIEVAL_TOP_N says it should.
    results = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(results) == min(base.retrieval_top_n, len(CHUNK_TEXTS))


async def test_an_override_changes_behaviour_on_the_very_next_request(owner, db):
    """No restart, no cache to invalidate - the same property `get_prompt` has.

    Asserted on the RESULT of a search, not on the value read back from
    GET /api/settings: reading back what was just written proves the row exists
    and nothing about whether anything uses it.
    """
    await _clear_overrides(db)
    before = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(before) == len(CHUNK_TEXTS)

    put = await owner.put("/api/settings/RETRIEVAL_TOP_N", json={"value": "1"})
    assert put.status_code == 200
    assert put.json()["value"] == 1 and put.json()["overridden"] is True

    after = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(after) == 1

    # And removing the override puts it back, which is what makes 기본값으로
    # 되돌리기 a promise rather than a button.
    assert (await owner.delete("/api/settings/RETRIEVAL_TOP_N")).status_code == 200
    restored = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(restored) == len(CHUNK_TEXTS)


@pytest.mark.parametrize("key", ["OPENAI_API_KEY", "DATABASE_URL", "EMBEDDING_DIM", "EMBEDDING_MODEL"])
async def test_a_key_that_is_not_runtime_safe_is_refused(owner, key):
    """Including the two the screen explains rather than offers. EMBEDDING_MODEL
    and EMBEDDING_DIM need a migration and a full re-index; a control for them
    would corrupt the corpus quietly."""
    put = await owner.put(f"/api/settings/{key}", json={"value": "x"})
    assert put.status_code == 400
    assert key in put.json()["detail"]
    assert (await owner.delete(f"/api/settings/{key}")).status_code == 400


async def test_the_api_key_can_be_neither_read_nor_written(owner, db, app):
    """Structural, not a filter: RUNTIME_SAFE_SETTINGS has no entry for it, so
    there is nothing for a future key to be added to by accident. The env-only
    notes are checked too - they are rendered on screen."""
    assert "OPENAI_API_KEY" not in RUNTIME_SAFE_SETTINGS
    body = (await owner.get("/api/settings")).json()
    serialised = str(body)
    assert "OPENAI_API_KEY" not in serialised
    assert "openai_api_key" not in serialised
    if app.state.settings.openai_api_key:
        assert app.state.settings.openai_api_key not in serialised
    assert {item["key"] for item in body["env_only"]} == {
        "EMBEDDING_MODEL",
        "EMBEDDING_DIM",
        # off/targeted/blanket - a choice, not a number, so it has no editable
        # spec and appears here instead. See app/core/settings_store.py.
        "NEIGHBOR_EXPANSION",
        # simple/bigram - also a choice, and changing it additionally invalidates
        # every stored tsvector until scripts/backfill_tsv.py has run.
        "SPARSE_TOKENIZER",
        # 켜짐/꺼짐 - 숫자 입력 칸으로 다룰 수 없는 값. 측정과 함께 바꾸는
        # 배포 결정이라 환경변수로만 바꾼다. app/retrieval/collapse.py 참조.
        "RETRIEVAL_COLLAPSE",
        # 어휘 빈도표(scripts/build_lexeme_df.py)가 먼저 있어야 켤 수 있는 값.
        "SPARSE_DF_TRIM",
        # 대화형 발화를 검색 전에 골라내는 판정. 실패는 항상 검색으로 강등.
        "INTENT_GATE",
        # 사례 서술을 첫 검색 전에 용어 질의로 다시 쓰는 단계. 측정 후 켠다.
        "RETRIEVAL_RECAST",
        # A model name, and "" means the rerank stage is absent from the call
        # path. It is listed so the screen SAYS the stage exists and is off,
        # rather than leaving the user to infer it from silence.
        "RERANK_MODEL",
    }
    assert all(item["reason"] for item in body["env_only"])

    # Even a row written straight into the table cannot make it readable or
    # applicable: load_overrides drops keys that are not in the spec table.
    db.add(AppSetting(key="OPENAI_API_KEY", value="sk-forged"))
    await db.commit()
    listed = await owner.get("/api/settings")
    # 200, not just "the value is absent": without the filter in load_overrides
    # this row reaches apply_overrides, raises KeyError inside the settings
    # dependency, and every request in the app becomes a 500.
    assert listed.status_code == 200
    assert "sk-forged" not in str(listed.json())
    assert (await owner.post("/api/search", json={"query": QUESTION})).status_code == 200


async def test_settings_are_admin_only(owner, other_client):
    assert (await other_client.get("/api/settings")).status_code == 403
    denied = await other_client.put("/api/settings/RETRIEVAL_TOP_N", json={"value": "2"})
    assert denied.status_code == 403
    assert (await other_client.delete("/api/settings/RETRIEVAL_TOP_N")).status_code == 403


async def test_settings_require_auth(client):
    assert (await client.get("/api/settings")).status_code == 401


async def test_a_value_outside_its_range_is_refused_in_korean(owner):
    for value in ("0", "999999", "abc"):
        refused = await owner.put("/api/settings/RETRIEVAL_TOP_N", json={"value": value})
        assert refused.status_code == 400
        assert any("가" <= ch <= "힣" for ch in refused.json()["detail"])


async def test_a_pair_that_only_breaks_together_is_refused(owner, db):
    """CHUNK_OVERLAP is in range on its own and invalid against CHUNK_SIZE. A
    per-key check would save it and every later ingestion would raise."""
    await _clear_overrides(db)
    assert (await owner.put("/api/settings/CHUNK_SIZE", json={"value": "400"})).status_code == 200
    refused = await owner.put("/api/settings/CHUNK_OVERLAP", json={"value": "800"})
    assert refused.status_code == 400
    assert await db.scalar(select(AppSetting.value).where(AppSetting.key == "CHUNK_OVERLAP")) is None


async def test_a_bad_row_is_ignored_rather_than_taking_answering_down(owner, db):
    """The read path never raises. A row that does not parse - only reachable by
    editing the table by hand - is dropped with a log, exactly as get_prompt
    falls back to the module constant."""
    await _clear_overrides(db)
    db.add(AppSetting(key="RETRIEVAL_TOP_N", value="not-a-number"))
    await db.commit()
    results = (await owner.post("/api/search", json={"query": "tomato blight"})).json()["results"]
    assert len(results) == len(CHUNK_TEXTS)


# --- The store on its own ----------------------------------------------------


async def test_effective_settings_returns_the_base_when_the_table_is_empty(db, app):
    await _clear_overrides(db)
    base = app.state.settings
    assert await effective_settings(db, base) is base


def test_apply_overrides_drops_an_invalid_chunk_pair_as_a_pair(app):
    """Keeping the half that parsed is not a repair: FixedChunking raises on
    overlap >= size, so a half-applied pair breaks ingestion instead of degrading
    it."""
    base = app.state.settings
    applied = apply_overrides(base, {"CHUNK_SIZE": "400", "CHUNK_OVERLAP": "800"})
    assert applied.chunk_size == base.chunk_size
    assert applied.chunk_overlap == base.chunk_overlap


def test_validated_settings_refuses_a_key_outside_the_spec_table(app):
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validated_settings(app.state.settings, {"OPENAI_API_KEY": "sk-forged"})


def test_every_spec_names_a_real_settings_field(app):
    """A typo in `field` would make a setting that saves, reports itself as
    applied, and changes nothing."""
    for spec in RUNTIME_SAFE_SETTINGS.values():
        assert hasattr(app.state.settings, spec.field), spec.key
        assert spec.minimum < spec.maximum


def test_a_spec_parses_and_bounds_its_own_value():
    spec = SettingSpec(
        key="X", field="rrf_k", kind=int, minimum=1, maximum=3, group="g", label="l", help="h"
    )
    assert spec.parse("2") == 2
    for bad in ("0", "4", "two", ""):
        with pytest.raises(ValueError):
            spec.parse(bad)


async def test_answer_still_takes_no_session(app, fake_llm):
    """The Slice 3 seam, re-checked from this slice: the trace is built inside
    answer() from what it already has, so nothing here grew a `db` parameter."""
    result = await answer(
        fake_llm,
        "question",
        [],
        [Evidence(source_type="rag", ref="chunk:1", content="evidence text", score=0.5)],
        settings=app.state.settings,
    )
    assert result.trace["evidence"][0]["included"] is True
    assert result.trace["version"] == 1
