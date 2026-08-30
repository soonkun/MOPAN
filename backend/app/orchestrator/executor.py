"""Running a validated plan, under bounds, and pausing for a human.

Three properties, each of which has a test that fails without it:

- **A failed step does not abort the plan.** The user asked a question, and one
  search that blew up does not make the other four worthless. The failure is
  recorded in the trace and the plan carries on.
- **The whole plan is under one wall clock**, enforced with `asyncio.timeout`
  the way `app/worker.py` bounds ingestion with `PIPELINE_TIMEOUT`. It is
  applied per WAVE against a single deadline rather than around the whole
  generator, because an `asyncio.timeout` that fires while an async generator is
  suspended at a `yield` cancels the CONSUMER's task and escapes as a bare
  CancelledError, killing the SSE stream instead of ending the plan. Every
  result is written by the step itself, so a wave the clock cancels still leaves
  the earlier waves' evidence behind.
- **A step above the risk threshold pauses instead of running.** The plan stops,
  a token is issued and the answer is not produced. Nothing about "we asked and
  nobody came back" runs the tool anyway.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import log_event
from app.llm.base import LLMProvider
from app.mcp.service import PendingToolCall, run_tool_calls
from app.models.mcp import RISK_LEVELS
from app.orchestrator.plan import AvailableResources, ExecutionPlan, PlanStep
from app.retrieval.evidence import Evidence
from app.retrieval.reranker import Reranker
from app.retrieval.service import hybrid_search
from app.retrieval.vector_store import PgVectorStore

logger = logging.getLogger("mopan.orchestrator")

STEP_FAILED_MESSAGE = "단계 실행에 실패했습니다."
STEP_TIMEOUT_MESSAGE = "제한 시간을 넘겨 실행하지 못했습니다."
STEP_DENIED_MESSAGE = "사용자가 실행을 거부했습니다."


def needs_approval(risk_level: str, threshold: str) -> bool:
    """At or above the threshold. `RISK_LEVELS` is ordered least to most
    dangerous, and an unknown level - which the Settings validator already
    refuses at boot - is treated as the most dangerous rather than the least."""
    if risk_level not in RISK_LEVELS:
        return True
    return RISK_LEVELS.index(risk_level) >= RISK_LEVELS.index(threshold)


def empty_plan_trace(settings: Settings, *, refused: str | None = None) -> dict:
    """The `plan` key for a run that never happened: the planner refused, or it
    returned an empty steps list because one plain search would do.

    Recorded rather than omitted. "The orchestrator was on and did nothing" is
    exactly the fact the trace screen has to be able to state, and a missing key
    is indistinguishable from an answer produced before this slice existed.
    """
    return {
        "steps": [],
        "step_count": 0,
        "tool_step_count": 0,
        "timed_out": False,
        "elapsed_ms": 0,
        "fell_back_to_direct_rag": True,
        "refused": refused,
        "budget_seconds": settings.orchestrator_timeout_seconds,
        "max_steps": settings.orchestrator_max_steps,
        "max_tool_calls": settings.orchestrator_max_tool_calls,
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


class PlanRun:
    """One execution of one plan, resumable.

    A class rather than a function because it has three outputs and only one of
    them can be yielded: the SSE frames a caller streams, the evidence the
    answer is built from, and the trace rows that go into `messages.trace`.

    `results` and `step_trace` are handed in on a resume, which is what makes an
    approved plan continue rather than start over - re-running a `write` tool
    the user already approved because a later step needed a second approval is
    exactly the unattended repeat this gate exists to prevent.
    """

    def __init__(
        self,
        plan: ExecutionPlan,
        resources: AvailableResources,
        *,
        settings: Settings,
        llm_provider: LLMProvider,
        sessionmaker: async_sessionmaker[AsyncSession],
        reranker: Reranker,
        approved: frozenset[str] = frozenset(),
        denied: frozenset[str] = frozenset(),
        results: dict[str, list[Evidence]] | None = None,
        step_trace: list[dict] | None = None,
    ) -> None:
        self.plan = plan
        self.resources = resources
        self.settings = settings
        self.llm_provider = llm_provider
        self.sessionmaker = sessionmaker
        self.reranker = reranker
        self.approved = approved
        self.denied = denied
        self.results: dict[str, list[Evidence]] = dict(results or {})
        self.step_trace: list[dict] = list(step_trace or [])
        # The step waiting on a human, set when the run stops early. The caller
        # issues a token for it and emits the approval frame; nothing else about
        # the plan has run past this point.
        self.pause: PlanStep | None = None
        self.timed_out = False
        self.elapsed_ms = 0

    # -- reading the result ------------------------------------------------

    def _finished(self) -> set[str]:
        return {entry["id"] for entry in self.step_trace}

    def evidence(self) -> list[Evidence]:
        """Every step's evidence as ONE list, deduplicated and interleaved.

        Deduplicated because several searches of one corpus return the same
        chunk, and paying for it twice in `ANSWER_CONTEXT_TOKEN_BUDGET` is the
        one way a multi-step plan can be strictly worse than a single search.

        Interleaved round-robin across the RAG steps, because the budget cuts
        from the END: five steps of six hits each is thirty items where the
        budget holds about six, so concatenating them would hand the model six
        hits from the FIRST step and nothing from the other four - a plan whose
        extra steps cost money and changed no answer. Round-robin gives each
        step its best hit before any step gets its second.

        Tool evidence goes first, in step order: the planner asked for it
        specifically, it is usually short, and it is the part a corpus search
        cannot supply.
        """
        tool_ids = [s.id for s in self.plan.steps if s.kind == "tool"]
        rag_ids = [s.id for s in self.plan.steps if s.kind == "rag"]
        merged: list[Evidence] = []
        seen: set[str] = set()

        def add(item: Evidence) -> None:
            if item.ref in seen:
                return
            seen.add(item.ref)
            merged.append(item)

        for step_id in tool_ids:
            for item in self.results.get(step_id, []):
                add(item)
        lists = [self.results.get(step_id, []) for step_id in rag_ids]
        for position in range(max((len(items) for items in lists), default=0)):
            for items in lists:
                if position < len(items):
                    add(items[position])
        return merged

    def trace(self, *, fallback: bool = False) -> dict:
        """The `plan` key of `messages.trace`. Slice 5's `build_trace` docstring
        reserved this and needs no migration for it - that is what the JSONB
        column is for."""
        return {
            "steps": self.step_trace,
            "step_count": len(self.plan.steps),
            "tool_step_count": sum(1 for s in self.plan.steps if s.kind == "tool"),
            "timed_out": self.timed_out,
            "elapsed_ms": self.elapsed_ms,
            # True when the plan ran and produced nothing, so the direct RAG path
            # answered instead. The single most useful thing this screen can say
            # about a slice whose default is still the direct path.
            "fell_back_to_direct_rag": fallback,
            # Set only by empty_plan_trace below; a run that got this far was not
            # refused, and the key exists on both shapes so the screen has one.
            "refused": None,
            "budget_seconds": self.settings.orchestrator_timeout_seconds,
            "max_steps": self.settings.orchestrator_max_steps,
            "max_tool_calls": self.settings.orchestrator_max_tool_calls,
            "approval_risk_level": self.settings.orchestrator_approval_risk_level,
        }

    # -- running -----------------------------------------------------------

    def _record(
        self, step: PlanStep, state: str, *, count: int = 0, ms: int = 0, error: str | None = None
    ) -> dict:
        entry = {
            "id": step.id,
            "kind": step.kind,
            "label": step.label,
            "state": state,
            "query": step.query or None,
            "collections": list(step.collection_names),
            "tool": step.tool.ref if step.tool else None,
            "risk_level": step.tool.risk_level if step.tool else None,
            "arguments": step.arguments or None,
            "depends_on": list(step.depends_on),
            "evidence_count": count,
            "ms": ms,
            "error": error,
        }
        self.step_trace.append(entry)
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

    async def _run(self, step: PlanStep) -> None:
        started = time.perf_counter()
        try:
            if step.kind == "rag":
                # Its own session, opened and closed inside the step: steps in a
                # wave run concurrently and would otherwise share one connection.
                # ORCHESTRATOR_MAX_STEPS (5) is the ceiling on how many are open
                # at once, against a pool of DB_POOL_SIZE + DB_MAX_OVERFLOW (20).
                async with self.sessionmaker() as db:
                    items = await hybrid_search(
                        db,
                        PgVectorStore(db),
                        self.llm_provider,
                        self.reranker,
                        step.query,
                        top_n=self.settings.retrieval_top_n,
                        rrf_k=self.settings.rrf_k,
                        candidate_limit=self.settings.retrieval_candidate_limit,
                        sparse_weight=self.settings.sparse_weight,
                        collection_ids=list(step.collection_ids) or None,
                    )
            else:
                assert step.tool is not None  # validate_plan guarantees it
                items = await run_tool_calls(
                    [
                        PendingToolCall(
                            target=step.tool.target,
                            server_name=step.tool.server_name,
                            tool_name=step.tool.tool_name,
                            arguments=step.arguments,
                            risk_level=step.tool.risk_level,
                        )
                    ],
                    settings=self.settings,
                )
        except asyncio.CancelledError:
            # The wall-clock budget, or a client disconnect. Not recorded here -
            # the wave loop marks every unrecorded step as timed out, and
            # swallowing a cancellation would break both.
            raise
        except Exception:
            # A tool that merely FAILS never reaches this: run_tool_calls turns
            # an MCPError into evidence saying so, on purpose. What lands here is
            # the retrieval side - a dead connection, an embedding call that
            # would not complete - and the message is generic because the real
            # one can quote a prompt or a DSN.
            logger.exception("plan step failed", extra={"step": step.id})
            self._record(
                step,
                "failed",
                ms=int((time.perf_counter() - started) * 1000),
                error=STEP_FAILED_MESSAGE,
            )
            return
        self.results[step.id] = list(items)
        self._record(
            step,
            "done",
            count=len(items),
            ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream(self) -> AsyncIterator[dict]:
        """Yields the SSE payloads for this run. Nothing is yielded from inside
        the `asyncio.timeout` block - see the module docstring."""
        run_started = time.perf_counter()
        deadline = run_started + self.settings.orchestrator_timeout_seconds
        threshold = self.settings.orchestrator_approval_risk_level
        try:
            for wave in self.plan.waves():
                done = self._finished()
                wave = [step for step in wave if step.id not in done]
                if not wave:
                    continue

                # A step the human refused, and every wave's refusals before its
                # approvals: a denied step is finished, not blocking.
                remaining = []
                for step in wave:
                    if step.id in self.denied:
                        yield self._frame(self._record(step, "skipped", error=STEP_DENIED_MESSAGE))
                    else:
                        remaining.append(step)
                wave = remaining
                if not wave:
                    continue

                blocked = next(
                    (
                        step
                        for step in wave
                        if step.tool is not None
                        and step.id not in self.approved
                        and needs_approval(step.tool.risk_level, threshold)
                    ),
                    None,
                )
                if blocked is not None:
                    # The WHOLE plan stops, not just this step: everything after
                    # it is ordered behind it, and producing an answer now would
                    # be answering a question the user is still being asked about.
                    self.pause = blocked
                    log_event(
                        logger,
                        "plan_paused_for_approval",
                        step=blocked.id,
                        tool=blocked.tool.ref if blocked.tool else None,
                        risk_level=blocked.tool.risk_level if blocked.tool else None,
                    )
                    return

                for step in wave:
                    yield {
                        "type": "step",
                        "id": step.id,
                        "kind": step.kind,
                        "label": step.label,
                        "state": "running",
                        "evidence_count": 0,
                        "ms": 0,
                        "detail": None,
                    }

                budget = deadline - time.perf_counter()
                if budget > 0:
                    try:
                        async with asyncio.timeout(budget):
                            await asyncio.gather(*(self._run(step) for step in wave))
                    except TimeoutError:
                        self.timed_out = True
                else:
                    self.timed_out = True

                recorded = {entry["id"]: entry for entry in self.step_trace}
                for step in wave:
                    entry = recorded.get(step.id) or self._record(
                        step, "timeout", error=STEP_TIMEOUT_MESSAGE
                    )
                    yield self._frame(entry)

                if self.timed_out:
                    log_event(
                        logger,
                        "plan_timed_out",
                        budget_seconds=self.settings.orchestrator_timeout_seconds,
                        completed=len([e for e in self.step_trace if e["state"] == "done"]),
                    )
                    return
        finally:
            self.elapsed_ms = int((time.perf_counter() - run_started) * 1000)
