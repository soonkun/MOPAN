"""The one interface everything callable implements.

**RAG IMPLEMENTS THE TOOL INTERFACE. IT DOES NOT BECOME AN MCP SERVER.** The
owner's phrasing was "RAG를 수행하는 MCP" and the intent is right - the planner,
the executor and the `@` menu must treat RAG, MCP and workflows as one kind of
thing. But **MCP is a transport, not the definition of a tool.** Making retrieval
a real MCP server would put an HTTP round trip, a serialisation and an auth
handshake in front of a search that is measured at 269ms in-process. Uniformity
comes from the interface; it does not come from the transport.

**EVERY TOOL RETURNS `list[Evidence]`.** That is the seam Slice 1 built and it has
now held four times: attachments, MCP, the orchestrator, and this. `answer()` does
not change, which is this slice's first acceptance criterion.

`risk_level`:
- `RagTool` is `read` - a search of this deployment's own corpus.
- `McpTool` carries the row's, which has existed since `mcp_tools`' first
  migration and defaults to `write` on discovery, because an unclassified tool
  must not be the cheap one.
- `WorkflowTool` inherits **the maximum risk of what its graph calls**. A
  workflow that wraps a destructive tool must not look safe, and the approval
  gate reads this the same way it reads a bare tool's.
"""

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.mcp.service import PendingToolCall, run_tool_calls
from app.retrieval.evidence import Evidence
from app.retrieval.reranker import Reranker
from app.retrieval.service import hybrid_search
from app.retrieval.vector_store import PgVectorStore
from app.workflow.catalogue import AvailableResources, AvailableTool, AvailableWorkflow
from app.workflow.graph import Node, workflow_risk_level

logger = logging.getLogger("mopan.workflow")

MISSING_QUERY_MESSAGE = "검색어가 비어 있어 실행하지 못했습니다."
DEPTH_EXCEEDED_MESSAGE = "워크플로우 중첩이 깊이 상한({limit})을 넘었습니다."
TOOL_CALL_CEILING_MESSAGE = "도구 호출이 상한({limit}회)을 넘었습니다."


@dataclass
class ToolContext:
    """The collaborators a tool call cannot invent for itself, plus the two
    counters that are shared across NESTING.

    `depth` and `tool_calls` are mutable and shared by reference on purpose: a
    workflow that calls a workflow must spend from the SAME tool-call budget as
    its caller, or the ceiling is per-level and a three-deep graph costs three
    times what the number on the screen says.
    """

    settings: Settings
    llm_provider: LLMProvider
    sessionmaker: async_sessionmaker[AsyncSession]
    # None means the rerank stage is absent, not stubbed. See
    # app/retrieval/reranker.py for why a null object is banned here.
    reranker: Reranker | None
    depth: int = 0
    # A one-element list rather than an int, because an int would be copied into
    # the nested context and the budget would silently reset. See above.
    tool_calls: list[int] = field(default_factory=lambda: [0])

    def spend(self) -> None:
        self.tool_calls[0] += 1
        if self.tool_calls[0] > self.settings.orchestrator_max_tool_calls:
            raise ToolLimitError(
                TOOL_CALL_CEILING_MESSAGE.format(limit=self.settings.orchestrator_max_tool_calls)
            )

    def check_depth(self) -> None:
        """Refuse to go a level deeper. Called by the executor BEFORE `spend`, and
        that order is the whole reason this is a separate method.

        Every level of nesting IS a tool call, so depth can never exceed the
        tool-call count. With the shipped defaults (3 and 3) a depth check made
        after `spend` therefore fires never: the ceiling always gets there first,
        and `workflow_max_depth` would be a setting with no behaviour. Checking
        first also means a refused descent costs no budget, which is the right way
        round anyway.
        """
        if self.depth + 1 > self.settings.workflow_max_depth:
            raise ToolLimitError(
                DEPTH_EXCEEDED_MESSAGE.format(limit=self.settings.workflow_max_depth)
            )

    def deeper(self) -> "ToolContext":
        # Checked again here rather than trusting the caller: `WorkflowTool.call`
        # is reachable from anywhere, and a bound a caller has to remember is not
        # a bound.
        self.check_depth()
        return ToolContext(
            settings=self.settings,
            llm_provider=self.llm_provider,
            sessionmaker=self.sessionmaker,
            reranker=self.reranker,
            depth=self.depth + 1,
            tool_calls=self.tool_calls,
        )


class ToolLimitError(RuntimeError):
    """A bound fired. Recorded on the node and the run carries on, exactly as a
    failed node does - the question is still worth answering from whatever else
    was found."""


class Tool(ABC):
    """RAG, MCP and a saved workflow, behind one signature."""

    name: str
    description: str | None = None
    input_schema: dict = {}
    risk_level: str = "read"

    @abstractmethod
    async def call(self, args: dict, *, ctx: ToolContext) -> list[Evidence]:
        """Arguments already resolved by `expr.resolve` - so nothing here ever
        sees a `{{...}}` - and Evidence out. No session, no request."""


class RagTool(Tool):
    """In-process `hybrid_search`. See the module docstring for why it is not
    behind HTTP."""

    risk_level = "read"

    def __init__(self, collection_ids: tuple[uuid.UUID, ...], names: tuple[str, ...] = ()) -> None:
        self.collection_ids = collection_ids
        self.name = "rag"
        self.description = ", ".join(names) or None
        self.input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}

    async def call(self, args: dict, *, ctx: ToolContext) -> list[Evidence]:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolLimitError(MISSING_QUERY_MESSAGE)
        # Its own session, opened and closed inside the call: nodes in a wave run
        # concurrently and would otherwise share one connection.
        async with ctx.sessionmaker() as db:
            return await hybrid_search(
                db,
                PgVectorStore(db),
                ctx.llm_provider,
                ctx.reranker,
                query.strip(),
                top_n=ctx.settings.retrieval_top_n,
                rrf_k=ctx.settings.rrf_k,
                candidate_limit=ctx.settings.retrieval_candidate_limit,
                sparse_weight=ctx.settings.sparse_weight,
                # NO `or None`. `validate_graph` writes the whole catalogue into a
                # node that named no collections, so an empty tuple here means the
                # catalogue was empty - and `or None` would turn "this workflow
                # may reach nothing" into "search every collection in the
                # database", the exact inversion the restriction exists to
                # prevent. hybrid_search reads [] as an IN () predicate that
                # matches no row, which is the truthful answer.
                collection_ids=list(self.collection_ids),
            )


class McpTool(Tool):
    """Over HTTP, through the client Slice 2 built. The detached `MCPTarget` is
    what lets this run with no session open."""

    def __init__(self, tool: AvailableTool) -> None:
        self.tool = tool
        self.name = f"mcp:{tool.ref}"
        self.description = tool.description
        self.input_schema = tool.input_schema
        self.risk_level = tool.risk_level

    async def call(self, args: dict, *, ctx: ToolContext) -> list[Evidence]:
        return await run_tool_calls(
            [
                PendingToolCall(
                    target=self.tool.target,
                    server_name=self.tool.server_name,
                    tool_name=self.tool.tool_name,
                    arguments=args,
                    risk_level=self.tool.risk_level,
                )
            ],
            settings=ctx.settings,
        )


class WorkflowTool(Tool):
    """A saved workflow, called as a tool. **Through the same executor.**

    Its `risk_level` is the maximum of what its graph calls, so wrapping a
    destructive tool in a workflow does not launder it past the approval gate.

    Its catalogue is the CALLER's, intersected with its own allow-lists
    (`AvailableResources.narrow`), so nesting can only ever narrow what is
    reachable - never widen it.
    """

    def __init__(self, workflow: AvailableWorkflow, resources: AvailableResources) -> None:
        self.workflow = workflow
        self.resources = resources
        # The callee's node trace, for the CALLER to fold into its own. Without
        # it `TracePlanStep.depth` would be a field that is always 0 and a
        # workflow that failed three levels down would be one `done` row on the
        # screen with no way to ask why.
        self.nested_trace: list[dict] = []
        self.name = f"workflow:{workflow.name}"
        self.description = workflow.description
        self.input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        self.risk_level = workflow_risk_level(workflow)

    async def call(self, args: dict, *, ctx: ToolContext) -> list[Evidence]:
        # Imported here, not at module scope: the executor imports this module to
        # build its tools, so a top-level import would be a cycle. One late
        # import is cheaper than a third module that exists only to break it.
        from app.workflow.executor import WorkflowRun
        from app.workflow.graph import validate_graph

        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolLimitError(MISSING_QUERY_MESSAGE)
        # deeper() raises before anything runs when the depth limit is reached.
        nested_ctx = ctx.deeper()
        narrowed = self.resources.narrow(self.workflow)
        # RE-VALIDATED against the narrowed catalogue, never trusted because it
        # was valid when it was saved: the callee's graph may name a tool this
        # CALLER cannot reach, and that has to be refused here rather than run.
        graph = validate_graph(self.workflow.graph, narrowed, settings=ctx.settings)
        run = WorkflowRun(
            graph,
            narrowed,
            question=query.strip(),
            settings=ctx.settings,
            llm_provider=ctx.llm_provider,
            sessionmaker=ctx.sessionmaker,
            reranker=ctx.reranker,
            ctx=nested_ctx,
            author="사람",
            workflow_name=self.workflow.name,
            workflow_version=self.workflow.version,
        )
        async for _ in run.stream():
            # A nested run's frames are not STREAMED: the SSE contract describes
            # one run's nodes, and interleaving a callee's ids with its caller's
            # live would make the progress list unreadable. The TRACE is a
            # different question - it is read afterwards, at leisure - so the
            # callee's rows are handed back for the caller to fold in under a
            # prefixed id.
            pass
        self.nested_trace = run.node_trace
        return run.evidence()


def tool_for(node: Node, resources: AvailableResources) -> Tool:
    """The one place a node becomes a Tool. `validate_graph` has already resolved
    the name, so exactly one of the three fields is set."""
    if node.tool is not None:
        return McpTool(node.tool)
    if node.workflow is not None:
        return WorkflowTool(node.workflow, resources)
    return RagTool(node.rag_collection_ids, node.rag_collection_names)
