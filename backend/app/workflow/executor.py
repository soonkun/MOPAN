"""Running a validated graph, under bounds, and pausing for a human.

**THERE IS ONE EXECUTOR.** A 워크플로우 a person drew and the graph 슈퍼 에이전트 just
wrote are the same input to this class - that is the design's central point and
its fifth acceptance criterion. The only thing that differs is `author`, which is
a field in the trace and changes nothing about how a node runs. If a second
execution path ever appears here, the slice has been undone.

Properties, each with a test that fails without it:

- **A failed node does not abort the run.** The user asked a question, and one
  search that blew up does not make the other four worthless. The failure is
  recorded in the trace and the run carries on.
- **The whole run is under one wall clock**, enforced with `asyncio.timeout`
  applied PER WAVE against a SINGLE deadline rather than around the whole
  generator. An `asyncio.timeout` that fires while an async generator is
  suspended at a `yield` cancels the CONSUMER's task and escapes as a bare
  CancelledError, killing the SSE stream instead of ending the run. The
  orchestrator learned this the hard way; nothing is yielded inside the block.
- **A node above the risk threshold pauses instead of running.** The run stops, a
  single-use token is issued and no answer is produced.
- **Bounds that a graph calling a graph makes necessary**: the node ceiling, the
  tool-call ceiling counted across nesting, and a depth limit. Cycles are refused
  statically at save AND cannot outrun the depth counter here.

WAVES, WITH BRANCHES. `input`, `branch` and `answer` resolve instantly and cost
nothing, so they are settled first, repeatedly, until the only ready nodes left
are tool calls - and THOSE are the wave that runs concurrently under the clock. A
branch activates one of its outgoing edges and prunes the other; a node all of
whose incoming edges were pruned is pruned in turn, which is what makes the
untaken side of a branch not run rather than run-and-be-ignored.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import log_event
from app.llm.base import LLMProvider
from app.models.mcp import RISK_LEVELS
from app.retrieval.evidence import Evidence
from app.retrieval.reranker import Reranker
from app.workflow.catalogue import AvailableResources
from app.workflow.expr import ExpressionError, evaluate, resolve
from app.workflow.graph import Node, WorkflowGraph
from app.workflow.tools import ToolContext, ToolLimitError, tool_for

logger = logging.getLogger("mopan.workflow")

NODE_FAILED_MESSAGE = "노드 실행에 실패했습니다."
NODE_TIMEOUT_MESSAGE = "제한 시간을 넘겨 실행하지 못했습니다."
NODE_DENIED_MESSAGE = "사용자가 실행을 거부했습니다."
NODE_PRUNED_MESSAGE = "분기에서 선택되지 않았습니다."

# What the trace calls the two authors. The words are the product owner's and
# they are the whole reason there is one trace shape rather than two.
AUTHOR_HUMAN = "사람"
AUTHOR_SUPER_AGENT = "슈퍼 에이전트"


def needs_approval(risk_level: str, threshold: str) -> bool:
    """At or above the threshold. `RISK_LEVELS` is ordered least to most
    dangerous, and an unknown level - which the Settings validator already
    refuses at boot - is treated as the most dangerous rather than the least."""
    if risk_level not in RISK_LEVELS:
        return True
    return RISK_LEVELS.index(risk_level) >= RISK_LEVELS.index(threshold)


def empty_run_trace(
    settings: Settings, *, refused: str | None = None, author: str = AUTHOR_SUPER_AGENT
) -> dict:
    """The `plan` key for a run that never happened: the planner refused, or it
    returned a graph with nothing but input and answer in it.

    Recorded rather than omitted. "슈퍼 에이전트 was on and did nothing" is exactly
    the fact the trace screen has to be able to state, and a missing key is
    indistinguishable from an answer produced before any of this existed.
    """
    return {
        "author": author,
        "workflow_name": None,
        "workflow_version": None,
        "steps": [],
        "step_count": 0,
        "tool_step_count": 0,
        "timed_out": False,
        "elapsed_ms": 0,
        "fell_back_to_direct_rag": True,
        "refused": refused,
        "budget_seconds": settings.orchestrator_timeout_seconds,
        "max_steps": settings.orchestrator_max_steps,
        "max_nodes": settings.workflow_max_nodes,
        "max_tool_calls": settings.orchestrator_max_tool_calls,
        "max_depth": settings.workflow_max_depth,
        "approval_risk_level": settings.orchestrator_approval_risk_level,
    }


def evidence_to_dict(item: Evidence) -> dict:
    return {
        "source_type": item.source_type,
        "ref": item.ref,
        "content": item.content,
        "score": item.score,
        "metadata": item.metadata,
    }


def evidence_from_dict(raw: dict) -> Evidence:
    return Evidence(
        source_type=raw.get("source_type", "rag"),
        ref=raw.get("ref", ""),
        content=raw.get("content", ""),
        score=raw.get("score"),
        metadata=raw.get("metadata") or {},
    )


def node_value(items: list[Evidence]) -> dict:
    """What a `{{...}}` reference can reach on a node that has run.

    Deliberately a small, FLAT, typed shape rather than the Evidence objects: a
    reference must resolve to one scalar (see expr.py), and exposing the whole
    metadata dict would invite `{{node.items.0.metadata}}` - a structure - into
    an argument. `title` is the filename for a corpus hit and the tool ref for a
    tool result, because that is what a person would put in a follow-up query.
    """
    entries = [
        {
            "ref": item.ref,
            "title": item.metadata.get("filename") or item.ref,
            "text": item.content,
            "score": item.score,
        }
        for item in items
    ]
    return {
        "count": len(entries),
        "items": entries,
        "top": entries[0] if entries else None,
        "text": entries[0]["text"] if entries else "",
    }


class WorkflowRun:
    """One execution of one graph, resumable.

    A class rather than a function because it has three outputs and only one can
    be yielded: the SSE frames a caller streams, the evidence the answer is built
    from, and the trace rows that go into `messages.trace`.

    `results` and `node_trace` are handed in on a resume, which is what makes an
    approved run continue rather than start over - re-running a `write` tool the
    user already approved because a LATER node needed a second approval is
    exactly the unattended repeat the gate exists to prevent.
    """

    def __init__(
        self,
        graph: WorkflowGraph,
        resources: AvailableResources,
        *,
        question: str,
        settings: Settings,
        llm_provider: LLMProvider,
        sessionmaker: async_sessionmaker[AsyncSession],
        reranker: Reranker,
        approved: frozenset[str] = frozenset(),
        denied: frozenset[str] = frozenset(),
        results: dict[str, list[Evidence]] | None = None,
        node_trace: list[dict] | None = None,
        ctx: ToolContext | None = None,
        author: str = AUTHOR_HUMAN,
        workflow_name: str | None = None,
        workflow_version: int | None = None,
    ) -> None:
        self.graph = graph
        self.resources = resources
        self.question = question
        self.settings = settings
        self.author = author
        self.workflow_name = workflow_name
        self.workflow_version = workflow_version
        self.approved = approved
        self.denied = denied
        self.results: dict[str, list[Evidence]] = dict(results or {})
        self.node_trace: list[dict] = list(node_trace or [])
        self.ctx = ctx or ToolContext(
            settings=settings,
            llm_provider=llm_provider,
            sessionmaker=sessionmaker,
            reranker=reranker,
        )
        # The node waiting on a human, set when the run stops early.
        self.pause: Node | None = None
        self.timed_out = False
        self.elapsed_ms = 0
        # Edge state. An edge is None until its source resolves, then True
        # (activate the target) or False (pruned).
        self._edges: dict[int, bool | None] = {index: None for index in range(len(graph.edges))}
        # The variable space `{{...}}` reads. `input` is seeded before anything
        # runs, which is why `{{input.text}}` is always legal.
        self._scope: dict[str, dict] = {}

    # -- reading the result ------------------------------------------------

    def _finished(self) -> set[str]:
        return {entry["id"] for entry in self.node_trace}

    def evidence(self) -> list[Evidence]:
        """Every node's evidence as ONE list, deduplicated and interleaved.

        Deduplicated because several searches of one corpus return the same
        chunk, and paying for it twice in `ANSWER_CONTEXT_TOKEN_BUDGET` is the
        one way a multi-node graph can be strictly worse than a single search.

        Interleaved round-robin across the SEARCH nodes, because the budget cuts
        from the END: five nodes of six hits each is thirty items where the
        budget holds about six, so concatenating them would hand the model six
        hits from the FIRST node and nothing from the other four - a graph whose
        extra nodes cost money and changed no answer.

        Tool and workflow evidence goes first, in node order: it was asked for
        specifically, it is usually short, and it is the part a corpus search
        cannot supply.
        """
        tool_ids = [n.id for n in self.graph.tool_nodes() if n.tool is not None or n.workflow is not None]
        rag_ids = [n.id for n in self.graph.tool_nodes() if n.tool is None and n.workflow is None]
        merged: list[Evidence] = []
        seen: set[str] = set()

        def add(item: Evidence) -> None:
            if item.ref in seen:
                return
            seen.add(item.ref)
            merged.append(item)

        for node_id in tool_ids:
            for item in self.results.get(node_id, []):
                add(item)
        lists = [self.results.get(node_id, []) for node_id in rag_ids]
        for position in range(max((len(items) for items in lists), default=0)):
            for items in lists:
                if position < len(items):
                    add(items[position])
        return merged

    def trace(self, *, fallback: bool = False) -> dict:
        """The `plan` key of `messages.trace`. **ONE shape**, whoever authored the
        graph - the design says so in as many words, and a second trace would
        make "which one am I looking at" unanswerable on the screen."""
        return {
            # 사람 or 슈퍼 에이전트. The only field that differs between the two
            # paths, which is the point.
            "author": self.author,
            "workflow_name": self.workflow_name,
            "workflow_version": self.workflow_version,
            "steps": self.node_trace,
            "step_count": len(self.graph.nodes),
            "tool_step_count": len(self.graph.tool_nodes()),
            "timed_out": self.timed_out,
            "elapsed_ms": self.elapsed_ms,
            "fell_back_to_direct_rag": fallback,
            "refused": None,
            "budget_seconds": self.settings.orchestrator_timeout_seconds,
            "max_steps": self.settings.orchestrator_max_steps,
            "max_nodes": self.settings.workflow_max_nodes,
            "max_tool_calls": self.settings.orchestrator_max_tool_calls,
            "max_depth": self.settings.workflow_max_depth,
            "approval_risk_level": self.settings.orchestrator_approval_risk_level,
        }

    # -- running -----------------------------------------------------------

    def _record(
        self,
        node: Node,
        state: str,
        *,
        count: int = 0,
        ms: int = 0,
        error: str | None = None,
        arguments: dict | None = None,
    ) -> dict:
        entry = {
            "id": node.id,
            "kind": node.kind,
            "label": node.label,
            "state": state,
            "query": (node.arguments or {}).get("query") if node.kind == "tool" else None,
            "collections": list(node.rag_collection_names),
            "tool": node.tool_ref,
            "risk_level": node.risk_level,
            # The RESOLVED arguments when there are any, so the trace shows what
            # actually went over the wire rather than the `{{...}}` that produced
            # it. That is the single most useful thing this screen can say about
            # a graph whose second node reads the first node's output.
            "arguments": arguments if arguments is not None else (node.arguments or None),
            "depends_on": [edge.source for edge in self.graph.incoming(node.id)],
            "depth": self.ctx.depth,
            "evidence_count": count,
            "ms": ms,
            "error": error,
        }
        self.node_trace.append(entry)
        return entry

    def _frame(self, entry: dict) -> dict:
        return {
            "type": "step",
            "id": entry["id"],
            "kind": entry["kind"],
            "label": entry["label"],
            "state": entry["state"],
            "evidence_count": entry["evidence_count"],
            "ms": entry["ms"],
            "detail": entry["error"],
        }

    def _activate(self, node_id: str, *, taken: str | None = None) -> None:
        """Resolve every edge OUT of a node. `taken` is the branch's verdict."""
        for index, edge in enumerate(self.graph.edges):
            if edge.source != node_id:
                continue
            self._edges[index] = True if taken is None else edge.when == taken

    def _prune_from(self, node_id: str) -> None:
        for index, edge in enumerate(self.graph.edges):
            if edge.source == node_id:
                self._edges[index] = False

    def _state_of(self, node: Node) -> str:
        """`ready`, `pruned`, or `waiting`."""
        incoming = [
            (index, edge)
            for index, edge in enumerate(self.graph.edges)
            if edge.target == node.id
        ]
        if not incoming:
            # An orphan. Only `input` normally has no incoming edge; anything else
            # with none is a node a person drew and did not wire up, and running
            # it is friendlier than silently ignoring it.
            return "ready"
        states = [self._edges[index] for index, _ in incoming]
        if any(state is None for state in states):
            return "waiting"
        return "ready" if any(states) else "pruned"

    async def _run(self, node: Node) -> None:
        started = time.perf_counter()
        resolved: dict | None = None
        try:
            resolved = resolve(node.arguments, self._scope)
            tool = tool_for(node, self.resources)
            if node.workflow is not None:
                # BEFORE `spend`, and see ToolContext.check_depth for why: after
                # it, the tool-call ceiling always fires first and the depth limit
                # is a setting with no behaviour.
                self.ctx.check_depth()
            self.ctx.spend()
            items = await tool.call(resolved, ctx=self.ctx)
        except asyncio.CancelledError:
            # The wall-clock budget, or a client disconnect. Not recorded here -
            # the wave loop marks every unrecorded node as timed out, and
            # swallowing a cancellation would break both.
            raise
        except (ExpressionError, ToolLimitError) as exc:
            # A reference that did not resolve to one value, an argument longer
            # than the cap, the tool-call ceiling, or the depth limit. The Korean
            # sentence is already safe to show - these are OUR messages, not a
            # provider's - so unlike the generic branch below it is kept.
            self._record(
                node,
                "failed",
                ms=int((time.perf_counter() - started) * 1000),
                error=str(exc),
                arguments=resolved,
            )
            return
        except Exception:
            # A tool that merely FAILS never reaches this: run_tool_calls turns an
            # MCPError into evidence saying so, on purpose. What lands here is the
            # retrieval side - a dead connection, an embedding call that would not
            # complete - and the message is generic because the real one can quote
            # a prompt or a DSN.
            logger.exception("workflow node failed", extra={"node": node.id})
            self._record(
                node,
                "failed",
                ms=int((time.perf_counter() - started) * 1000),
                error=NODE_FAILED_MESSAGE,
                arguments=resolved,
            )
            return
        self.results[node.id] = list(items)
        self._scope[node.id] = node_value(list(items))
        self._record(
            node,
            "done",
            count=len(items),
            ms=int((time.perf_counter() - started) * 1000),
            arguments=resolved,
        )
        self._fold_nested(node, tool)

    def _fold_nested(self, node: Node, tool: object) -> None:
        """A `workflow:` node's callee rows, folded into this trace.

        Ids are prefixed with the calling node's - `call/search` - so they cannot
        collide with a node id of this graph (`validate_graph` refuses `/` in an
        id) and `_finished()` cannot mistake one for a node it has to run. The
        rows carry the callee's own `depth`, which is what makes that field mean
        something rather than being 0 on every row.
        """
        for entry in getattr(tool, "nested_trace", None) or []:
            self.node_trace.append({**entry, "id": f"{node.id}/{entry['id']}"})

    def _settle_instant(self) -> list[dict]:
        """Resolve every ready `input`, `branch` and `answer` node, repeatedly.

        They cost nothing and produce no evidence, so settling them outside the
        wall-clock block keeps the budget spent on the calls that actually spend
        money - and it is what lets a branch decide which tool node is in the
        NEXT wave.
        """
        entries: list[dict] = []
        moved = True
        while moved:
            moved = False
            for node in self.graph.nodes:
                if node.id in self._finished() or node.kind == "tool":
                    continue
                state = self._state_of(node)
                if state == "waiting":
                    continue
                if state == "pruned":
                    self._prune_from(node.id)
                    entries.append(self._record(node, "skipped", error=NODE_PRUNED_MESSAGE))
                    moved = True
                    continue
                if node.kind == "input":
                    self._scope["input"] = {"text": self.question}
                    self._activate(node.id)
                    entries.append(self._record(node, "done"))
                elif node.kind == "branch":
                    try:
                        verdict = evaluate(node.condition, self._scope)
                    except ExpressionError as exc:
                        # A branch that cannot be decided prunes BOTH sides rather
                        # than guessing one. Guessing would run a tool because an
                        # expression was malformed, which is the worst of the
                        # three available outcomes.
                        self._prune_from(node.id)
                        entries.append(self._record(node, "failed", error=str(exc)))
                        moved = True
                        continue
                    self._activate(node.id, taken="true" if verdict else "false")
                    entries.append(
                        self._record(node, "done", arguments={"result": verdict})
                    )
                else:  # answer
                    self._activate(node.id)
                    entries.append(self._record(node, "done"))
                moved = True
        return entries

    async def stream(self) -> AsyncIterator[dict]:
        """Yields the SSE payloads for this run. Nothing is yielded from inside
        the `asyncio.timeout` block - see the module docstring."""
        run_started = time.perf_counter()
        deadline = run_started + self.settings.orchestrator_timeout_seconds
        threshold = self.settings.orchestrator_approval_risk_level
        # The node ceiling, counted at RUN as well as refused at save. A graph
        # row can be edited in the database, and a saved graph outlives the
        # settings that were in force when it was saved.
        if len(self.graph.nodes) > self.settings.workflow_max_nodes:
            log_event(
                logger,
                "workflow_node_ceiling",
                nodes=len(self.graph.nodes),
                limit=self.settings.workflow_max_nodes,
            )
            self.elapsed_ms = 0
            return
        # A resumed run has to put its finished nodes' values back before
        # anything reads `{{...}}`, or the node after an approved one resolves
        # against an empty scope.
        self._scope["input"] = {"text": self.question}
        for node_id, items in self.results.items():
            self._scope[node_id] = node_value(items)
        # And put the EDGE state back, which is not the same as marking the nodes
        # finished. Three cases, and getting them wrong on a resume would run the
        # untaken side of a branch on the second request only:
        #  - a branch resolved one way, recorded in its `arguments.result`
        #  - a node PRUNED by a branch prunes its successors in turn
        #  - anything else that finished - done, failed, or a node the human
        #    denied - resolves its edges, because "the user refused this tool" is
        #    not "abandon the rest of the run"
        for entry in self.node_trace:
            if entry.get("state") not in ("done", "skipped", "failed"):
                continue
            if entry["kind"] == "branch" and entry.get("state") == "done":
                self._activate(
                    entry["id"],
                    taken="true" if (entry.get("arguments") or {}).get("result") else "false",
                )
            elif entry.get("error") == NODE_PRUNED_MESSAGE:
                self._prune_from(entry["id"])
            else:
                self._activate(entry["id"])
        try:
            while True:
                for entry in self._settle_instant():
                    yield self._frame(entry)

                done = self._finished()
                wave = [
                    node
                    for node in self.graph.nodes
                    if node.kind == "tool" and node.id not in done and self._state_of(node) == "ready"
                ]
                pruned = [
                    node
                    for node in self.graph.nodes
                    if node.kind == "tool" and node.id not in done and self._state_of(node) == "pruned"
                ]
                for node in pruned:
                    self._prune_from(node.id)
                    yield self._frame(self._record(node, "skipped", error=NODE_PRUNED_MESSAGE))
                if pruned:
                    continue
                if not wave:
                    return

                # A node the human refused, and every refusal before any approval:
                # a denied node is finished, not blocking.
                remaining = []
                for node in wave:
                    if node.id in self.denied:
                        self._activate(node.id)
                        yield self._frame(self._record(node, "skipped", error=NODE_DENIED_MESSAGE))
                    else:
                        remaining.append(node)
                wave = remaining
                if not wave:
                    continue

                blocked = next(
                    (
                        node
                        for node in wave
                        if node.id not in self.approved
                        and node.risk_level is not None
                        and needs_approval(node.risk_level, threshold)
                    ),
                    None,
                )
                if blocked is not None:
                    # The WHOLE run stops, not just this node: everything after it
                    # is ordered behind it, and producing an answer now would be
                    # answering a question the user is still being asked about.
                    self.pause = blocked
                    log_event(
                        logger,
                        "workflow_paused_for_approval",
                        node=blocked.id,
                        tool=blocked.tool_ref,
                        risk_level=blocked.risk_level,
                    )
                    return

                for node in wave:
                    yield {
                        "type": "step",
                        "id": node.id,
                        "kind": node.kind,
                        "label": node.label,
                        "state": "running",
                        "evidence_count": 0,
                        "ms": 0,
                        "detail": None,
                    }

                budget = deadline - time.perf_counter()
                if budget > 0:
                    try:
                        async with asyncio.timeout(budget):
                            await asyncio.gather(*(self._run(node) for node in wave))
                    except TimeoutError:
                        self.timed_out = True
                else:
                    self.timed_out = True

                recorded = {entry["id"]: entry for entry in self.node_trace}
                for node in wave:
                    entry = recorded.get(node.id) or self._record(
                        node, "timeout", error=NODE_TIMEOUT_MESSAGE
                    )
                    # Whatever happened - done, failed or timed out - the node is
                    # finished and its edges resolve, so a later node is not left
                    # waiting on one that will never report.
                    self._activate(node.id)
                    yield self._frame(entry)

                if self.timed_out:
                    log_event(
                        logger,
                        "workflow_timed_out",
                        budget_seconds=self.settings.orchestrator_timeout_seconds,
                        completed=len([e for e in self.node_trace if e["state"] == "done"]),
                    )
                    return
        finally:
            self.elapsed_ms = int((time.perf_counter() - run_started) * 1000)
