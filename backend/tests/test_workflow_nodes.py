"""새 노드 넷(2026-09-13): 모델 호출·질문 분류·정보 추출·텍스트 조합. 검증기(저장)와 실행기(런) 양쪽."""
from unittest.mock import AsyncMock

import pytest

from app.llm.base import ChatResult
from app.workflow.executor import WorkflowRun
from app.workflow.expr import ExpressionError, render_template, template_references
from app.workflow.graph import GraphError, validate_graph
from tests.test_workflow_engine import (
    ANSWER_NODE,
    INPUT_NODE,
    NullSession,
    rag_evidence,
    resources_with,
    settings_with,
    states_of,
)


def graph(*middle: dict, edges: list[dict] | None = None) -> dict:
    nodes = [INPUT_NODE, *middle, ANSWER_NODE]
    if edges is None:
        ids = [n["id"] for n in nodes]
        edges = [{"from": a, "to": b} for a, b in zip(ids, ids[1:], strict=False)]
    return {"nodes": nodes, "edges": edges}


def llm(node_id="m", **cfg) -> dict:
    return {"id": node_id, "kind": "llm", "config": {"prompt": "요약: {{input.text}}", **cfg}}


def classify(node_id="c", categories=None, **cfg) -> dict:
    return {
        "id": node_id,
        "kind": "classify",
        "config": {"categories": categories or [{"id": "law", "label": "법령"}, {"id": "chat", "label": "잡담"}], **cfg},
    }


def extract(node_id="e", fields=None, **cfg) -> dict:
    return {"id": node_id, "kind": "extract", "config": {"fields": fields or [{"name": "city", "type": "string"}], **cfg}}


def template(node_id="t", text="질문: {{input.text}}") -> dict:
    return {"id": node_id, "kind": "template", "config": {"text": text}}


def provider_saying(*replies: str) -> AsyncMock:
    p = AsyncMock()
    it = iter(replies)
    p.chat = AsyncMock(side_effect=lambda *a, **k: ChatResult(content=next(it), model="m", usage={}))
    return p


def run_of(raw, provider, **kwargs) -> WorkflowRun:
    return WorkflowRun(
        validate_graph(raw, resources_with(), settings=settings_with()),
        resources_with(),
        question="대전 날씨 알려줘",
        settings=settings_with(),
        llm_provider=provider,
        sessionmaker=lambda: NullSession(),
        reranker=object(),
        **kwargs,
    )


async def drain(run: WorkflowRun) -> list[dict]:
    return [frame async for frame in run.stream()]


# --- 식: 템플릿 ---------------------------------------------------------------


def test_template_rendering_substitutes_scalars_and_refuses_structures():
    scope = {"input": {"text": "안녕"}, "s": {"count": 2, "top": {"title": "a.pdf"}, "items": [1]}}
    assert render_template("Q={{input.text}} n={{s.count}} t={{ s.top.title }}", scope) == "Q=안녕 n=2 t=a.pdf"
    assert [r.segments for r in template_references("{{input.text}} {{s.count}}")] == [("input", "text"), ("s", "count")]
    with pytest.raises(ExpressionError):
        render_template("{{s.items}}", scope)
    with pytest.raises(ExpressionError):
        template_references("{{a b}}")


# --- 검증 ------------------------------------------------------------------------


def test_validation_of_the_new_kinds():
    validate_graph(graph(llm(), extract(), template()), resources_with(), settings=settings_with())
    with pytest.raises(GraphError, match="프롬프트가 비어"):
        validate_graph(graph({"id": "m", "kind": "llm", "config": {"prompt": " "}}), resources_with(), settings=settings_with())
    with pytest.raises(GraphError, match="갈래가 2"):
        validate_graph(graph(classify(categories=[{"id": "only", "label": "1"}]), edges=[{"from": "input", "to": "c"}, {"from": "c", "to": "answer", "when": "only"}]), resources_with(), settings=settings_with())
    with pytest.raises(GraphError, match="이름이 올바른 항목"):
        validate_graph(graph(extract(fields=[{"name": "bad name"}])), resources_with(), settings=settings_with())
    with pytest.raises(GraphError, match="본문이 비어"):
        validate_graph(graph(template(text="")), resources_with(), settings=settings_with())
    # 앞서 실행되지 않는 노드를 프롬프트가 참조하면 저장 거부
    with pytest.raises(GraphError, match="앞서 실행되지 않는"):
        validate_graph(graph(llm(prompt="{{answer.text}}")), resources_with(), settings=settings_with())
    # 정돈: 모르는 키는 버리고, 갈래 라벨이 비면 id
    g = validate_graph(
        graph(classify(categories=[{"id": "x"}, {"id": "y", "label": "Y", "junk": 1}]),
              edges=[{"from": "input", "to": "c"}, {"from": "c", "to": "answer", "when": "x"}, {"from": "c", "to": "answer", "when": "y"}]),
        resources_with(), settings=settings_with(),
    )
    assert g.by_id()["c"].config["categories"] == [{"id": "x", "label": "x", "description": ""}, {"id": "y", "label": "Y", "description": ""}]
    assert g.to_raw()["nodes"][1]["config"]["categories"][1]["label"] == "Y"


def test_classify_edges_must_name_a_category():
    base = [INPUT_NODE, classify(), {"id": "s", "kind": "tool", "tool": "rag", "arguments": {"query": "{{input.text}}"}}, ANSWER_NODE]
    ok = {"nodes": base, "edges": [{"from": "input", "to": "c"}, {"from": "c", "to": "s", "when": "law"}, {"from": "c", "to": "answer", "when": "chat"}, {"from": "s", "to": "answer"}]}
    validate_graph(ok, resources_with(), settings=settings_with())
    with pytest.raises(GraphError, match="갈래 이름을 지정"):
        validate_graph({"nodes": base, "edges": [{"from": "input", "to": "c"}, {"from": "c", "to": "s"}, {"from": "s", "to": "answer"}]}, resources_with(), settings=settings_with())
    with pytest.raises(GraphError, match="없는 갈래"):
        validate_graph({"nodes": base, "edges": [{"from": "input", "to": "c"}, {"from": "c", "to": "s", "when": "nope"}, {"from": "s", "to": "answer"}]}, resources_with(), settings=settings_with())


# --- 실행 ------------------------------------------------------------------------


async def test_template_is_instant_and_llm_reads_it(monkeypatch):
    provider = provider_saying("요약문입니다")
    raw = graph(template(text="사용자 질문: {{input.text}}"), llm(prompt="{{t.text}} 를 한 줄로", output_fields=[]))
    run = run_of(raw, provider)
    frames = await drain(run)
    assert states_of(run) == {"input": "done", "t": "done", "m": "done", "answer": "done"}
    sent = provider.chat.call_args.args[0]
    assert sent[-1].content == "사용자 질문: 대전 날씨 알려줘 를 한 줄로"
    assert run._scope["m"]["text"] == "요약문입니다"
    outputs = {f["id"]: f.get("output") for f in frames if f["type"] == "step" and f["state"] == "done"}
    assert outputs["t"] == "사용자 질문: 대전 날씨 알려줘" and outputs["m"] == "요약문입니다"
    assert run.evidence() == []  # as_evidence가 아니면 근거가 아니다


async def test_llm_with_output_fields_and_as_evidence():
    provider = provider_saying('{"title": "제목", "score": 3}')
    run = run_of(graph(llm(output_fields=["title", "score"], as_evidence=True)), provider)
    await drain(run)
    assert run._scope["m"] == {"text": '{"title": "제목", "score": 3}', "title": "제목", "score": 3}
    assert provider.chat.call_args.kwargs["response_format"] == {"type": "json_object"}
    assert [e.source_type for e in run.evidence()] == ["llm"]


async def test_classify_opens_only_the_chosen_category(monkeypatch):
    from app.workflow import tools as tools_module

    async def fake_search(db, store, provider, reranker, query, **kwargs):
        return [rag_evidence("chunk:1")]

    monkeypatch.setattr(tools_module, "hybrid_search", fake_search)
    nodes = [
        INPUT_NODE,
        classify(),
        {"id": "s", "kind": "tool", "tool": "rag", "arguments": {"query": "{{input.text}}"}},
        {"id": "s2", "kind": "tool", "tool": "rag", "arguments": {"query": "잡담용"}},
        ANSWER_NODE,
    ]
    edges = [
        {"from": "input", "to": "c"},
        {"from": "c", "to": "s", "when": "law"},
        {"from": "c", "to": "s2", "when": "chat"},
        {"from": "s", "to": "answer"},
        {"from": "s2", "to": "answer"},
    ]
    run = run_of({"nodes": nodes, "edges": edges}, provider_saying('{"category": "law"}'))
    await drain(run)
    states = states_of(run)
    assert states["c"] == "done" and states["s"] == "done" and states["s2"] == "skipped" and states["answer"] == "done"
    assert run._scope["c"] == {"category": "law", "label": "법령"}
    # 라벨로 답해도 봐준다
    run2 = run_of({"nodes": nodes, "edges": edges}, provider_saying('{"category": "잡담"}'))
    await drain(run2)
    assert states_of(run2)["s2"] == "done" and states_of(run2)["s"] == "skipped"
    # 정해진 갈래 밖의 답은 실패 - 어느 갈래도 열리지 않는다(분기와 같은 규칙)
    run3 = run_of({"nodes": nodes, "edges": edges}, provider_saying('{"category": "뭐"}'))
    await drain(run3)
    assert states_of(run3)["c"] == "failed" and states_of(run3)["s"] == "skipped" and states_of(run3)["s2"] == "skipped"


async def test_extract_feeds_a_tool_argument():
    from app.workflow import tools as tools_module

    seen: list[str] = []

    async def fake_search(db, store, provider, reranker, query, **kwargs):
        seen.append(query)
        return []

    import pytest

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tools_module, "hybrid_search", fake_search)
    try:
        raw = graph(
            extract(fields=[{"name": "city", "type": "string"}, {"name": "days", "type": "number"}]),
            {"id": "s", "kind": "tool", "tool": "rag", "arguments": {"query": "{{e.city}}"}},
        )
        run = run_of(raw, provider_saying('{"city": "대전", "days": "3"}'))
        await drain(run)
        assert run._scope["e"] == {"city": "대전", "days": 3}
        assert seen == ["대전"]
        assert states_of(run)["s"] == "done"
    finally:
        monkeypatch.undo()


async def test_a_model_node_failure_is_one_failed_node_not_a_dead_run():
    provider = AsyncMock()
    provider.chat = AsyncMock(side_effect=RuntimeError("down"))
    run = run_of(graph(llm()), provider)
    await drain(run)
    assert states_of(run) == {"input": "done", "m": "failed", "answer": "done"}
    # 모델 출력을 못 읽는 경우도 같다
    run2 = run_of(graph(llm(output_fields=["a"])), provider_saying("not json"))
    await drain(run2)
    assert states_of(run2)["m"] == "failed"
