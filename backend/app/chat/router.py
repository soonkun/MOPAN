import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.authorization import get_owned_conversation
from app.auth.dependencies import get_current_user
from app.chat.service import answer, load_history, persist_turn, retrieve
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.llm.base import LLMError, LLMProvider
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.retrieval.reranker import NoneReranker
from app.retrieval.vector_store import PgVectorStore
from app.schemas.chat import ChatRequest, ConversationResponse, MessageResponse
from app.schemas.search import EvidenceResponse, SearchRequest, SearchResponse

logger = logging.getLogger("mopan.chat")
router = APIRouter(prefix="/api", tags=["chat"])


def get_llm_provider(request: Request) -> LLMProvider:
    return request.app.state.llm_provider


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.sessionmaker


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
            chat_answer = await answer(llm_provider, payload.message, history, evidence, settings=settings)

            # Phase 3: a fresh short session to persist the turn. `conversation` is
            # the ownership-checked object from above - detached, not expired - so
            # nothing here re-reads it by bare id.
            async with sessionmaker() as persist_db:
                await persist_turn(persist_db, conversation, payload.message, chat_answer, retrieval_ms)

            yield _sse({"type": "citations", "citations": chat_answer.citations})
            yield _sse(
                {
                    "type": "done",
                    "conversation_id": str(conversation.id),
                    "content": chat_answer.content,
                    "citations": chat_answer.citations,
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
    # by default, so its /api/* rewrite proxy gzips this response - and gzip
    # buffers, which collapses the whole stream into one chunk delivered at the
    # end. Measured through `next start`: with plain no-cache the searching and
    # error frames arrived 234ms and 234ms apart from a backend that emitted them
    # 491ms apart, so the user saw no status at all until the answer landed; with
    # no-transform, 32ms and 575ms. X-Accel-Buffering is the nginx-specific hint
    # for the same problem, and no-transform is the standard one every conforming
    # proxy in the chain - Next, nginx, Cloudflare - is required to honour.
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


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    conversation = await get_owned_conversation(db, conversation_id, user)
    await db.delete(conversation)  # messages cascade
    await db.commit()
