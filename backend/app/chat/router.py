import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
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
from app.chat.service import answer, load_history, persist_turn, retrieve
from app.core.config import MODEL_LABELS, Settings, get_app_settings
from app.core.db import get_db_session
from app.documents.storage import delete_document_files
from app.llm.base import LLMError, LLMProvider
from app.models.attachment import Attachment
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.retrieval.reranker import NoneReranker
from app.retrieval.vector_store import PgVectorStore
from app.schemas.chat import (
    AnswerModelResponse,
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


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
    settings: Settings = Depends(get_app_settings),
):
    """Server-Sent Events. Slice 1 emits status -> citations -> done; the `token`
    event type is reserved, and Slice 3 will add per-step execution status here
    without changing the contract."""
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
    model = payload.model or settings.answer_model
    if model not in settings.selectable_models:
        raise HTTPException(status_code=400, detail=f"사용할 수 없는 답변 모델입니다: {model}")

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
            # Phase 1: a short session for retrieval. Every session lives inside an
            # `async with`, so a client disconnect - which reaches this generator as
            # GeneratorExit/CancelledError at a yield - still returns the connection.
            yield _sse({"type": "status", "status": "searching"})
            retrieval_started = time.perf_counter()
            async with sessionmaker() as retrieval_db:
                evidence = await retrieve(
                    retrieval_db,
                    PgVectorStore(retrieval_db),
                    llm_provider,
                    NoneReranker(),
                    payload.message,
                    settings=settings,
                    collection_ids=payload.collection_ids,
                )
            retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)

            # Phase 2: no DB session held across the LLM round trip.
            yield _sse({"type": "status", "status": "answering"})
            # The user's own files first: they are the most specific thing in the
            # request, and build_prompt fills evidence in order, so if the budget
            # cannot hold everything it is a corpus chunk that goes, not the PDF
            # the user just attached. Note it is ONE list from here on - attachment
            # text competes for ANSWER_CONTEXT_TOKEN_BUDGET with the RAG evidence
            # rather than being added on top of it.
            chat_answer = await answer(
                llm_provider,
                payload.message,
                history,
                attachment_evidence + evidence,
                settings=settings,
                images=images,
                model=model,
            )

            # Phase 3: a fresh short session to persist the turn. `conversation` is
            # the ownership-checked object from above - detached, not expired - so
            # nothing here re-reads it by bare id.
            async with sessionmaker() as persist_db:
                await persist_turn(
                    persist_db,
                    conversation,
                    payload.message,
                    chat_answer,
                    retrieval_ms,
                    attachment_ids=attachment_ids,
                )

            yield _sse({"type": "citations", "citations": chat_answer.citations})
            yield _sse(
                {
                    "type": "done",
                    "conversation_id": str(conversation.id),
                    "content": chat_answer.content,
                    "citations": chat_answer.citations,
                    # The provider's RESOLVED id ("gpt-4o-2024-08-06"), the same
                    # string persisted to Message.model, so the answer on screen
                    # and the answer after a reload are labelled identically.
                    "model": chat_answer.model,
                }
            )
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
