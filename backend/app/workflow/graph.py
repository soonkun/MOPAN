"""The workflow graph, and the boundary that refuses one.

THE VALIDATOR IS THE BOUNDARY, not the author's good intentions - and there are
two authors. A person draws a graph on the canvas; 슈퍼 에이전트 has a model write
one per question. **Both come through this function**, which is what makes the
fifth acceptance criterion of the design ("두 경로가 갈라지면 이 설계의 요점이
사라진다") a property of the code rather than a promise. A graph is data until
`validate_graph` has turned every name in it into an object that was passed IN.

A graph is refused WHOLE, never partly attempted. For the planner that is the old
rule restated: a model that hallucinated one tool name has told you what its
other choices are worth, and the caller falls back to the plain RAG path. For a
person it is the only honest answer to a save: a half-saved graph is a graph
whose picture and behaviour disagree.

WHAT IS CHECKED AT SAVE, and therefore never has to be caught at run:

- every node kind is one of the four, and there is exactly one `input` and one
  `answer` (the design: without those two it cannot be executed, and letting them
  be deleted produces a graph that saves and will not run)
- every `tool` resolves against the catalogue - which is already narrowed to this
  workflow's allow-lists, so **a graph naming a tool outside the allowed list is
  refused at save**, criterion 4
- the node ceiling and the tool-call ceiling
- every edge names nodes that exist, and **the edges contain no cycle**
- a `workflow` node does not lead back here, transitively, through the graphs of
  the workflows it calls
- every `{{...}}` reference names a node that can actually precede this one, and
  is a whole reference rather than a template (see expr.py)
- branch conditions are shapes the evaluator understands, and `kind: "llm"` is
  refused: it is in the schema and is not switched on

The depth limit is the one bound that CANNOT live here - a graph two levels deep
is legal, and only a run knows how deep it already is - so it is counted in the
executor. Cycles get both: refused statically here, and the depth counter catches
anything that reaches a run regardless (a graph edited in the database, a
workflow whose callee changed after this one was saved).
"""

import uuid
from dataclasses import dataclass, field

from app.core.config import Settings
from app.workflow.catalogue import (
    AvailableResources,
    AvailableTool,
    AvailableWorkflow,
    workflow_risk_level,
)
from app.workflow.expr import ExpressionError, check_condition, references_in, template_references

NODE_KINDS = ("input", "tool", "branch", "answer", "llm", "classify", "extract", "template")
# 모델을 부르는 노드. 도구 노드처럼 시간과 돈을 쓰므로 웨이브(병렬)로 돌고 호출 상한을 센다.
MODEL_NODE_KINDS = ("llm", "classify", "extract")
MAX_CATEGORIES = 12
MAX_EXTRACT_FIELDS = 12
MAX_PROMPT_CHARS = 20_000
INPUT_NODE_KIND = "input"
ANSWER_NODE_KIND = "answer"

# Every message here can reach a person: an admin saving a graph gets it as a
# Korean 400, and a planner refusal lands in messages.trace, which the trace
# screen renders. Korean regardless of the reader, per the standing constraint.
NOT_AN_OBJECT_MESSAGE = "워크플로우 그래프를 이해하지 못했습니다."
TOO_MANY_NODES_MESSAGE = "노드가 상한({limit}개)을 넘었습니다."
TOO_MANY_TOOL_CALLS_MESSAGE = "도구 호출이 상한({limit}회)을 넘었습니다."
DUPLICATE_NODE_MESSAGE = "그래프에 같은 노드 id가 두 번 나왔습니다: {name}"
UNKNOWN_NODE_KIND_MESSAGE = "알 수 없는 노드 종류입니다: {name}"
BAD_NODE_ID_MESSAGE = "노드 id가 올바르지 않습니다: {name}"
MISSING_INPUT_MESSAGE = "질문(input) 노드가 있어야 합니다. 그래프당 하나이며 지울 수 없습니다."
MISSING_ANSWER_MESSAGE = "답변(answer) 노드가 있어야 합니다. 그래프당 하나이며 지울 수 없습니다."
DUPLICATE_INPUT_MESSAGE = "질문(input) 노드는 그래프당 하나여야 합니다."
DUPLICATE_ANSWER_MESSAGE = "답변(answer) 노드는 그래프당 하나여야 합니다."
UNKNOWN_TOOL_MESSAGE = "등록되지 않은 도구를 지정한 그래프입니다: {name}"
UNKNOWN_COLLECTION_MESSAGE = "이 워크플로우가 사용할 수 없는 분류를 지정했습니다: {name}"
UNKNOWN_WORKFLOW_MESSAGE = "등록되지 않은 워크플로우를 지정했습니다: {name}"
UNKNOWN_EDGE_NODE_MESSAGE = "존재하지 않는 노드를 잇는 간선이 있습니다: {name}"
SELF_EDGE_MESSAGE = "노드가 자기 자신을 가리키는 간선이 있습니다: {name}"
CYCLIC_MESSAGE = "그래프의 간선이 순환합니다."
WORKFLOW_CYCLE_MESSAGE = "워크플로우가 자기 자신을 다시 부릅니다: {name}"
FORWARD_REFERENCE_MESSAGE = "앞서 실행되지 않는 노드를 참조합니다: {name}"
EMPTY_QUERY_MESSAGE = "검색어가 없는 검색 노드가 있습니다."
MISSING_CONDITION_MESSAGE = "조건이 없는 분기 노드가 있습니다: {name}"
BRANCH_EDGE_MESSAGE = "분기 노드의 간선에는 참/거짓을 지정해야 합니다: {name}"
NON_BRANCH_WHEN_MESSAGE = "분기 노드가 아닌 곳의 간선에는 참/거짓을 지정할 수 없습니다: {name}"
EMPTY_PROMPT_MESSAGE = "프롬프트가 비어 있는 모델 호출 노드가 있습니다: {name}"
EMPTY_TEMPLATE_MESSAGE = "본문이 비어 있는 텍스트 조합 노드가 있습니다: {name}"
BAD_CATEGORIES_MESSAGE = "질문 분류 노드에는 서로 다른 갈래가 2~{limit}개 있어야 합니다: {name}"
BAD_FIELDS_MESSAGE = "정보 추출 노드에는 이름이 올바른 항목이 1~{limit}개 있어야 합니다: {name}"
CLASSIFY_EDGE_MESSAGE = "질문 분류 노드의 간선에는 갈래 이름을 지정해야 합니다: {name}"
UNKNOWN_CATEGORY_EDGE_MESSAGE = "질문 분류 노드에 없는 갈래를 잇는 간선이 있습니다: {name}"
TOO_LONG_PROMPT_MESSAGE = "프롬프트가 너무 깁니다(최대 {limit}자): {name}"

_ID_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class GraphError(ValueError):
    """A graph that will not be run.

    On the save path the router turns it into a Korean 400. On the planner path
    the caller records it in the trace and falls back to the direct RAG path; it
    is never raised at a user as an HTTP error there.
    """


def _valid_id(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        return False
    return all(ch in _ID_OK or "가" <= ch <= "힣" for ch in value)


@dataclass(frozen=True)
class Node:
    """One node. Coordinates ride ALONG, deliberately.

    `x`/`y` are stored because a person arranged them and reopening the canvas
    has to show the same picture. They are the one part of a node the executor
    reads nothing from - which is exactly why they belong on the node rather than
    in a parallel layout blob that could drift out of step with it.
    """

    id: str
    kind: str
    label: str = ""
    x: float = 0.0
    y: float = 0.0
    # kind == "tool". Exactly one of these three is set.
    rag_collection_ids: tuple[uuid.UUID, ...] = ()
    rag_collection_names: tuple[str, ...] = ()
    tool: AvailableTool | None = None
    workflow: AvailableWorkflow | None = None
    arguments: dict = field(default_factory=dict)
    # kind == "branch"
    condition: dict | None = None
    # kind in llm/classify/extract/template. 종류별 키:
    #   llm:      prompt(템플릿), system, model, output_fields[list[str]], as_evidence[bool]
    #   classify: text(템플릿, 기본 {{input.text}}), categories[list[{id,label,description}]], model
    #   extract:  text(템플릿), fields[list[{name,description,type}]], model
    #   template: text(템플릿)
    config: dict = field(default_factory=dict)

    @property
    def tool_ref(self) -> str | None:
        """What the node named, back in the flat namespace it was written in."""
        if self.kind != "tool":
            return None
        if self.tool is not None:
            return f"mcp:{self.tool.ref}"
        if self.workflow is not None:
            return f"workflow:{self.workflow.name}"
        return "rag"

    @property
    def risk_level(self) -> str | None:
        if self.tool is not None:
            return self.tool.risk_level
        if self.workflow is not None:
            # Inherited: a workflow that wraps a destructive tool must not look
            # safe. Computed here rather than stored so it cannot go stale when
            # an admin reclassifies the tool underneath it.
            return workflow_risk_level(self.workflow)
        if self.kind == "tool":
            return "read"  # RAG is a read of this deployment's own corpus.
        return None


@dataclass(frozen=True)
class Edge:
    """An edge ORDERS execution AND carries data.

    That is the one thing `PlanStep.depends_on` deliberately did not do, and the
    difference this slice exists to make: a node reads an earlier node's result
    through `{{...}}`, and this edge is what says "earlier".

    `when` is set only on an edge leaving a `branch`, and is "true" or "false".
    """

    source: str
    target: str
    when: str | None = None


@dataclass(frozen=True)
class WorkflowGraph:
    nodes: tuple[Node, ...] = ()
    edges: tuple[Edge, ...] = ()

    def by_id(self) -> dict[str, Node]:
        """Nodes by id. Not used by the executor - which walks `self.nodes` - but
        it is how every reader of a validated graph asks "what did node X become",
        which is what a test of the validator is for."""
        return {node.id: node for node in self.nodes}

    def incoming(self, node_id: str) -> list[Edge]:
        return [edge for edge in self.edges if edge.target == node_id]

    def tool_nodes(self) -> list[Node]:
        return [node for node in self.nodes if node.kind == "tool"]

    def order(self) -> list[str]:
        """Topological order. Safe to call unguarded: `validate_graph` has already
        refused a cycle, so this terminates."""
        remaining = {node.id: {edge.source for edge in self.incoming(node.id)} for node in self.nodes}
        done: list[str] = []
        while remaining:
            ready = [nid for nid, deps in remaining.items() if not deps - set(done)]
            if not ready:
                raise GraphError(CYCLIC_MESSAGE)
            done.extend(sorted(ready))
            for nid in ready:
                del remaining[nid]
        return done

    def to_raw(self) -> dict:
        """Back to the JSON shape it was authored in - names, never resolved
        objects - so a paused run can be stored in Redis and re-validated on
        resume rather than trusted across requests. Re-validating is not
        belt-and-braces: between the pause and the approval an admin may have
        disabled the very tool that was waiting."""
        return {
            "nodes": [
                {
                    "id": node.id,
                    "kind": node.kind,
                    "label": node.label,
                    "x": node.x,
                    "y": node.y,
                    **(
                        {
                            "tool": node.tool_ref,
                            "collections": list(node.rag_collection_names),
                            "arguments": node.arguments,
                        }
                        if node.kind == "tool"
                        else {}
                    ),
                    **({"condition": node.condition} if node.kind == "branch" else {}),
                    **({"config": node.config} if node.kind in (*MODEL_NODE_KINDS, "template") else {}),
                }
                for node in self.nodes
            ],
            "edges": [
                {"from": edge.source, "to": edge.target, **({"when": edge.when} if edge.when else {})}
                for edge in self.edges
            ],
        }


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _check_workflow_cycles(
    workflow: AvailableWorkflow, resources: AvailableResources, seen: frozenset[uuid.UUID]
) -> None:
    """Walk `workflow:` refs transitively and refuse a return to `seen`.

    Static, at save time, and paired with the executor's depth counter rather
    than replacing it: this can only see the graphs that exist NOW, and a callee
    edited afterwards would make a cycle nobody re-checked. Refused statically
    AND counted at run, which is what the design asked for.
    """
    if workflow.id in seen:
        raise GraphError(WORKFLOW_CYCLE_MESSAGE.format(name=workflow.name[:100]))
    by_name = {w.name: w for w in resources.workflows}
    nodes = workflow.graph.get("nodes") if isinstance(workflow.graph, dict) else None
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or node.get("kind") != "tool":
            continue
        ref = node.get("tool")
        if not isinstance(ref, str) or not ref.startswith("workflow:"):
            continue
        callee = by_name.get(ref[len("workflow:") :])
        if callee is not None:
            _check_workflow_cycles(callee, resources, seen | {workflow.id})


def validate_graph(
    raw: object,
    resources: AvailableResources,
    *,
    settings: Settings,
    self_id: uuid.UUID | None = None,
) -> WorkflowGraph:
    """Turn what was authored into a graph that can only reach what was passed in.

    `self_id` is the workflow being SAVED, when there is one. It is what makes
    `A -> B -> A` refusable at save: without it the walk cannot know which
    workflow the graph under validation belongs to. The planner passes None - a
    graph the model just wrote is not a saved workflow and cannot be its own
    ancestor.
    """
    if not isinstance(raw, dict):
        raise GraphError(NOT_AN_OBJECT_MESSAGE)
    raw_nodes = raw.get("nodes")
    raw_edges = raw.get("edges") or []
    if raw_nodes is None:
        raw_nodes = []
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise GraphError(NOT_AN_OBJECT_MESSAGE)
    if len(raw_nodes) > settings.workflow_max_nodes:
        raise GraphError(TOO_MANY_NODES_MESSAGE.format(limit=settings.workflow_max_nodes))

    by_collection_name = {c.name: c for c in resources.collections}
    by_tool_ref = {t.ref: t for t in resources.tools}
    by_workflow_name = {w.name: w for w in resources.workflows}

    nodes: list[Node] = []
    seen_ids: set[str] = set()
    tool_calls = 0
    for index, entry in enumerate(raw_nodes, start=1):
        if not isinstance(entry, dict):
            raise GraphError(NOT_AN_OBJECT_MESSAGE)
        # A missing id is filled rather than refused - it is the one field a model
        # has no reason to be right about, and a graph of good nodes must not die
        # of a bookkeeping detail. A DUPLICATE id IS refused: that one silently
        # collapses two nodes into one, and edges would then point at both.
        raw_id = entry.get("id")
        node_id = raw_id if _valid_id(raw_id) else f"n{index}"
        if raw_id is not None and not _valid_id(raw_id):
            raise GraphError(BAD_NODE_ID_MESSAGE.format(name=str(raw_id)[:50]))
        if node_id in seen_ids:
            raise GraphError(DUPLICATE_NODE_MESSAGE.format(name=node_id[:50]))
        seen_ids.add(node_id)

        kind = entry.get("kind")
        if kind not in NODE_KINDS:
            raise GraphError(UNKNOWN_NODE_KIND_MESSAGE.format(name=str(kind)[:50]))
        try:
            x = float(entry.get("x") or 0)
            y = float(entry.get("y") or 0)
        except (TypeError, ValueError):
            x = y = 0.0
        # Never taken from the model when it can be derived: a label is rendered
        # on screen, and one the planner wrote would be third-party-influenced
        # text in the UI. A PERSON's label is kept - they typed it - and capped.
        raw_label = entry.get("label")
        label = raw_label.strip()[:120] if isinstance(raw_label, str) and raw_label.strip() else ""

        if kind == "tool":
            ref = entry.get("tool")
            arguments = entry.get("arguments")
            arguments = arguments if isinstance(arguments, dict) else {}
            # References are checked for SHAPE here (whole reference, not a
            # template) and for REACHABILITY below, once every node id is known.
            try:
                references_in(arguments)
            except ExpressionError as exc:
                raise GraphError(str(exc)) from exc

            tool_calls += 1
            if tool_calls > settings.orchestrator_max_tool_calls:
                raise GraphError(
                    TOO_MANY_TOOL_CALLS_MESSAGE.format(limit=settings.orchestrator_max_tool_calls)
                )

            if ref == "rag" or ref is None:
                names = _as_str_list(entry.get("collections"))
                for name in names:
                    if name not in by_collection_name:
                        raise GraphError(UNKNOWN_COLLECTION_MESSAGE.format(name=name[:100]))
                # NO NAMES MEANS THE WHOLE CATALOGUE, WRITTEN OUT. It must never
                # mean an empty tuple that the executor turns back into
                # `collection_ids=None` - every collection in the database,
                # whatever the catalogue held - because that is the one way a
                # workflow's collection restriction could be walked around.
                # `resources.collections` is already narrowed, so resolving the
                # default here closes it where every other name is resolved.
                chosen = [by_collection_name[n] for n in names] if names else list(resources.collections)
                query = arguments.get("query")
                if not isinstance(query, str) or not query.strip():
                    raise GraphError(EMPTY_QUERY_MESSAGE)
                nodes.append(
                    Node(
                        id=node_id,
                        kind="tool",
                        label=label or f"문서 검색: {', '.join(c.name for c in chosen)[:60]}",
                        x=x,
                        y=y,
                        rag_collection_ids=tuple(c.id for c in chosen),
                        rag_collection_names=tuple(c.name for c in chosen),
                        arguments=arguments,
                    )
                )
            elif isinstance(ref, str) and ref.startswith("mcp:"):
                name = ref[len("mcp:") :]
                if name not in by_tool_ref:
                    raise GraphError(UNKNOWN_TOOL_MESSAGE.format(name=name[:100]))
                nodes.append(
                    Node(
                        id=node_id,
                        kind="tool",
                        label=label or f"도구 호출: {name}",
                        x=x,
                        y=y,
                        tool=by_tool_ref[name],
                        # Not validated against input_schema: the MCP server owns
                        # that schema and answers a bad argument set with a
                        # JSON-RPC error, which becomes evidence saying the call
                        # failed. Same rule the manual path follows.
                        arguments=arguments,
                    )
                )
            elif isinstance(ref, str) and ref.startswith("workflow:"):
                name = ref[len("workflow:") :]
                callee = by_workflow_name.get(name)
                if callee is None:
                    raise GraphError(UNKNOWN_WORKFLOW_MESSAGE.format(name=name[:100]))
                _check_workflow_cycles(callee, resources, frozenset({self_id} if self_id else ()))
                nodes.append(
                    Node(
                        id=node_id,
                        kind="tool",
                        label=label or f"워크플로우: {name}",
                        x=x,
                        y=y,
                        workflow=callee,
                        arguments=arguments,
                    )
                )
            else:
                raise GraphError(UNKNOWN_TOOL_MESSAGE.format(name=str(ref)[:100]))
        elif kind == "branch":
            condition = entry.get("condition")
            if condition is None:
                raise GraphError(MISSING_CONDITION_MESSAGE.format(name=node_id[:50]))
            try:
                check_condition(condition)
            except ExpressionError as exc:
                raise GraphError(str(exc)) from exc
            nodes.append(
                Node(id=node_id, kind="branch", label=label or "분기", x=x, y=y, condition=condition)
            )
        elif kind in (*MODEL_NODE_KINDS, "template"):
            config = _parse_config(kind, node_id, entry.get("config"))
            nodes.append(Node(id=node_id, kind=kind, label=label or CONFIG_KIND_LABEL[kind], x=x, y=y, config=config))
        else:
            nodes.append(
                Node(
                    id=node_id,
                    kind=kind,
                    label=label or ("질문" if kind == INPUT_NODE_KIND else "답변"),
                    x=x,
                    y=y,
                )
            )

    inputs = [n for n in nodes if n.kind == INPUT_NODE_KIND]
    answers = [n for n in nodes if n.kind == ANSWER_NODE_KIND]
    if len(inputs) > 1:
        raise GraphError(DUPLICATE_INPUT_MESSAGE)
    if len(answers) > 1:
        raise GraphError(DUPLICATE_ANSWER_MESSAGE)
    if not inputs:
        raise GraphError(MISSING_INPUT_MESSAGE)
    if not answers:
        raise GraphError(MISSING_ANSWER_MESSAGE)

    ids = {node.id for node in nodes}
    branch_ids = {node.id for node in nodes if node.kind == "branch"}
    # 질문 분류 노드의 간선은 갈래 id를 `when`으로 갖는다 - 분기의 참/거짓과 같은 자리.
    categories_of = {
        node.id: {c["id"] for c in node.config.get("categories", [])} for node in nodes if node.kind == "classify"
    }
    edges: list[Edge] = []
    for entry in raw_edges:
        if not isinstance(entry, dict):
            raise GraphError(NOT_AN_OBJECT_MESSAGE)
        source, target = entry.get("from"), entry.get("to")
        for endpoint in (source, target):
            if endpoint not in ids:
                raise GraphError(UNKNOWN_EDGE_NODE_MESSAGE.format(name=str(endpoint)[:50]))
        if source == target:
            raise GraphError(SELF_EDGE_MESSAGE.format(name=str(source)[:50]))
        when = entry.get("when")
        if source in categories_of:
            if not isinstance(when, str) or not when:
                raise GraphError(CLASSIFY_EDGE_MESSAGE.format(name=str(source)[:50]))
            if when not in categories_of[source]:
                raise GraphError(UNKNOWN_CATEGORY_EDGE_MESSAGE.format(name=f"{source}:{when}"[:50]))
            edges.append(Edge(source=source, target=target, when=when))
            continue
        if when is not None:
            when = "true" if when in (True, "true") else "false" if when in (False, "false") else None
            if when is None:
                raise GraphError(BRANCH_EDGE_MESSAGE.format(name=str(source)[:50]))
        if source in branch_ids and when is None:
            raise GraphError(BRANCH_EDGE_MESSAGE.format(name=str(source)[:50]))
        if source not in branch_ids and when is not None:
            raise GraphError(NON_BRANCH_WHEN_MESSAGE.format(name=str(source)[:50]))
        edges.append(Edge(source=source, target=target, when=when))

    graph = WorkflowGraph(nodes=tuple(nodes), edges=tuple(edges))
    # Cycle detection by construction: `order()` cannot make progress on one, and
    # doing it HERE rather than in the executor is what lets the executor iterate
    # without a guard.
    order = graph.order()
    position = {node_id: index for index, node_id in enumerate(order)}

    # Every `{{a.b}}` must name a node that is EARLIER in the order. Checked here
    # rather than at run because a forward reference is a graph that would fail
    # the same way on every question - the definition of something to catch at
    # save. `input` is always position 0, so `{{input.text}}` is always legal.
    for node in nodes:
        for reference in references_in(node.arguments):
            head = reference.segments[0]
            if head not in ids or position[head] >= position[node.id]:
                raise GraphError(FORWARD_REFERENCE_MESSAGE.format(name=reference.raw[:100]))
    for node in nodes:
        if node.kind != "branch" or node.condition is None:
            continue
        for reference in _condition_references(node.condition):
            head = reference.segments[0]
            if head not in ids or position[head] >= position[node.id]:
                raise GraphError(FORWARD_REFERENCE_MESSAGE.format(name=reference.raw[:100]))
    for node in nodes:
        for reference in config_references(node):
            head = reference.segments[0]
            if head not in ids or position[head] >= position[node.id]:
                raise GraphError(FORWARD_REFERENCE_MESSAGE.format(name=reference.raw[:100]))
    return graph


CONFIG_KIND_LABEL = {"llm": "모델 호출", "classify": "질문 분류", "extract": "정보 추출", "template": "텍스트 조합"}
_TEMPLATE_KEYS = {"llm": ("prompt", "system"), "classify": ("text",), "extract": ("text",), "template": ("text",)}


def config_references(node: Node) -> list:
    """모델·텍스트 노드의 본문(템플릿)이 참조하는 노드들. 저장 시 도달 가능성 검사용."""
    found = []
    for key in _TEMPLATE_KEYS.get(node.kind, ()):
        found.extend(template_references(node.config.get(key)))
    return found


def _parse_config(kind: str, node_id: str, raw: object) -> dict:
    """종류별 설정을 검사해 정돈한 dict로. 모르는 키는 버린다 - 저장된 그래프는 코드보다 오래 산다."""
    raw = raw if isinstance(raw, dict) else {}
    name = node_id[:50]

    def text_of(key: str, *, required: bool, default: str = "") -> str:
        value = raw.get(key)
        value = value.strip() if isinstance(value, str) else ""
        if not value:
            value = default
        if len(value) > MAX_PROMPT_CHARS:
            raise GraphError(TOO_LONG_PROMPT_MESSAGE.format(limit=MAX_PROMPT_CHARS, name=name))
        try:
            template_references(value)
        except ExpressionError as exc:
            raise GraphError(str(exc)) from exc
        if required and not value:
            raise GraphError((EMPTY_PROMPT_MESSAGE if kind == "llm" else EMPTY_TEMPLATE_MESSAGE).format(name=name))
        return value

    model = raw.get("model")
    model = model.strip()[:100] if isinstance(model, str) and model.strip() else None
    if kind == "template":
        return {"text": text_of("text", required=True)}
    if kind == "llm":
        fields = [f for f in _as_str_list(raw.get("output_fields")) if SEGMENT_RE_OK(f)][:MAX_EXTRACT_FIELDS]
        return {
            "prompt": text_of("prompt", required=True),
            "system": text_of("system", required=False),
            "model": model,
            "output_fields": fields,
            "as_evidence": bool(raw.get("as_evidence")),
        }
    if kind == "classify":
        categories = []
        seen: set[str] = set()
        for item in raw.get("categories") if isinstance(raw.get("categories"), list) else []:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            if not isinstance(cid, str) or not SEGMENT_RE_OK(cid) or cid in seen:
                continue
            seen.add(cid)
            label = item.get("label") if isinstance(item.get("label"), str) else ""
            description = item.get("description") if isinstance(item.get("description"), str) else ""
            categories.append({"id": cid[:40], "label": label.strip()[:80] or cid, "description": description.strip()[:300]})
        if not 2 <= len(categories) <= MAX_CATEGORIES:
            raise GraphError(BAD_CATEGORIES_MESSAGE.format(limit=MAX_CATEGORIES, name=name))
        return {"text": text_of("text", required=False, default="{{input.text}}"), "categories": categories, "model": model}
    # extract
    fields = []
    seen_names: set[str] = set()
    for item in raw.get("fields") if isinstance(raw.get("fields"), list) else []:
        if not isinstance(item, dict):
            continue
        fname = item.get("name")
        if not isinstance(fname, str) or not SEGMENT_RE_OK(fname) or fname in seen_names:
            continue
        seen_names.add(fname)
        ftype = item.get("type") if item.get("type") in ("string", "number", "boolean") else "string"
        description = item.get("description") if isinstance(item.get("description"), str) else ""
        fields.append({"name": fname[:40], "type": ftype, "description": description.strip()[:300]})
    if not 1 <= len(fields) <= MAX_EXTRACT_FIELDS:
        raise GraphError(BAD_FIELDS_MESSAGE.format(limit=MAX_EXTRACT_FIELDS, name=name))
    return {"text": text_of("text", required=False, default="{{input.text}}"), "fields": fields, "model": model}


def SEGMENT_RE_OK(value: str) -> bool:
    from app.workflow.expr import SEGMENT_RE

    return bool(value) and len(value) <= 40 and SEGMENT_RE.match(value) is not None


def _condition_references(condition: object) -> list:
    if not isinstance(condition, dict):
        return []
    found = []
    for key in ("left", "right", "of"):
        value = condition.get(key)
        if isinstance(value, dict) or isinstance(value, list) and value and isinstance(value[0], dict):
            for part in value if isinstance(value, list) else [value]:
                found.extend(_condition_references(part))
        else:
            found.extend(references_in(value))
    return found
