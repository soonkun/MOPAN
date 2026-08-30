import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.service import AgentScopeError, ResolvedAgent, load_agent
from app.attachments.service import (
    attachment_root,
    load_claimable,
    no_vision_message,
    to_evidence,
    to_image_urls,
)
from app.auth.authorization import get_owned_conversation
from app.auth.dependencies import get_current_user
from app.chat.service import answer, load_history, persist_turn, retrieve
from app.core.config import MODEL_LABELS, Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.core.redis import get_redis
from app.documents.storage import delete_document_files
from app.llm.base import LLMError, LLMProvider
from app.mcp.service import load_tool_calls, run_tool_calls
from app.models.attachment import Attachment
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.orchestrator.approval import (
    APPROVAL_NOT_FOUND_MESSAGE,
    consume_pending,
    store_pending,
)
from app.orchestrator.executor import (
    PlanRun,
    empty_plan_trace,
    evidence_from_dict,
    evidence_to_dict,
)
from app.orchestrator.plan import ExecutionPlan, PlanError, load_available, validate_plan
from app.orchestrator.planner import plan as make_plan
from app.retrieval.evidence import Evidence
from app.retrieval.reranker import NoneReranker
from app.retrieval.vector_store import PgVectorStore
from app.schemas.chat import (
    AnswerModelResponse,
    ApprovalDecision,
    ChatRequest,
    ConversationResponse,
    MessageResponse,
)
from app.schemas.search import EvidenceResponse, SearchRequest, SearchResponse

logger = logging.getLogger("mopan.chat")
router = APIRouter(prefix="/api", tags=["chat"])


def get_llm_provider(request: Request) -> LLMProvider:
    return request.app.state.llm_provider


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.sessionmaker


class ConversationUpdate(BaseModel):
    """The rename body. Local to this router rather than app/schemas/chat.py
    because `title` is the whole thing and nothing else consumes it.

    500 is the column width; 200 is the bound offered to a human. A sidebar row
    truncates at ~30 characters, so anything past that is invisible in the one
    place the title is read - and the auto-generated title is `message[:80]`, so
    200 is already generous against the only other writer of this field."""

    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def _stripped_and_not_blank(cls, value: str) -> str:
        # min_length runs before this, so "   " gets past it. A whitespace-only
        # title renders as an unclickable-looking blank row in the history list.
        stripped = value.strip()
        if not stripped:
            raise ValueError("대화 제목을 입력해 주세요.")
        return stripped


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


async def _pause_frame(
    redis: Redis,
    run: PlanRun,
    execution_plan: ExecutionPlan,
    *,
    settings: Settings,
    user: User,
    conversation: Conversation,
    question: str,
    model: str,
    collection_ids: list[uuid.UUID] | None,
    attachment_ids: list[uuid.UUID],
    tool_evidence: list[Evidence],
    agent: ResolvedAgent,
) -> dict:
    """Store everything the resume needs and return the frame that asks.

    WHAT IS STORED IS NAMES, not resolved objects: the plan goes back to the
    JSON shape the planner emitted, and the resume re-loads the catalogue and
    re-validates against it. So a tool an admin disabled while the user was
    deciding is refused on resume exactly as it would have been on a fresh
    request - and no MCP auth token is written to Redis at any point.

    The evidence already gathered rides along, so approving does not re-run the
    steps that already finished. Re-running a `write` tool because a LATER step
    needed its own approval is precisely the unattended repeat this gate exists
    to prevent.

    The AGENT is stored as an id, for the same reason the plan is stored as
    names: the resume re-loads it and re-narrows the catalogue against it, so an
    agent an admin disabled - or whose tool list they trimmed - while the user
    was deciding refuses the resumed plan exactly as it would refuse a fresh one.
    """
    step = run.pause
    assert step is not None and step.tool is not None
    token = await store_pending(
        redis,
        {
            "user_id": str(user.id),
            "conversation_id": str(conversation.id),
            "question": question,
            "model": model,
            "agent_id": str(agent.id) if agent.id else None,
            "collection_ids": [str(c) for c in collection_ids] if collection_ids else None,
            "attachment_ids": [str(a) for a in attachment_ids],
            "plan": execution_plan.to_raw(),
            "results": {
                step_id: [evidence_to_dict(item) for item in items]
                for step_id, items in run.results.items()
            },
            "step_trace": run.step_trace,
            "tool_evidence": [evidence_to_dict(item) for item in tool_evidence],
            "awaiting": step.id,
            "approved": sorted(run.approved),
            "denied": sorted(run.denied),
            "plan_ms": run.elapsed_ms,
        },
        ttl_seconds=settings.orchestrator_approval_ttl_seconds,
    )
    return {
        "type": "approval_required",
        "approval_token": token,
        "expires_in": settings.orchestrator_approval_ttl_seconds,
        "conversation_id": str(conversation.id),
        "step": {
            "id": step.id,
            "label": step.label,
            "server": step.tool.server_name,
            "tool": step.tool.tool_name,
            "risk_level": step.tool.risk_level,
            "arguments": step.arguments,
        },
    }


async def _complete(
    *,
    llm_provider: LLMProvider,
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    conversation: Conversation,
    question: str,
    history: list[dict],
    evidence: list[Evidence],
    plan_evidence: list[Evidence],
    plan_trace: dict | None,
    plan_ms: int,
    collection_ids: list[uuid.UUID] | None,
    images: list[str] | None,
    model: str,
    attachment_ids: list[uuid.UUID],
    agent: ResolvedAgent,
) -> AsyncIterator[str]:
    """Everything after the evidence has been gathered: retrieve if there is
    none, answer, persist, emit `citations` and `done`.

    Shared by POST /api/chat and POST /api/chat/approve, which differ only in how
    they got their evidence. A resumed plan has to end exactly the way a fresh
    one does - same fallback, same trace, same `done` frame carrying the real row
    id - and two copies of this would have diverged on the first bug fix.

    `evidence` is what the turn already carries whatever the orchestrator did:
    the user's own attachments, then the tools they picked by hand.
    """
    retrieval_ms = plan_ms
    fell_back = not plan_evidence
    if fell_back:
        # THE FALLBACK. A plan that yielded nothing - refused, empty, every step
        # failed, or the clock ran out before the first result - must not produce
        # an ungrounded answer. It answers from the plain RAG path instead, which
        # is also what keeps the Korean uncited-answer notice meaningful.
        yield _sse({"type": "status", "status": "searching"})
        started = time.perf_counter()
        async with sessionmaker() as retrieval_db:
            plan_evidence = await retrieve(
                retrieval_db,
                PgVectorStore(retrieval_db),
                llm_provider,
                NoneReranker(),
                question,
                settings=settings,
                collection_ids=collection_ids,
                # THE FALLBACK IS INSIDE THE BOUNDARY TOO. This is the path a
                # refused or empty plan lands on, and an agent restricted to one
                # collection whose plan was thrown away must not answer from the
                # whole corpus instead. `retrieve` narrows again itself; passing
                # the agent here is what makes that narrowing reachable.
                agent=agent,
            )
        retrieval_ms += int((time.perf_counter() - started) * 1000)
    if plan_trace is not None:
        plan_trace["fell_back_to_direct_rag"] = fell_back

    yield _sse({"type": "status", "status": "answering"})
    chat_answer = await answer(
        llm_provider,
        question,
        history,
        evidence + plan_evidence,
        settings=settings,
        images=images,
        model=model,
        prompt_name=agent.prompt_name,
    )
    if plan_trace is not None:
        # THE ACCEPTANCE TEST FOR THIS SLICE IS THAT answer() DID NOT CHANGE, so
        # the plan is merged into the trace `build_trace` already produced rather
        # than passed into it. `messages.trace` is JSONB and build_trace's own
        # docstring reserved this key, so there is no migration and no new
        # parameter on the one function Slice 1 deliberately gave no collaborators.
        chat_answer.trace["plan"] = plan_trace

    async with sessionmaker() as persist_db:
        assistant_message_id = await persist_turn(
            persist_db,
            conversation,
            question,
            chat_answer,
            retrieval_ms,
            attachment_ids=attachment_ids,
            agent_name=agent.name,
        )

    yield _sse({"type": "citations", "citations": chat_answer.citations})
    yield _sse(
        {
            "type": "done",
            "conversation_id": str(conversation.id),
            "message_id": str(assistant_message_id),
            "content": chat_answer.content,
            "citations": chat_answer.citations,
            "model": chat_answer.model,
            # Null for the default agent. Carried on the frame so the answer on
            # screen says what produced it without waiting for a reload, exactly
            # as `model` is.
            "agent_name": agent.name,
        }
    )


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
    settings: Settings = Depends(get_app_settings),
    redis: Redis = Depends(get_redis),
):
    """Server-Sent Events. Slice 1 emits status -> citations -> done; Slice 2
    added `calling_tool`; Slice 3 adds `planning`, a `step` frame per plan step
    and an `approval_required` frame the client answers with a second request.
    The `token` event type is still reserved."""
    # Resolved BEFORE the response starts. Once StreamingResponse begins the status
    # line is already on the wire, so nothing raised inside the generator can set
    # one: an unowned conversation id would degrade from 404 to a 200 carrying an
    # error frame. The frontend's streamChat() reads response.ok first and expects
    # exactly this.
    # Both checks below run before the conversation is created, for the same
    # reason the ownership check does: a bad attachment id - or a model that is
    # not on the allowlist - must not leave a titled, empty conversation in the
    # sidebar that the user then has to delete by hand.
    #
    # The model FIRST, before the attachments are even loaded: an arbitrary model
    # string from a client must never reach the provider, because the operator
    # pays per call and this allowlist is the only thing standing between a forged
    # body and gpt-4o pricing - or a model that does not exist, whose 400 would
    # otherwise arrive as an error frame inside a 200 after the row was written.
    # THE AGENT FIRST, because everything below is resolved against it. A missing
    # id is a 404 and a disabled one a 409, both before the conversation exists -
    # the rule every other pre-flight check in this function follows.
    agent = await load_agent(db, payload.agent_id)

    # The agent supplies the DEFAULT, never the ceiling: the allowlist below is
    # still the only thing that decides what reaches the provider, so a row whose
    # model an operator later dropped from ANSWER_MODELS is refused here exactly
    # as a forged body would be. An explicit `model` in the request still wins,
    # which is what keeps the composer's own picker meaningful when an agent is
    # selected.
    model = payload.model or agent.answer_model or settings.answer_model
    if model not in settings.selectable_models:
        raise HTTPException(status_code=400, detail=f"사용할 수 없는 답변 모델입니다: {model}")

    # THE COLLECTION BOUNDARY, resolved before anything is written. `retrieve`
    # and `load_available` both narrow again on their own - this is not the
    # enforcement, it is the refusal: a question scoped to a collection this
    # agent cannot reach gets a Korean 400 rather than an answer built from
    # nothing, which would read as "the corpus does not say".
    try:
        collection_ids = agent.scope_collections(payload.collection_ids)
    except AgentScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # An agent that carries the orchestrator turns it on; the per-question toggle
    # can still turn it on for an agent that does not. There is deliberately no
    # way to turn it OFF for an agent configured with it - that is the agent's
    # configuration, and the composer shows the toggle forced on and says so.
    use_orchestrator = payload.orchestrator or agent.orchestrator

    attachment_ids = payload.attachment_ids or []
    if len(attachment_ids) > settings.max_attachments_per_message:
        raise HTTPException(
            status_code=400,
            detail=f"첨부파일은 한 번에 최대 {settings.max_attachments_per_message}개까지 보낼 수 있습니다.",
        )
    attachments = await load_claimable(db, attachment_ids, user)
    # Read off disk here, not inside the generator: a missing file is then a real
    # 404 with a Korean detail rather than an error frame inside a 200.
    images = await to_image_urls(attachments)
    attachment_evidence = to_evidence(attachments)
    # The upload gate only proved SOME allowlisted model can see. This is the one
    # that proves the model the user actually picked can - without it, choosing a
    # text-only model for a question with a screenshot in it sends an image part
    # to a blind model and gets an opaque provider 400 back inside a 200 stream.
    if images and not settings.model_supports_vision(model):
        raise HTTPException(status_code=400, detail=no_vision_message(model))

    # Resolved here, before the conversation exists, for exactly the reason the
    # attachment ids and the model are: an unknown tool id, a tool an admin
    # disabled, or one classified `destructive` must be a real 4xx with a Korean
    # detail, not an error frame inside a 200 after a titled empty conversation
    # has been written to the sidebar. load_tool_calls returns detached targets,
    # so nothing below holds a session across the network call.
    tool_requests = payload.tool_calls or []
    if len(tool_requests) > settings.max_tool_calls_per_message:
        raise HTTPException(
            status_code=400,
            detail=f"도구는 한 번에 최대 {settings.max_tool_calls_per_message}개까지 호출할 수 있습니다.",
        )
    pending_tool_calls = await load_tool_calls(
        db, [(t.tool_id, t.arguments) for t in tool_requests], agent
    )

    # Loaded here, before the response starts, for the same reason everything
    # above it is: this is the ONLY set of names the planner may use, and reading
    # it needs the request's session. Narrowed by `collection_ids`, so a question
    # scoped to one collection produces a plan that cannot search another.
    # `agent` goes in with it: a tool the agent does not carry never enters the
    # catalogue, so a plan naming it cannot be validated and is refused WHOLE
    # rather than filtered - the same treatment a hallucinated name gets.
    resources = await load_available(db, collection_ids, agent) if use_orchestrator else None

    if payload.conversation_id is None:
        conversation = Conversation(user_id=user.id, title=payload.message[:80])
        db.add(conversation)
        # No refresh: `id` is a client-side default populated at flush, and
        # expire_on_commit=False leaves it readable after the commit and the close.
        await db.commit()
    else:
        conversation = await get_owned_conversation(db, payload.conversation_id, user)
    history = await load_history(db, conversation)
    # `db` is not touched again below, and it must not be: since FastAPI 0.106 a
    # yield-dependency's exit code runs BEFORE the response body is sent, so this
    # session is already closed by the time the generator runs. Using it there
    # would either fail or - if the version's behaviour ever moves back - hold
    # get_current_user's autobegun transaction open across the whole LLM round
    # trip, which is exactly the pool exhaustion the phases below avoid. Measured,
    # not assumed: test_no_connection_is_idle_in_transaction_across_the_llm_call
    # reads pg_stat_activity from a second connection at that moment.

    async def stream() -> AsyncIterator[str]:
        try:
            # Phase 0: the tools the user picked, if any. Before retrieval so the
            # user sees the slow, visible thing happening first, and with NO
            # session open - run_tool_calls takes detached targets on purpose.
            # A tool that fails returns Evidence saying so rather than raising:
            # the question is still worth answering from whatever else was found.
            tool_evidence = []
            if pending_tool_calls:
                yield _sse({"type": "status", "status": "calling_tool"})
                tool_evidence = await run_tool_calls(pending_tool_calls, settings=settings)

            # Phase 1: the plan, if the user asked for one. Every session below
            # lives inside an `async with`, so a client disconnect - which reaches
            # this generator as GeneratorExit/CancelledError at a yield - still
            # returns the connection.
            plan_evidence: list[Evidence] = []
            plan_trace: dict | None = None
            plan_ms = 0
            if resources is not None:
                yield _sse({"type": "status", "status": "planning"})
                execution_plan: ExecutionPlan | None = None
                try:
                    execution_plan = await make_plan(
                        payload.message, resources, llm_provider=llm_provider, settings=settings
                    )
                except PlanError as exc:
                    # A refused plan is a PLANNER failure, not a user error: the
                    # question is still answerable from the direct path, so it is
                    # recorded in the trace and the fallback below runs. This is
                    # where a hallucinated tool name ends up.
                    log_event(logger, "plan_refused", detail=str(exc))
                    plan_trace = empty_plan_trace(settings, refused=str(exc))
                if execution_plan is not None and execution_plan.steps:
                    run = PlanRun(
                        execution_plan,
                        resources,
                        settings=settings,
                        llm_provider=llm_provider,
                        sessionmaker=sessionmaker,
                        reranker=NoneReranker(),
                    )
                    async for frame in run.stream():
                        yield _sse(frame)
                    if run.pause is not None:
                        yield _sse(
                            await _pause_frame(
                                redis,
                                run,
                                execution_plan,
                                settings=settings,
                                user=user,
                                conversation=conversation,
                                question=payload.message,
                                model=model,
                                collection_ids=collection_ids,
                                attachment_ids=attachment_ids,
                                tool_evidence=tool_evidence,
                                agent=agent,
                            )
                        )
                        # TERMINAL. No answer is produced: the user is being asked
                        # whether a high-risk tool may run, and answering now would
                        # be answering a question that is still open.
                        return
                    plan_evidence = run.evidence()
                    plan_trace = run.trace()
                    plan_ms = run.elapsed_ms
                elif execution_plan is not None:
                    # An empty plan is a legitimate answer from the planner - "one
                    # plain search would do" - and it falls through to exactly that.
                    plan_trace = empty_plan_trace(settings)

            # Phases 2 and 3. The user's own files first: they are the most
            # specific thing in the request, and build_prompt fills evidence in
            # order, so if the budget cannot hold everything it is a corpus chunk
            # that goes, not the PDF the user just attached. It is ONE list from
            # here on - attachment text, hand-picked tool results and plan
            # evidence all compete for the same ANSWER_CONTEXT_TOKEN_BUDGET
            # rather than being added on top of one another. That single list is
            # the entire security argument of Slices 2 and 3: a tool result
            # inherits the nonce fence, _strip_fence_markers and the one budget
            # structurally, because there is nowhere else for it to go.
            async for frame in _complete(
                llm_provider=llm_provider,
                sessionmaker=sessionmaker,
                settings=settings,
                conversation=conversation,
                question=payload.message,
                history=history,
                evidence=attachment_evidence + tool_evidence,
                plan_evidence=plan_evidence,
                plan_trace=plan_trace,
                plan_ms=plan_ms,
                collection_ids=collection_ids,
                images=images,
                model=model,
                attachment_ids=attachment_ids,
                agent=agent,
            ):
                yield frame
        except LLMError:
            # The traceback goes to the log, never into the stream: the detail a
            # provider raises can quote the prompt back.
            logger.exception("chat failed at the LLM call")
            yield _sse({"type": "error", "detail": "답변 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."})
        except Exception:
            logger.exception("chat failed")
            yield _sse({"type": "error", "detail": "요청을 처리하지 못했습니다."})

    # no-transform is load-bearing, not decoration. Next.js ships `compress: true`
    # by default, so its /api/* rewrite proxy gzips this response whenever the
    # client asks for gzip - and gzip buffers, so the frames stop reaching the
    # client as they are emitted. Every browser asks; curl does not unless
    # given --compressed, which is why an early probe of the rewrite (Task 20)
    # reported the stream arriving intact and was wrong.
    #
    # Measured through `next start` against a stub origin emitting four SSE frames
    # at 0/500/2000/2000ms - so they leave the origin at 0, 500, 2500 and 4500ms -
    # timing raw socket reads. The numbers below are CUMULATIVE ms since the
    # request, and the first read of each row is the response headers:
    #   origin direct, any Accept-Encoding    none  0, 0, 502, 2513, 4514, 4514
    #   via Next, no Accept-Encoding          none  22, 522, 2530, 4536, 4536
    #   via Next, Accept-Encoding: gzip       gzip  3, 4534, 4534
    #   via Next, gzip + this header          none  3, 509, 2520, 4525, 4526
    # Five reads collapse to three, and both body reads land at 4534ms - after the
    # last frame was emitted. So without this header a browser gets the headers,
    # then nothing until the answer is finished, and the status frames arrive in
    # the same read as the answer they were supposed to precede.
    #
    # Where gzip flushes is a byte threshold, not end-of-stream: padding each frame
    # to 8KB moves the first body read to 2533ms - still two frames late, just not
    # all the way to the end. Real status frames here are ~45 bytes, the small end
    # of that, which is the row above.
    #
    # `compress: false` in next.config.js would also fix it, and is worse twice
    # over: Next-only, and it turns gzip off for the whole app's HTML, JS and CSS
    # to fix one endpoint. A response header travels with the resource to every
    # proxy in the chain; a build flag stops at the first. Cloudflare's docs say
    # it compresses at its own edge and honours no-transform - documented, not
    # measured here, and worth re-checking against the deployment.
    #
    # X-Accel-Buffering: no beside it is nginx's vendor hint for a different
    # failure - nginx's proxy buffering, not its gzip. nginx is the one hop no
    # header covers here: its gzip module has no no-transform handling at all, so
    # `gzip_proxied any` would compress this stream anyway and would have to be
    # configured not to. Nothing in this deployment terminates through nginx.
    #
    # Residual risk, for Task 24: cloudflared has its own reported SSE buffering
    # behaviour, unrelated to compression, that no-transform does not address.
    # Re-measure the status labels once the tunnel is actually up.
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/approve")
async def approve(
    payload: ApprovalDecision,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
    settings: Settings = Depends(get_app_settings),
    redis: Redis = Depends(get_redis),
):
    """Resume a plan that paused on a high-risk step. Same SSE contract as
    POST /api/chat, because it is the same stream continued.

    Everything that can refuse does so BEFORE the response starts, exactly as
    /api/chat resolves its model and its tool ids first: once a StreamingResponse
    has begun there is no status line left to set, and a 404 would degrade into
    an error frame inside a 200.

    The token is consumed - read and deleted in one atomic GETDEL - before
    anything else happens, so it cannot be replayed even by a double-clicked
    button, and a token belonging to another user is the same 404 an unknown one
    gets.
    """
    stored = await consume_pending(redis, payload.approval_token, user.id)
    if stored is None:
        raise HTTPException(status_code=404, detail=APPROVAL_NOT_FOUND_MESSAGE)

    conversation = await get_owned_conversation(db, uuid.UUID(stored["conversation_id"]), user)
    # RE-LOADED, not carried across the pause, for the reason the plan is
    # re-validated below: an admin may have disabled the agent or trimmed its
    # tool list while the user was deciding, and the resumed request has to be
    # refused exactly as a fresh one would be. load_agent raises the same 404/409
    # it raises on /api/chat, before the response starts.
    stored_agent_id = stored.get("agent_id")
    agent = await load_agent(db, uuid.UUID(stored_agent_id) if stored_agent_id else None)
    collection_ids = [uuid.UUID(c) for c in stored.get("collection_ids") or []] or None
    attachment_ids = [uuid.UUID(a) for a in stored.get("attachment_ids") or []]
    attachments = await load_claimable(db, attachment_ids, user)
    images = await to_image_urls(attachments)
    attachment_evidence = to_evidence(attachments)

    # RE-VALIDATED, not trusted across the pause. An admin may have disabled the
    # tool or the whole server while the user was deciding, and a plan that names
    # it must then be refused the way a fresh one would be - which is exactly what
    # load_available + validate_plan already do, with no second rule to keep in
    # step.
    try:
        resources = await load_available(db, collection_ids, agent)
    except AgentScopeError as exc:
        # The agent's collections were trimmed under the pause and no longer
        # cover the scope this question was asked with. Same 409 the refused plan
        # gets below, and for the same reason: the request was fine, the world
        # changed.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        execution_plan = validate_plan(stored.get("plan"), resources, settings=settings)
    except PlanError as exc:
        # 409, not 404: the request is well-formed and the token was real; the
        # world changed under it. Korean, because it reaches the user.
        log_event(logger, "approval_plan_no_longer_valid", detail=str(exc))
        raise HTTPException(
            status_code=409,
            detail="승인을 기다리는 동안 계획을 실행할 수 없게 되었습니다. 질문을 다시 보내 주세요.",
        ) from exc

    awaiting = stored.get("awaiting")
    approved = set(stored.get("approved") or [])
    denied = set(stored.get("denied") or [])
    (approved if payload.approved else denied).add(awaiting)
    log_event(
        logger,
        "plan_approval_decided",
        step=awaiting,
        approved=payload.approved,
        user_id=str(user.id),
    )

    history = await load_history(db, conversation)
    question = stored["question"]
    model = stored["model"]
    tool_evidence = [evidence_from_dict(item) for item in stored.get("tool_evidence") or []]
    results = {
        step_id: [evidence_from_dict(item) for item in items]
        for step_id, items in (stored.get("results") or {}).items()
    }

    async def stream() -> AsyncIterator[str]:
        try:
            run = PlanRun(
                execution_plan,
                resources,
                settings=settings,
                llm_provider=llm_provider,
                sessionmaker=sessionmaker,
                reranker=NoneReranker(),
                approved=frozenset(approved),
                denied=frozenset(denied),
                results=results,
                step_trace=list(stored.get("step_trace") or []),
            )
            async for frame in run.stream():
                yield _sse(frame)
            if run.pause is not None:
                # A SECOND high-risk step. A new token, because the first one is
                # already burned - approving one step is never approval of the next.
                yield _sse(
                    await _pause_frame(
                        redis,
                        run,
                        execution_plan,
                        settings=settings,
                        user=user,
                        conversation=conversation,
                        question=question,
                        model=model,
                        collection_ids=collection_ids,
                        attachment_ids=attachment_ids,
                        tool_evidence=tool_evidence,
                        agent=agent,
                    )
                )
                return
            async for frame in _complete(
                llm_provider=llm_provider,
                sessionmaker=sessionmaker,
                settings=settings,
                conversation=conversation,
                question=question,
                history=history,
                evidence=attachment_evidence + tool_evidence,
                plan_evidence=run.evidence(),
                plan_trace=run.trace(),
                plan_ms=int(stored.get("plan_ms") or 0) + run.elapsed_ms,
                collection_ids=collection_ids,
                images=images,
                model=model,
                attachment_ids=attachment_ids,
                agent=agent,
            ):
                yield frame
        except LLMError:
            logger.exception("approved plan failed at the LLM call")
            yield _sse({"type": "error", "detail": "답변 생성에 실패했습니다. 잠시 후 다시 시도해 주세요."})
        except Exception:
            logger.exception("approved plan failed")
            yield _sse({"type": "error", "detail": "요청을 처리하지 못했습니다."})

    # The same headers /api/chat sends, and for the same measured reason: without
    # no-transform the Next.js rewrite proxy gzips the stream and buffers every
    # frame until the answer is finished.
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    settings: Settings = Depends(get_app_settings),
):
    """Retrieval on its own, so search quality can be inspected without going
    through the chat model. Same corpus and the same collection scoping as
    /api/chat - it reaches nothing chat could not already answer from."""
    effective = (
        settings if payload.top_n is None else settings.model_copy(update={"retrieval_top_n": payload.top_n})
    )
    evidence = await retrieve(
        db,
        PgVectorStore(db),
        llm_provider,
        NoneReranker(),
        payload.query,
        settings=effective,
        collection_ids=payload.collection_ids,
    )
    return SearchResponse(
        query=payload.query,
        results=[
            EvidenceResponse(
                source_type=e.source_type,
                ref=e.ref,
                content=e.content,
                score=e.score,
                metadata=e.metadata,
            )
            for e in evidence
        ],
    )


@router.get("/models", response_model=list[AnswerModelResponse])
async def list_models(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
):
    """What the composer's model picker lists. Any authenticated user may read it:
    it is the same allowlist POST /api/chat enforces, so it discloses nothing a
    user could not already learn by sending a model and being refused."""
    return [
        AnswerModelResponse(
            id=model,
            label=MODEL_LABELS.get(model, model),
            is_default=model == settings.answer_model,
        )
        for model in settings.selectable_models
    ]


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.scalars(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc())
    )
    return list(result)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    # The returned object, not the bare id: discarding it and re-querying by id is
    # the caller-discipline pattern load_history/persist_turn exist to remove.
    conversation = await get_owned_conversation(db, conversation_id, user)
    result = await db.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at)
    )
    return list(result)


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """The sidebar's 이름 변경. Auto-titling takes `message[:80]` of the first
    question, which is a sentence fragment more often than it is a name.

    404 for a missing id and for someone else's alike - get_owned_conversation's
    rule, unchanged, so a rename cannot be used to probe for ids the way a 403
    would let it. Note this bumps `updated_at`, so a renamed conversation moves to
    the top of the history list: that list is ordered by updated_at, and a rename
    is an update to the row the list is showing."""
    conversation = await get_owned_conversation(db, conversation_id, user)
    conversation.title = payload.title
    await db.commit()
    # `updated_at` is `onupdate=func.now()`, a SERVER-side expression, so the
    # UPDATE leaves that one attribute expired whatever expire_on_commit says -
    # and response serialisation then touches it outside the greenlet and raises
    # MissingGreenlet. This is the load that makes the value real.
    await db.refresh(conversation)
    return conversation


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    conversation = await get_owned_conversation(db, conversation_id, user)
    # Collected before the DELETE, because attachments cascade away with their
    # messages and the ids would be unrecoverable afterwards - the same
    # row-then-file order delete_document uses.
    attachment_ids = (
        await db.scalars(
            select(Attachment.id)
            .join(Message, Message.id == Attachment.message_id)
            .where(Message.conversation_id == conversation.id)
        )
    ).all()
    await db.delete(conversation)  # messages cascade, and attachments with them
    await db.commit()
    root = attachment_root(settings.upload_dir)
    for attachment_id in attachment_ids:
        await delete_document_files(root, str(attachment_id))
