"""The execution plan, and the boundary that refuses one.

THE EXECUTOR IS THE BOUNDARY, not the planner's good intentions. The planner is
an LLM call, and the answer model on this deployment already reads Korean legal
double negatives backwards; assuming the planner also produces nonsense is not
pessimism, it is the design. So a plan is data until `validate_plan` has turned
every name in it into an object that was passed IN - a tool the planner invented
resolves to nothing and the plan is refused whole, never partly attempted.

Refused whole rather than step by step on purpose: a model that hallucinated one
tool name has told you what its other choices are worth, and the caller falls
back to the plain RAG path, which answers the question.
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.service import DEFAULT_AGENT, ResolvedAgent
from app.core.config import Settings
from app.mcp.client import MCPTarget
from app.models.collection import Collection
from app.models.mcp import McpServer, McpTool

StepKind = Literal["rag", "tool"]

# Every message here is logged and lands in messages.trace; none of them is
# handed to a user as-is, because a refused plan is a planner failure the user
# never asked about - they get an answer from the direct path instead. Korean
# regardless: the standing constraint has no "except when nobody reads it" case,
# and the trace screen is read by a person.
NOT_AN_OBJECT_MESSAGE = "실행 계획을 이해하지 못했습니다."
UNKNOWN_TOOL_MESSAGE = "등록되지 않은 도구를 지정한 계획입니다: {name}"
UNKNOWN_COLLECTION_MESSAGE = "이 질문에서 사용할 수 없는 컬렉션을 지정한 계획입니다: {name}"
TOO_MANY_STEPS_MESSAGE = "계획 단계가 상한({limit}개)을 넘었습니다."
TOO_MANY_TOOL_CALLS_MESSAGE = "도구 호출이 상한({limit}회)을 넘었습니다."
DUPLICATE_STEP_MESSAGE = "계획에 같은 단계 id가 두 번 나왔습니다: {name}"
UNKNOWN_DEPENDENCY_MESSAGE = "계획이 존재하지 않는 단계에 의존합니다: {name}"
CYCLIC_MESSAGE = "계획의 단계 의존 관계가 순환합니다."
EMPTY_QUERY_MESSAGE = "검색어가 없는 검색 단계가 있습니다."
UNKNOWN_KIND_MESSAGE = "알 수 없는 단계 종류입니다: {name}"


class PlanError(ValueError):
    """A plan that will not be run. The caller falls back to the direct RAG path;
    it is never raised at a user as an HTTP error."""


@dataclass(frozen=True)
class AvailableCollection:
    id: uuid.UUID
    name: str
    description: str | None = None


@dataclass(frozen=True)
class AvailableTool:
    """One tool the planner may name, already resolved to something callable.

    It carries the `MCPTarget` rather than a server id, so executing a step opens
    no database session - the same rule app/mcp/service.py:run_tool_calls was
    given a detached target for, and the reason that function takes no `db`.
    """

    id: uuid.UUID
    server_name: str
    tool_name: str
    description: str | None
    input_schema: dict
    risk_level: str
    target: MCPTarget

    @property
    def ref(self) -> str:
        """`server/tool`, which is what the planner names and what the citation
        ref already looks like. Both halves are unique by database constraint
        (uq_mcp_servers_name, uq_mcp_tools_server_name), so this identifies one
        row - which is why the planner is given names and not uuids: a model
        transcribing a uuid gets it wrong, and a model naming a tool it can see
        in the catalogue does not."""
        return f"{self.server_name}/{self.tool_name}"


@dataclass(frozen=True)
class AvailableResources:
    """Everything the planner is allowed to name. Anything else is refused."""

    collections: tuple[AvailableCollection, ...] = ()
    tools: tuple[AvailableTool, ...] = ()


async def load_available(
    db: AsyncSession,
    collection_ids: list[uuid.UUID] | None = None,
    agent: ResolvedAgent = DEFAULT_AGENT,
) -> AvailableResources:
    """What this question may reach.

    `collection_ids` is the scope the REQUEST asked for, and narrowing here is
    what makes "the planner may only name collections that were passed to it"
    true rather than aspirational: a user who scoped their question to one
    collection gets a plan that cannot search another, and `validate_plan`
    refuses one that tries. Slice 1's authorization model already says every
    authenticated user may READ every collection, so this is a scoping boundary
    on top of that, not a replacement for it.

    `agent` is the SECOND, stronger narrowing, and THIS IS THE PLACE IT CANNOT BE
    BYPASSED. An agent's allowed-collection and allowed-tool lists are permission
    boundaries: a tool the agent does not carry never enters the catalogue, so
    `validate_plan` cannot resolve its name and refuses the whole plan - the same
    treatment a hallucinated tool name gets, and for the same reason. Doing it in
    the router instead would make the boundary a habit; doing it here makes it a
    property of the only function that can produce a catalogue.

    The agent scope is re-applied here even though the caller has usually applied
    it already. That is deliberate and free - intersecting an already-intersected
    set changes nothing - and it is what stops a future caller that forgets from
    quietly widening the boundary. `scope_collections` raises AgentScopeError for
    a request that names a collection outside the agent, which the router turns
    into a Korean 400; it never silently empties the scope.

    Tools are exactly what `GET /api/mcp/tools` lists - enabled tools on enabled
    servers - so a tool an admin turned off is not merely un-runnable, it is
    invisible to the planner and unnameable in a plan.
    """
    scoped = agent.scope_collections(collection_ids)
    query = select(Collection).order_by(Collection.name)
    if scoped is not None:
        query = query.where(Collection.id.in_(scoped))
    collections = tuple(
        AvailableCollection(id=row.id, name=row.name, description=row.description)
        for row in (await db.scalars(query)).all()
    )
    tool_query = (
        select(McpTool, McpServer)
        .join(McpServer, McpServer.id == McpTool.server_id)
        .where(McpTool.enabled.is_(True), McpServer.enabled.is_(True))
        .order_by(McpServer.name, McpTool.name)
    )
    if agent.tool_ids:
        tool_query = tool_query.where(McpTool.id.in_(agent.tool_ids))
    rows = (await db.execute(tool_query)).all()
    tools = tuple(
        AvailableTool(
            id=tool.id,
            server_name=server.name,
            tool_name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema or {},
            risk_level=tool.risk_level,
            target=MCPTarget(name=server.name, base_url=server.base_url, auth_token=server.auth_token),
        )
        for tool, server in rows
    )
    return AvailableResources(collections=collections, tools=tools)


@dataclass(frozen=True)
class PlanStep:
    """One step. `depends_on` ORDERS execution and nothing more.

    Stated plainly because the alternative is a promise the code does not keep:
    no step consumes another step's output. Feeding one step's text into the
    next step's arguments would need a model call per step - a planner that
    loops, which is the bill this slice exists not to run up - and the answer
    model already sees every step's evidence at once. So a dependency means
    "run after", which is exactly what decides what may run concurrently.
    """

    id: str
    kind: StepKind
    label: str
    query: str = ""
    collection_ids: tuple[uuid.UUID, ...] = ()
    collection_names: tuple[str, ...] = ()
    tool: AvailableTool | None = None
    arguments: dict = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionPlan:
    steps: tuple[PlanStep, ...] = ()

    def waves(self) -> list[list[PlanStep]]:
        """Dependency levels, oldest first. Everything in one wave is
        independent of everything else in it, which is the whole reason
        `depends_on` exists: a wave is what runs concurrently.

        Safe to call unguarded - `validate_plan` has already rejected a cycle and
        an unknown dependency, so this terminates."""
        remaining = {step.id: step for step in self.steps}
        done: set[str] = set()
        levels: list[list[PlanStep]] = []
        while remaining:
            wave = [s for s in remaining.values() if set(s.depends_on) <= done]
            if not wave:
                raise PlanError(CYCLIC_MESSAGE)
            levels.append(wave)
            for step in wave:
                del remaining[step.id]
                done.add(step.id)
        return levels

    def to_raw(self) -> dict:
        """Back to the JSON shape the planner emitted, so a paused plan can be
        stored and re-validated on resume rather than trusted across requests.
        Re-validating is not belt-and-braces: between the pause and the approval
        an admin may have disabled the very tool that was waiting, and the
        resumed plan has to be refused the same way a fresh one would be."""
        return {
            "steps": [
                {
                    "id": step.id,
                    "kind": step.kind,
                    "query": step.query,
                    "collections": list(step.collection_names),
                    "tool": step.tool.ref if step.tool else None,
                    "arguments": step.arguments,
                    "depends_on": list(step.depends_on),
                }
                for step in self.steps
            ]
        }


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def validate_plan(raw: object, resources: AvailableResources, *, settings: Settings) -> ExecutionPlan:
    """Turn what the model said into steps that can only reach what was passed in.

    Refuses, in this order: a body that is not a plan, a step ceiling, a
    duplicate id, an unknown kind, an empty search, an unknown collection, an
    unknown tool, a tool-call ceiling, an unknown dependency, a cycle.
    """
    if not isinstance(raw, dict):
        raise PlanError(NOT_AN_OBJECT_MESSAGE)
    raw_steps = raw.get("steps")
    if raw_steps is None:
        raw_steps = []
    if not isinstance(raw_steps, list):
        raise PlanError(NOT_AN_OBJECT_MESSAGE)
    if len(raw_steps) > settings.orchestrator_max_steps:
        raise PlanError(TOO_MANY_STEPS_MESSAGE.format(limit=settings.orchestrator_max_steps))

    by_name = {c.name: c for c in resources.collections}
    by_ref = {t.ref: t for t in resources.tools}

    steps: list[PlanStep] = []
    seen: set[str] = set()
    tool_calls = 0
    for index, entry in enumerate(raw_steps, start=1):
        if not isinstance(entry, dict):
            raise PlanError(NOT_AN_OBJECT_MESSAGE)
        # A missing id is filled rather than refused: it is the one field the
        # model has no reason to be right about, and a plan of good steps must
        # not die of a bookkeeping detail. A DUPLICATE id is refused, because
        # that one silently collapses two steps into one dependency node.
        raw_id = entry.get("id")
        step_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else f"s{index}"
        if step_id in seen:
            raise PlanError(DUPLICATE_STEP_MESSAGE.format(name=step_id[:50]))
        seen.add(step_id)
        kind = entry.get("kind")
        depends_on = tuple(_as_str_list(entry.get("depends_on")))

        if kind == "rag":
            query = entry.get("query")
            if not isinstance(query, str) or not query.strip():
                raise PlanError(EMPTY_QUERY_MESSAGE)
            names = _as_str_list(entry.get("collections"))
            for name in names:
                if name not in by_name:
                    raise PlanError(UNKNOWN_COLLECTION_MESSAGE.format(name=name[:100]))
            # NO NAMES MEANS THE WHOLE CATALOGUE, WRITTEN OUT. It used to mean an
            # empty tuple that the executor turned back into `collection_ids=None`
            # - every collection in the database, whatever the catalogue held -
            # and that was the one way an agent's collection restriction could be
            # walked around: a planner that simply omitted "collections" searched
            # outside the agent. `resources.collections` is already narrowed to
            # what this question may reach, so resolving the default here closes
            # it in the same place every other name is resolved.
            chosen = [by_name[name] for name in names] if names else list(resources.collections)
            steps.append(
                PlanStep(
                    id=step_id,
                    kind="rag",
                    # Derived here, never taken from the model: this string is
                    # rendered on screen, and a label the planner wrote would be
                    # third-party-influenced text in the UI for no benefit.
                    label=f"문서 검색: {query.strip()[:60]}",
                    query=query.strip(),
                    # ALWAYS a closed set of ids, never empty-meaning-everything.
                    # It is empty only when the catalogue itself is, which is a
                    # corpus with nothing in it to find.
                    collection_ids=tuple(c.id for c in chosen),
                    collection_names=tuple(c.name for c in chosen),
                    depends_on=depends_on,
                )
            )
        elif kind == "tool":
            name = entry.get("tool")
            if not isinstance(name, str) or name not in by_ref:
                raise PlanError(UNKNOWN_TOOL_MESSAGE.format(name=str(name)[:100]))
            tool_calls += 1
            if tool_calls > settings.orchestrator_max_tool_calls:
                raise PlanError(
                    TOO_MANY_TOOL_CALLS_MESSAGE.format(limit=settings.orchestrator_max_tool_calls)
                )
            arguments = entry.get("arguments")
            steps.append(
                PlanStep(
                    id=step_id,
                    kind="tool",
                    label=f"도구 호출: {by_ref[name].ref}",
                    tool=by_ref[name],
                    # Not validated against input_schema: the MCP server owns
                    # that schema and answers a bad argument set with a JSON-RPC
                    # error, which becomes evidence saying the call failed. Same
                    # rule as the manual path in app/schemas/chat.py.
                    arguments=arguments if isinstance(arguments, dict) else {},
                    depends_on=depends_on,
                )
            )
        else:
            raise PlanError(UNKNOWN_KIND_MESSAGE.format(name=str(kind)[:50]))

    ids = {step.id for step in steps}
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in ids:
                raise PlanError(UNKNOWN_DEPENDENCY_MESSAGE.format(name=dependency[:50]))
            if dependency == step.id:
                raise PlanError(CYCLIC_MESSAGE)

    plan = ExecutionPlan(steps=tuple(steps))
    # Cycle detection by construction: waves() cannot make progress on one, and
    # doing it HERE rather than in the executor is what lets the executor call
    # waves() without a guard.
    plan.waves()
    return plan
