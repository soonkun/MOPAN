"""What a question may reach, and the boundary that decides it.

This file is the old `app/agents/service.py` and the old
`app/orchestrator/plan.py:load_available` merged, because after Slice 6 they were
answering the same question: **what is callable here.** A 워크플로우 that a person
drew and a 슈퍼 에이전트 graph the model wrote are both validated against the same
`AvailableResources`, which is what makes the fifth acceptance criterion - one
executor, one boundary - true rather than aspirational.

THE TWO LISTS ARE PERMISSION BOUNDARIES, NOT HINTS. An admin who reads
"이 워크플로우는 A 분류만 사용" on a screen has been told something, and the only way
that sentence is true is if the restriction lives where nothing routes around it.
So it lives here, and the functions that decide what a question may reach -
`load_available` and `app/chat/service.py:retrieve` - both apply it themselves
rather than trusting a router to have narrowed first.

EMPTY MEANS UNRESTRICTED, for both lists. That is what makes `DEFAULT_WORKFLOW` -
used when a request names none - identical to the app as it behaved before any of
this existed, and it is why an empty `workflows` table changes nothing.

NESTING DOES NOT WIDEN THE BOUNDARY. A `workflow` node runs a saved workflow, and
that callee has an allow-list of its own. `AvailableResources.narrow` intersects
the two, so a workflow restricted to 분류 A cannot reach 분류 B by calling a
workflow that carries B. That intersection is why there is deliberately no third
"허용 워크플로우" join table: an allow-list of NAMES would say which boundaries you
may delegate to, and the intersection says what can actually be reached, which is
the property anybody was after.
"""

import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.client import MCPTarget
from app.models.collection import Collection
from app.models.mcp import RISK_LEVELS, McpServer, McpTool
from app.models.workflow import Workflow, WorkflowVersion

WORKFLOW_NOT_FOUND_MESSAGE = "워크플로우를 찾을 수 없습니다."
WORKFLOW_DISABLED_MESSAGE = "사용이 중지된 워크플로우입니다. 관리자에게 문의해 주세요."
COLLECTION_NOT_ALLOWED_MESSAGE = "이 워크플로우가 사용할 수 없는 분류입니다."
TOOL_NOT_ALLOWED_MESSAGE = "이 워크플로우가 사용할 수 없는 도구입니다."

# The prompt a workflow answers with unless it names another. Matches
# app/chat/service.py:answer's own default, which is what makes the default
# workflow a no-op rather than a second code path.
DEFAULT_PROMPT_NAME = "answer_agent"


class WorkflowScopeError(ValueError):
    """The request asked for something outside this workflow's boundary.

    A ValueError rather than an HTTPException so the boundary object stays usable
    off the request path (the executor, the tests, a future worker). The router
    turns it into a 400 with this message, which is already Korean.
    """


@dataclass(frozen=True)
class AvailableCollection:
    id: uuid.UUID
    name: str
    description: str | None = None


@dataclass(frozen=True)
class AvailableTool:
    """One MCP tool a graph may name, already resolved to something callable.

    It carries the `MCPTarget` rather than a server id, so executing a node opens
    no database session - the same rule `app/mcp/service.py:run_tool_calls` was
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
        """`server/tool`, which is what a graph names and what the citation ref
        already looks like. Both halves are unique by database constraint
        (uq_mcp_servers_name, uq_mcp_tools_server_name), so this identifies one
        row - which is why a graph carries names and not uuids: a model
        transcribing a uuid gets it wrong, and a name is what a person reads on a
        canvas."""
        return f"{self.server_name}/{self.tool_name}"


def graph_risk_level(graph: object, risk_by_ref: dict[str, str]) -> str:
    """A WorkflowTool inherits the MAXIMUM risk of what its graph calls.

    Computed from the stored graph and a live `server/tool -> risk_level` map
    rather than from a column, so an admin reclassifying an MCP tool from `write`
    to `destructive` re-gates every workflow that calls it without anybody
    re-saving anything. **A workflow that wraps a destructive tool must not look
    safe**, and a column would go stale the moment the classification moved.

    A `mcp:` ref the map does not answer for is treated as the WORST case, not the
    cheapest: the tool is named in the graph, something is stopping us reading its
    row, and `needs_approval` already applies the same rule to an unknown level.

    A nested `workflow:` ref contributes nothing here. It cannot be resolved from
    a plain dict, and it does not need to be: the callee's own nodes are gated
    when the callee runs, and the depth limit bounds how far that goes.

    It lives in this module rather than beside `validate_graph` only because
    `graph.py` imports FROM here; putting it there would be a cycle.
    """
    worst = 0
    nodes = graph.get("nodes") if isinstance(graph, dict) else None
    for node in nodes if isinstance(nodes, list) else []:
        if not isinstance(node, dict) or node.get("kind") != "tool":
            continue
        ref = node.get("tool")
        if not isinstance(ref, str) or not ref.startswith("mcp:"):
            continue  # `rag` is a read of this deployment's own corpus.
        level = risk_by_ref.get(ref[len("mcp:") :])
        worst = max(worst, RISK_LEVELS.index(level) if level in RISK_LEVELS else len(RISK_LEVELS) - 1)
    return RISK_LEVELS[worst]


@dataclass(frozen=True)
class AvailableWorkflow:
    """A saved workflow that another graph - or the planner - may call.

    The graph travels WITH it, detached from the session, for the same reason
    `AvailableTool` carries its target: a `workflow` node runs with nothing open.
    """

    id: uuid.UUID
    name: str
    description: str | None
    version: int
    graph: dict
    # THE INHERITED MAXIMUM of what this graph calls, computed by `load_available`
    # against the FULL tool table rather than the narrowed catalogue: a workflow
    # that wraps a destructive tool must not look safe just because the caller
    # cannot reach that tool directly.
    risk_level: str = "read"
    collection_ids: frozenset[uuid.UUID] = frozenset()
    tool_ids: frozenset[uuid.UUID] = frozenset()


def workflow_risk_level(workflow: "AvailableWorkflow") -> str:
    """The inherited maximum for a resolved catalogue entry. `load_available`
    already computed it against the FULL tool table, so this is a read."""
    return workflow.risk_level


@dataclass(frozen=True)
class AvailableResources:
    """Everything a graph is allowed to name. Anything else is refused."""

    collections: tuple[AvailableCollection, ...] = ()
    tools: tuple[AvailableTool, ...] = ()
    workflows: tuple[AvailableWorkflow, ...] = ()

    def narrow(self, workflow: AvailableWorkflow) -> "AvailableResources":
        """The catalogue a NESTED workflow runs against: this one, intersected
        with the callee's own allow-lists.

        Intersected, never replaced. Replacing would let a workflow restricted to
        분류 A reach 분류 B by calling a workflow that carries B, which is the one
        way nesting could widen a boundary. Empty means unrestricted on the
        callee's side too, so a callee with no lists simply inherits the caller's.
        """
        collections = self.collections
        if workflow.collection_ids:
            collections = tuple(c for c in collections if c.id in workflow.collection_ids)
        tools = self.tools
        if workflow.tool_ids:
            tools = tuple(t for t in tools if t.id in workflow.tool_ids)
        return AvailableResources(collections=collections, tools=tools, workflows=self.workflows)


@dataclass(frozen=True)
class ResolvedWorkflow:
    """One workflow, flattened to what a request needs, with no session attached.

    Detached on purpose, the same rule `app/mcp/client.py:MCPTarget` follows: it
    travels through the streaming generator and into the executor, both of which
    run with no database session open.

    `graph` is the ACTIVE version's graph, or None for the default workflow. A
    row always has one after migration 0010 - every pre-Slice-6 agent was
    converted - so None here means "no workflow was named", which is the app
    answering exactly as it did before workflows existed.
    """

    id: uuid.UUID | None = None
    # None is the default workflow, and it is what lands in
    # `messages.workflow_name`: NULL there means "the app answered as it always did".
    name: str | None = None
    prompt_name: str = DEFAULT_PROMPT_NAME
    answer_model: str | None = None
    version: int | None = None
    graph: dict | None = None
    # EMPTY = UNRESTRICTED for both. See the module docstring.
    collection_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    tool_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def scope_collections(self, requested: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        """The collections this question may actually search.

        None out means "no restriction" - which is what `hybrid_search` reads as
        every collection. A LIST out is a closed set, and an empty list is a
        closed set of nothing: `collection_ids=[]` renders as an IN () predicate
        that matches no row, so it returns no evidence rather than silently
        falling back to everything. That distinction is the whole guard.

        Idempotent, so both the router and `load_available` can call it.
        """
        if not self.collection_ids:
            return requested
        if requested is None:
            return sorted(self.collection_ids, key=str)
        allowed = [c for c in requested if c in self.collection_ids]
        if not allowed:
            # Refused, not silently emptied. A question scoped to a collection
            # this workflow cannot reach is a mistake worth a sentence; answering
            # it from nothing would look like the corpus had no answer.
            raise WorkflowScopeError(COLLECTION_NOT_ALLOWED_MESSAGE)
        return allowed

    def allows_tool(self, tool_id: uuid.UUID) -> bool:
        return not self.tool_ids or tool_id in self.tool_ids


# The workflow a request gets when it names none: every field at the value the
# app used before any of this existed. `answer()` already defaults to
# `answer_agent` and both sets are empty, so nothing about this object narrows
# anything and it carries no graph to run.
DEFAULT_WORKFLOW = ResolvedWorkflow()


def resolve(workflow: Workflow, version: WorkflowVersion | None) -> ResolvedWorkflow:
    return ResolvedWorkflow(
        id=workflow.id,
        name=workflow.name,
        prompt_name=workflow.prompt_name,
        answer_model=workflow.answer_model,
        version=version.version if version else None,
        graph=version.graph if version else None,
        collection_ids=frozenset(c.id for c in workflow.collections),
        tool_ids=frozenset(t.id for t in workflow.tools),
    )


async def load_workflow(db: AsyncSession, workflow_id: uuid.UUID | None) -> ResolvedWorkflow:
    """Resolve the workflow a chat request named, or refuse.

    Called BEFORE the conversation is created and before the StreamingResponse
    begins, for the reason every other pre-flight check in that router is: once
    the status line is on the wire a refusal degrades into an error frame inside
    a 200, and a bad workflow id must not leave a titled, empty conversation in
    the sidebar.
    """
    if workflow_id is None:
        return DEFAULT_WORKFLOW
    workflow = await db.scalar(select(Workflow).where(Workflow.id == workflow_id))
    if workflow is None:
        raise HTTPException(status_code=404, detail=WORKFLOW_NOT_FOUND_MESSAGE)
    if not workflow.enabled:
        # 409, not the 404 above: the row exists and an admin turned it off, so
        # there is nothing to conceal - only a state to explain.
        raise HTTPException(status_code=409, detail=WORKFLOW_DISABLED_MESSAGE)
    version = await db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.is_active.is_(True)
        )
    )
    return resolve(workflow, version)


async def load_available(
    db: AsyncSession,
    collection_ids: list[uuid.UUID] | None = None,
    workflow: ResolvedWorkflow = DEFAULT_WORKFLOW,
) -> AvailableResources:
    """What this question may reach: collections, MCP tools, saved workflows.

    `collection_ids` is the scope the REQUEST asked for, and narrowing here is
    what makes "a graph may only name collections that were passed to it" true
    rather than aspirational.

    `workflow` is the SECOND, stronger narrowing, and THIS IS THE PLACE IT CANNOT
    BE BYPASSED. A tool the workflow does not carry never enters the catalogue,
    so `validate_graph` cannot resolve its name and refuses the whole graph - at
    SAVE for an authored one and at PLAN time for a 슈퍼 에이전트 one.

    Tools are exactly what `GET /api/mcp/tools` lists - enabled tools on enabled
    servers - so a tool an admin turned off is not merely un-runnable, it is
    invisible and unnameable. Workflows are the enabled ones that HAVE an active
    version: a workflow with no graph is not callable, and listing it would only
    produce a refusal one layer later.
    """
    scoped = workflow.scope_collections(collection_ids)
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
    # UNNARROWED, and read before the workflow's allow-list is applied. It feeds
    # `graph_risk_level` below, and a nested workflow's risk must be the truth
    # about what IT calls, not about what this caller happens to be allowed to
    # call itself - otherwise wrapping a destructive tool in a workflow the
    # caller cannot reach directly would launder it past the approval gate.
    all_rows = (await db.execute(tool_query)).all()
    risk_by_ref = {f"{server.name}/{tool.name}": tool.risk_level for tool, server in all_rows}
    if workflow.tool_ids:
        tool_query = tool_query.where(McpTool.id.in_(workflow.tool_ids))
    rows = (await db.execute(tool_query)).all() if workflow.tool_ids else all_rows
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

    workflow_rows = (
        await db.execute(
            select(Workflow, WorkflowVersion)
            .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
            .where(Workflow.enabled.is_(True), WorkflowVersion.is_active.is_(True))
            .order_by(Workflow.name)
        )
    ).all()
    workflows = tuple(
        AvailableWorkflow(
            id=row.id,
            name=row.name,
            description=row.description,
            version=version.version,
            graph=version.graph,
            risk_level=graph_risk_level(version.graph, risk_by_ref),
            collection_ids=frozenset(c.id for c in row.collections),
            tool_ids=frozenset(t.id for t in row.tools),
        )
        for row, version in workflow_rows
        # A WORKFLOW DOES APPEAR IN ITS OWN CATALOGUE, and that is deliberate.
        # Leaving it out looks like a cheap way to refuse self-reference, and it
        # silently breaks the case that actually matters: A saving a node that
        # calls B, whose graph calls A. The walk in `validate_graph` resolves
        # names against THIS tuple, so with A missing from it the walk reaches B,
        # cannot resolve A, and reports no cycle. Both cases are caught by the
        # `seen` set instead, which is one rule rather than two.
    )
    return AvailableResources(collections=collections, tools=tools, workflows=workflows)
