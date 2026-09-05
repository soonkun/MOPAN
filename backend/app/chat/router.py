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

from app.attachments.service import (
    attachment_root,
    load_claimable,
    no_vision_message,
    to_evidence,
    to_image_urls,
)
from app.auth.authorization import get_owned_conversation
from app.auth.dependencies import get_current_user
from app.chat.intent import classify_intent
from app.chat.condense import condense_followup
from app.chat.service import ChatAnswer, answer, load_history, persist_turn, retrieve
from app.core.config import (
    MODEL_LABELS,
    Settings,
    get_app_settings,
    model_supports_reasoning,
)
from app.core.localtime import now_line
from app.core.db import get_db_session
from app.core.logging import log_event
from app.core.redis import get_redis
from app.documents.storage import delete_document_files
from app.llm.base import LLMError, LLMProvider
from app.mcp.auto import deliberate_and_run
from app.mcp.service import load_tool_calls, run_tool_calls
from app.models.attachment import Attachment
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.retrieval.evidence import Evidence
from app.retrieval.reranker import make_reranker
from app.retrieval.vector_store import PgVectorStore
from app.schemas.chat import (
    AnswerModelResponse,
    ApprovalDecision,
    ChatRequest,
    ConversationResponse,
    MessageResponse,
)
from app.schemas.search import EvidenceResponse, SearchRequest, SearchResponse
from app.workflow.approval import (
    APPROVAL_NOT_FOUND_MESSAGE,
    consume_pending,
    store_pending,
)
from app.workflow.catalogue import (
    ResolvedWorkflow,
    WorkflowScopeError,
    load_available,
    load_workflow,
)
from app.workflow.executor import (
    AUTHOR_HUMAN,
    AUTHOR_SUPER_AGENT,
    WorkflowRun,
    empty_run_trace,
    evidence_from_dict,
    evidence_to_dict,
)
from app.workflow.graph import GraphError, WorkflowGraph, validate_graph
from app.workflow.planner import plan as make_plan

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
    run: WorkflowRun,
    graph: WorkflowGraph,
    *,
    settings: Settings,
    user: User,
    conversation: Conversation,
    question: str,
    model: str,
    collection_ids: list[uuid.UUID] | None,
    attachment_ids: list[uuid.UUID],
    tool_evidence: list[Evidence],
    workflow: ResolvedWorkflow,
) -> dict:
    """Store everything the resume needs and return the frame that asks.

    **UNCHANGED FROM SLICE 3 IN EVERY RESPECT THAT MATTERS**, which is why it was
    reused rather than redesigned: single-use token, `GETDEL` on consume, burned
    on refusal, and NAMES stored rather than resolved objects.

    WHAT IS STORED IS NAMES: the graph goes back to the JSON shape it was
    authored in, and the resume re-loads the catalogue and re-validates against
    it. So a tool an admin disabled while the user was deciding is refused on
    resume exactly as it would have been on a fresh request - and no MCP auth
    token is written to Redis at any point.

    The evidence already gathered rides along, so approving does not re-run the
    nodes that already finished. Re-running a `write` tool because a LATER node
    needed its own approval is precisely the unattended repeat this gate exists
    to prevent.

    The WORKFLOW is stored as an id, for the same reason the graph is stored as
    names: the resume re-loads it and re-narrows the catalogue against it, so a
    workflow an admin disabled - or whose tool list they trimmed - while the user
    was deciding refuses the resumed run exactly as it would refuse a fresh one.
    """
    node = run.pause
    assert node is not None
    token = await store_pending(
        redis,
        {
            "user_id": str(user.id),
            "conversation_id": str(conversation.id),
            "question": question,
            "model": model,
            "workflow_id": str(workflow.id) if workflow.id else None,
            "author": run.author,
            "collection_ids": [str(c) for c in collection_ids] if collection_ids else None,
            "attachment_ids": [str(a) for a in attachment_ids],
            "graph": graph.to_raw(),
            "results": {
                node_id: [evidence_to_dict(item) for item in items]
                for node_id, items in run.results.items()
            },
            "node_trace": run.node_trace,
            "tool_evidence": [evidence_to_dict(item) for item in tool_evidence],
            "awaiting": node.id,
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
            "id": node.id,
            "label": node.label,
            # `server` is None for a `workflow:` node: there is no MCP server
            # behind it, and the risk level it carries is the maximum of what its
            # own graph calls. The client renders `tool` either way.
            "server": node.tool.server_name if node.tool else None,
            "tool": node.tool_ref,
            "risk_level": node.risk_level,
            "arguments": node.arguments,
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
    workflow: ResolvedWorkflow,
    user_nickname: str | None = None,
    auto_tool_ids: list[uuid.UUID] | None = None,
    reasoning_effort: str | None = None,
    client_tz: str | None = None,
) -> AsyncIterator[str]:
    """Everything after the evidence has been gathered: retrieve if there is
    none, answer, persist, emit `citations` and `done`.

    Shared by POST /api/chat and POST /api/chat/approve, which differ only in how
    they got their evidence. A resumed run has to end exactly the way a fresh one
    does - same fallback, same trace, same `done` frame carrying the real row id -
    and two copies of this would have diverged on the first bug fix.

    `evidence` is what the turn already carries whatever the graph did: the
    user's own attachments, then the tools they picked by hand.
    """
    retrieval_ms = plan_ms
    fell_back = not plan_evidence
    # 의도 게이트. 직접 RAG로 떨어지는 발화만 판정한다 - 그래프·플래너가 근거를
    # 만들었거나(plan_evidence) 사용자가 도구·첨부를 직접 골랐으면(evidence)
    # 검색 의도는 이미 표명된 것이라 게이트가 물을 것이 없다. "chat"이면 검색
    # 자체를 건너뛴다: 인사말의 정답 청크는 존재하지 않고, 존재하지 않는 것을
    # 잘 검색하는 방법도 존재하지 않는다. 모든 실패는 "search"로 강등되므로
    # (classify_intent 참조) 이 게이트가 최악의 경우 하는 일은 아무것도 바꾸지
    # 않는 것이다.
    intent = "search"
    # 후속 턴 압축이 게이트보다 먼저다. "소셜네트워크용이야"는 홀로 보면
    # 잡담이라 게이트가 chat으로 넘겼고, smalltalk이 분류표 대신 자기 지식으로
    # 근거 0개 답을 냈다(실사고). 이력에 비추어 자립형 검색 질문이 만들어지면
    # 그것이 곧 검색 의도의 표명이므로 게이트를 부르지 않는다. 압축이 "pass"
    # (이미 자립형이거나 진짜 잡담)면 원문이 게이트로 간다 - 첫 턴은 이력이
    # 없어 이 단계 자체가 없고, 계약은 전부 원문 강등이다(condense.py).
    question_for_retrieval = question
    if fell_back and not evidence and history and settings.followup_condense:
        condensed = await condense_followup(
            llm_provider,
            history,
            question,
            model=settings.query_expansion_model,
            timeout=settings.query_expansion_timeout_seconds,
        )
        if condensed:
            question_for_retrieval = condensed
            log_event(logger, "followup_condensed", chars=len(condensed))
    if question_for_retrieval is question and fell_back and not evidence and settings.intent_gate:
        intent = await classify_intent(
            llm_provider,
            question,
            model=settings.query_expansion_model,
            timeout=settings.query_expansion_timeout_seconds,
        )
    if intent == "chat":
        fell_back = False
    # 자동 도구 사용 (app/mcp/auto.py) - 켜 둔 서버의 read 도구를 모델이 보고
    # 필요하면 부른다. 의도와 무관하게 묻는다: "오늘 날씨 어때?"는 게이트가
    # chat으로 분류하지만(코퍼스에 정답 청크가 없다는 판정 자체는 옳다) 답은
    # 도구에 있다 - 게이트가 도구까지 막으면 서버를 켜 둔 의미가 없다(실사고:
    # 잡담 에이전트가 "날씨는 기상청에서"라고 안내해 버림). 인사말이면 숙고가
    # "pass"를 답해 호출이 없고, 그 한 번의 숙고가 서버를 켜 둔 값이다.
    # 실패는 전부 "추가 근거 없음"으로 강등되므로 답변을 못 막는다.
    # 사용자의 "지금" - 숙고와 답변 양쪽에 실린다. "올해 휴일"이 2023년으로
    # 답하던 실사고(모델의 올해는 학습 시점에 고정)의 처방.
    current_time = now_line(client_tz, settings.default_timezone)
    auto_trace = None
    auto_ask = None
    if auto_tool_ids:
        yield _sse({"type": "status", "status": "calling_tool"})
        async with sessionmaker() as tool_db:
            auto_evidence, auto_trace, auto_ask = await deliberate_and_run(
                tool_db,
                llm_provider,
                settings=settings,
                question=question,
                auto_tool_ids=auto_tool_ids,
                model=model,
                workflow=workflow,
                history=history,
                current_time=current_time,
            )
        evidence = evidence + auto_evidence
        if auto_evidence and intent == "chat":
            # 도구가 근거를 냈으면 잡담이 아니다: 잡담 프롬프트 대신 근거 답변
            # 프롬프트로, 근거-없음 경고 규칙도 정상 답변의 것으로.
            if auto_trace is not None:
                auto_trace["intent_promoted"] = "chat->search"
            intent = "search"
        if auto_evidence:
            # 도구가 근거를 냈으면 직접 RAG는 건너뛴다. 숙고는 "실시간·외부
            # 데이터가 실제로 필요할 때만" 도구를 부르도록 지시되어 있으므로 그
            # 판정이 곧 "코퍼스는 이 질문의 근거가 아니다"이다. 실사고: "서울
            # 날씨"가 search로 분류되어 도구 근거 1개에 무관한 심사기준 14개가
            # 얹혀 나갔다 - 질문당 약 8천 토큰이 순수 낭비이고, 무관 근거의
            # 전달은 날조를 부른다(answer()의 관련성 바닥 주석 실측). 도구와
            # 문서가 둘 다 필요한 질문은 @로 도구를 직접 고르면 된다 - 그
            # 경로는 검색을 그대로 돈다.
            fell_back = False
        if auto_ask and not evidence and not plan_evidence:
            # 도구가 답인데 필수 인자가 질문에 없다("오늘 날씨 알려줘"에 지역이
            # 없다). 문서 검색으로 새면 "관련 문서가 없습니다"가 나가는데, 그건
            # 이 되물음보다 무조건 나쁜 답이다 - 검색을 건너뛰고 아래에서 이
            # 문장이 그대로 답이 된다. 사용자가 지역을 답하면 다음 턴의 숙고가
            # 대화 이력으로 그것을 읽는다.
            fell_back = False
    if fell_back:
        # THE FALLBACK. A run that yielded nothing - refused, a graph of just
        # input and answer, every node failed, or the clock ran out before the
        # first result - must not produce an ungrounded answer. It answers from
        # the plain RAG path instead, which is also what keeps the Korean
        # uncited-answer notice meaningful.
        yield _sse({"type": "status", "status": "searching"})
        started = time.perf_counter()
        async with sessionmaker() as retrieval_db:
            plan_evidence = await retrieve(
                retrieval_db,
                PgVectorStore(retrieval_db),
                llm_provider,
                make_reranker(settings, llm_provider),
                question_for_retrieval,
                settings=settings,
                collection_ids=collection_ids,
                # THE FALLBACK IS INSIDE THE BOUNDARY TOO. This is the path a
                # refused or empty graph lands on, and a workflow restricted to
                # one collection whose graph was thrown away must not answer from
                # the whole corpus instead. `retrieve` narrows again itself;
                # passing the workflow here is what makes that narrowing
                # reachable.
                workflow=workflow,
            )
        retrieval_ms += int((time.perf_counter() - started) * 1000)
    if plan_trace is not None:
        plan_trace["fell_back_to_direct_rag"] = fell_back

    yield _sse({"type": "status", "status": "answering"})
    if auto_ask and not evidence and not plan_evidence:
        # 되물음이 곧 답이다. 모델을 다시 부르지 않는다 - 숙고가 적은 문장을
        # 그대로 내보내야 "무엇이 부족한지"가 왜곡 없이 닿는다.
        chat_answer = ChatAnswer(
            content=auto_ask,
            model=model or settings.answer_model,
            prompt_name="tool_clarify",
            prompt_version="",
        )
    else:
        chat_answer = await answer(
            llm_provider,
            question,
            history,
            evidence + plan_evidence,
            settings=settings,
            images=images,
            model=model,
            prompt_name=workflow.prompt_name,
            intent=intent,
            user_nickname=user_nickname,
            reasoning_effort=reasoning_effort,
            current_time=current_time,
        )
    # 추적 화면이 "왜 인용이 없는가"에 답할 수 있게. prompt_name(smalltalk_agent)
    # 이 이미 기록되지만, 그것이 게이트의 판정이었다는 사실은 여기만 안다.
    if intent != "search":
        chat_answer.trace["intent"] = intent
    if auto_trace is not None:
        chat_answer.trace["auto_tools"] = auto_trace
    if question_for_retrieval != question:
        # 추적 화면이 "검색은 실제로 무엇을 찾았는가"에 답할 수 있게.
        chat_answer.trace["condensed_query"] = question_for_retrieval
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
            workflow_name=workflow.name,
            workflow_version=workflow.version,
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
            # 화면이 "왜 인용이 없는가"를 구분할 열쇠 - smalltalk_agent 답변에는
            # 근거-없음 경고 자체가 부적용이다.
            "prompt_name": chat_answer.prompt_name,
            # Null when no workflow answered. Carried on the frame so the answer
            # on screen says what produced it without waiting for a reload,
            # exactly as `model` is.
            "workflow_name": workflow.name,
            "workflow_version": workflow.version,
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
    added `calling_tool`; Slice 3 added `planning`, a `step` frame per step and an
    `approval_required` frame the client answers with a second request. Slice 6
    keeps every one of them and changes what produces them: a `step` frame is now
    a graph NODE, and the graph is either the workflow's or one 슈퍼 에이전트 just
    wrote. The `token` event type is still reserved.

    **THERE IS ONE EXECUTION PATH BELOW.** A saved workflow and 슈퍼 에이전트 differ
    only in where the graph came from; both then go through `WorkflowRun`. If
    that ever stops being true, the slice has been undone."""
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
    # THE WORKFLOW FIRST, because everything below is resolved against it. A
    # missing id is a 404 and a disabled one a 409, both before the conversation
    # exists - the rule every other pre-flight check in this function follows.
    workflow = await load_workflow(db, payload.workflow_id)

    # The workflow supplies the DEFAULT, never the ceiling: the allowlist below is
    # still the only thing that decides what reaches the provider, so a row whose
    # model an operator later dropped from ANSWER_MODELS is refused here exactly
    # as a forged body would be. An explicit `model` in the request still wins,
    # which is what keeps the composer's own picker meaningful when a workflow is
    # selected.
    model = payload.model or workflow.answer_model or settings.answer_model
    if model not in settings.selectable_models:
        raise HTTPException(status_code=400, detail=f"사용할 수 없는 답변 모델입니다: {model}")

    # THE COLLECTION BOUNDARY, resolved before anything is written. `retrieve`
    # and `load_available` both narrow again on their own - this is not the
    # enforcement, it is the refusal: a question scoped to a collection this
    # workflow cannot reach gets a Korean 400 rather than an answer built from
    # nothing, which would read as "the corpus does not say".
    try:
        collection_ids = workflow.scope_collections(payload.collection_ids)
    except WorkflowScopeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 슈퍼 에이전트 IS A PER-CONVERSATION CHOICE AND NOTHING ELSE NOW. It used to
    # be turnable on by a row - `agents.orchestrator` - which is precisely how a
    # fixed procedure ended up switching on autonomous planning. Migration 0010
    # dropped that column; a workflow's remaining job on this path is the scope
    # check, and it is applied below either way.
    #
    # When BOTH are present the model writes the graph and the workflow supplies
    # the boundary, the prompt and the model: 슈퍼 에이전트 is a way of AUTHORING a
    # graph, so a saved graph and an authored one cannot both run on one turn.
    use_planner = payload.orchestrator

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
        db, [(t.tool_id, t.arguments) for t in tool_requests], workflow
    )

    # Loaded here, before the response starts, for the same reason everything
    # above it is: this is the ONLY set of names a graph may use, and reading it
    # needs the request's session. Narrowed by `collection_ids`, so a question
    # scoped to one collection produces a graph that cannot search another.
    # `workflow` goes in with it: a tool it does not carry never enters the
    # catalogue, so a graph naming it cannot be validated and is refused WHOLE
    # rather than filtered - the same treatment a hallucinated name gets.
    #
    # ONE CATALOGUE FOR BOTH AUTHORS. The saved graph is re-validated against it
    # too, never trusted because it was valid at save: an admin may have disabled
    # a tool since, and a graph naming it has to be refused now.
    needs_graph = use_planner or workflow.graph is not None
    resources = await load_available(db, collection_ids, workflow) if needs_graph else None
    saved_graph: WorkflowGraph | None = None
    saved_graph_refused: str | None = None
    if not use_planner and workflow.graph is not None:
        try:
            saved_graph = validate_graph(
                workflow.graph, resources, settings=settings, self_id=workflow.id
            )
        except GraphError as exc:
            # NOT a 400. The world changed under a graph that was valid when it
            # was saved, and the question is still answerable from the direct
            # path - the same posture a refused plan gets, recorded in the trace.
            log_event(logger, "workflow_graph_refused", detail=str(exc))
            saved_graph_refused = str(exc)

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

            # Phase 1: the GRAPH. One of two things put it here - a person saved
            # it, or the model just wrote it - and from the `WorkflowRun` below
            # there is no difference at all. Every session inside the run lives
            # in an `async with`, so a client disconnect - which reaches this
            # generator as GeneratorExit/CancelledError at a yield - still
            # returns the connection.
            plan_evidence: list[Evidence] = []
            plan_trace: dict | None = None
            plan_ms = 0
            graph: WorkflowGraph | None = saved_graph
            author = AUTHOR_HUMAN
            if saved_graph_refused is not None:
                plan_trace = empty_run_trace(settings, refused=saved_graph_refused, author=AUTHOR_HUMAN)
            if use_planner and resources is not None:
                author = AUTHOR_SUPER_AGENT
                yield _sse({"type": "status", "status": "planning"})
                try:
                    graph = await make_plan(
                        payload.message, resources, llm_provider=llm_provider, settings=settings
                    )
                except GraphError as exc:
                    # A refused graph is a PLANNER failure, not a user error: the
                    # question is still answerable from the direct path, so it is
                    # recorded in the trace and the fallback below runs. This is
                    # where a hallucinated tool name ends up.
                    log_event(logger, "plan_refused", detail=str(exc))
                    plan_trace = empty_run_trace(settings, refused=str(exc), author=author)
                    graph = None
            if graph is not None and graph.tool_nodes():
                # THE ONE EXECUTOR. Nothing below this line knows which author
                # produced the graph except the `author` field it records.
                run = WorkflowRun(
                    graph,
                    resources,
                    question=payload.message,
                    settings=settings,
                    llm_provider=llm_provider,
                    sessionmaker=sessionmaker,
                    reranker=make_reranker(settings, llm_provider),
                    author=author,
                    workflow_name=workflow.name,
                    workflow_version=workflow.version,
                )
                async for frame in run.stream():
                    yield _sse(frame)
                if run.pause is not None:
                    yield _sse(
                        await _pause_frame(
                            redis,
                            run,
                            graph,
                            settings=settings,
                            user=user,
                            conversation=conversation,
                            question=payload.message,
                            model=model,
                            collection_ids=collection_ids,
                            attachment_ids=attachment_ids,
                            tool_evidence=tool_evidence,
                            workflow=workflow,
                        )
                    )
                    # TERMINAL. No answer is produced: the user is being asked
                    # whether a high-risk tool may run, and answering now would
                    # be answering a question that is still open.
                    return
                plan_evidence = run.evidence()
                plan_trace = run.trace()
                plan_ms = run.elapsed_ms
            elif graph is not None:
                # A graph of just input and answer is a legitimate answer from the
                # planner - "one plain search would do" - and a legitimate thing
                # for a person to draw. It falls through to exactly that.
                plan_trace = empty_run_trace(settings, author=author)

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
                workflow=workflow,
                user_nickname=user.nickname,
                auto_tool_ids=payload.auto_tool_ids,
                reasoning_effort=payload.reasoning_effort,
                client_tz=payload.client_tz,
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
    """Resume a run that paused on a high-risk node. Same SSE contract as
    POST /api/chat, because it is the same stream continued.

    **The mechanism is Slice 3's, reused unchanged**: single-use token, `GETDEL`
    on consume, burned on refusal, names in Redis rather than resolved objects.
    What it resumes is a workflow graph now, whoever authored it.

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
    # RE-LOADED, not carried across the pause, for the reason the graph is
    # re-validated below: an admin may have disabled the workflow or trimmed its
    # tool list while the user was deciding, and the resumed request has to be
    # refused exactly as a fresh one would be. load_workflow raises the same
    # 404/409 it raises on /api/chat, before the response starts.
    stored_workflow_id = stored.get("workflow_id")
    workflow = await load_workflow(db, uuid.UUID(stored_workflow_id) if stored_workflow_id else None)
    collection_ids = [uuid.UUID(c) for c in stored.get("collection_ids") or []] or None
    attachment_ids = [uuid.UUID(a) for a in stored.get("attachment_ids") or []]
    attachments = await load_claimable(db, attachment_ids, user)
    images = await to_image_urls(attachments)
    attachment_evidence = to_evidence(attachments)

    # RE-VALIDATED, not trusted across the pause. An admin may have disabled the
    # tool or the whole server while the user was deciding, and a graph that names
    # it must then be refused the way a fresh one would be - which is exactly what
    # load_available + validate_graph already do, with no second rule to keep in
    # step.
    try:
        resources = await load_available(db, collection_ids, workflow)
    except WorkflowScopeError as exc:
        # The workflow's collections were trimmed under the pause and no longer
        # cover the scope this question was asked with. Same 409 the refused graph
        # gets below, and for the same reason: the request was fine, the world
        # changed.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        graph = validate_graph(stored.get("graph"), resources, settings=settings)
    except GraphError as exc:
        # 409, not 404: the request is well-formed and the token was real; the
        # world changed under it. Korean, because it reaches the user.
        log_event(logger, "approval_graph_no_longer_valid", detail=str(exc))
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
        "workflow_approval_decided",
        node=awaiting,
        approved=payload.approved,
        user_id=str(user.id),
    )

    history = await load_history(db, conversation)
    question = stored["question"]
    model = stored["model"]
    tool_evidence = [evidence_from_dict(item) for item in stored.get("tool_evidence") or []]
    results = {
        node_id: [evidence_from_dict(item) for item in items]
        for node_id, items in (stored.get("results") or {}).items()
    }

    async def stream() -> AsyncIterator[str]:
        try:
            run = WorkflowRun(
                graph,
                resources,
                question=question,
                settings=settings,
                llm_provider=llm_provider,
                sessionmaker=sessionmaker,
                reranker=make_reranker(settings, llm_provider),
                approved=frozenset(approved),
                denied=frozenset(denied),
                results=results,
                node_trace=list(stored.get("node_trace") or []),
                # Carried across the pause so the trace still says who wrote the
                # graph. It is the only field the two authors differ on.
                author=stored.get("author") or AUTHOR_HUMAN,
                workflow_name=workflow.name,
                workflow_version=workflow.version,
            )
            async for frame in run.stream():
                yield _sse(frame)
            if run.pause is not None:
                # A SECOND high-risk node. A new token, because the first one is
                # already burned - approving one node is never approval of the next.
                yield _sse(
                    await _pause_frame(
                        redis,
                        run,
                        graph,
                        settings=settings,
                        user=user,
                        conversation=conversation,
                        question=question,
                        model=model,
                        collection_ids=collection_ids,
                        attachment_ids=attachment_ids,
                        tool_evidence=tool_evidence,
                        workflow=workflow,
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
                workflow=workflow,
                user_nickname=user.nickname,
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
        make_reranker(settings, llm_provider),
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
            reasoning=model_supports_reasoning(model),
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
