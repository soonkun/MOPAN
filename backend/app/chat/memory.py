"""멀티턴 기억 - 원문 창 밖의 오래된 턴을 대화별 롤링 요약으로 접는다.

설계(docs/technical-report.md §11): 답변 프롬프트는 최근 HISTORY_WINDOW_MESSAGES개
메시지를 원문으로, 그보다 오래된 턴은 conversations.summary 하나로 받는다.
LangChain의 ConversationSummaryBufferMemory·OpenAI Agents SDK의 compaction·
Claude Code의 auto-compact가 모두 같은 "버퍼+요약" 모양이다 - 슬라이딩 창만으로는
열 메시지 앞의 사실이 소리 없이 사라지고, 전체 요약만으로는 직전 턴의 어투와
세부가 뭉개진다.

갱신은 답변이 저장된 뒤 백그라운드 태스크에서 값싼 모델이 한다. 사용자 대기
시간에 들어가지 않고, 실패하면 요약이 그대로일 뿐 답변은 영향이 없다.
summary_through_at 뒤의 메시지만 새로 접으므로 어떤 이유로 한 번 빠져도 다음
턴이 이어받는다(멱등).

요약은 사용자 발화에서 나온 모델 출력이므로 근거와 같은 등급의 불신 대상이다:
build_prompt가 울타리 표식을 지우고 "참고용" 꼬리표를 달아 싣는다.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import log_event
from app.llm.base import ChatMessage, LLMProvider
from app.models.conversation import Conversation
from app.models.message import Message

logger = logging.getLogger("mopan.chat")

# 요약 상한. 한국어 1,500자 ≈ 1,000토큰 - HISTORY_RESERVE_TOKENS(1,500) 안에
# 최근 한 턴과 함께 들어가는 크기.
SUMMARY_MAX_CHARS = 1500
# 접을 새 메시지가 이보다 적으면 기다린다(두 턴마다 한 번 - 매 턴 부르면
# 요약 호출이 답변 호출만큼 잦아진다).
MIN_FOLD_MESSAGES = 4
# 접는 메시지 한 개의 상한. 요약기에 실리는 것은 줄거리지 본문이 아니다.
_MESSAGE_CHARS = 2000
_TIMEOUT_SECONDS = 60.0

_SYSTEM = (
    "You maintain a running summary of a conversation between a user and an assistant, "
    "in Korean. You are given the CURRENT summary (may be empty) and the NEW turns that "
    "happened after it. Write the UPDATED summary: merge the new turns into the current "
    "summary, keeping (1) the user's goal and constraints, (2) facts, names, numbers, dates "
    "and decisions established so far, (3) questions the assistant asked and how the user "
    "answered them, (4) anything still open or unresolved. Drop pleasantries and repetition. "
    "Do not invent anything that is not in the turns. Plain prose or short bullets, at most "
    f"{SUMMARY_MAX_CHARS} characters. Output the summary only - no preamble, no heading."
)

# 살아 있는 백그라운드 태스크의 참조. create_task의 결과를 잡아 두지 않으면 GC가
# 실행 중인 태스크를 거둘 수 있다(asyncio 문서의 명시된 함정).
_tasks: set[asyncio.Task] = set()
# 대화별로 지금 도는 갱신. 실측(2026-09-12): 6턴째 답이 오자마자 1초 뒤 보낸 7턴째가
# 요약 갱신(3~9초)보다 먼저 대화를 읽어 요약 0자로 답했다. 다음 턴은 제 대화의 갱신이
# 돌고 있으면 잠깐(상한 INFLIGHT_WAIT_SECONDS) 기다렸다가 읽는다 - 보통은 이미 끝나 있다.
_inflight: dict[uuid.UUID, asyncio.Task] = {}
INFLIGHT_WAIT_SECONDS = 10.0


def schedule_refresh(
    sessionmaker: async_sessionmaker[AsyncSession],
    llm_provider: LLMProvider,
    *,
    settings: Settings,
    conversation_id: uuid.UUID,
) -> None:
    """답변 저장 뒤에 부른다. 요청과 분리된 태스크라 클라이언트가 done 프레임을
    받고 연결을 끊어도 갱신은 끝까지 간다.

    ponytail: 프로세스가 죽으면 같이 죽는다 - 다음 턴이 이어받으므로 arq 큐에
    넣지 않는다. 요약 지연이 문제가 되면 그때 워커로 옮긴다."""
    task = asyncio.create_task(
        refresh_summary(sessionmaker, llm_provider, settings=settings, conversation_id=conversation_id)
    )
    _tasks.add(task)
    _inflight[conversation_id] = task
    task.add_done_callback(_tasks.discard)
    task.add_done_callback(lambda t, cid=conversation_id: _inflight.pop(cid, None) if _inflight.get(cid) is t else None)


async def await_inflight(conversation_id: uuid.UUID, timeout: float = INFLIGHT_WAIT_SECONDS) -> None:
    """이 대화의 요약 갱신이 돌고 있으면 끝날 때까지(상한까지) 기다린다. 없으면 즉시."""
    task = _inflight.get(conversation_id)
    if task is None or task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except Exception:  # TimeoutError 포함 - 어떤 이유든 기다림을 포기하고 그냥 읽는다
        return


def spawn(coro) -> asyncio.Task:
    """추적되는 백그라운드 태스크 - drain()이 기다리고 GC가 거두지 못한다."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain() -> None:
    """떠 있는 갱신을 전부 기다린다. 테스트의 정리와 프로세스 종료용 - 이벤트 루프가
    갱신 도중에 닫히면 그 세션의 연결이 풀에 반납되지 않은 채 '트랜잭션 중 유휴'로
    남고, 다음 TRUNCATE가 영원히 기다린다(실측: 전체 스위트가 여기서 멈췄다)."""
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def refresh_summary(
    sessionmaker: async_sessionmaker[AsyncSession],
    llm_provider: LLMProvider,
    *,
    settings: Settings,
    conversation_id: uuid.UUID,
) -> bool:
    """접은 것이 있으면 True. 실패는 전부 경고 로그 + False - 답변 경로는 이
    함수의 결과를 기다리지도, 보지도 않는다."""
    try:
        async with sessionmaker() as db:
            conversation = await db.get(Conversation, conversation_id)
            if conversation is None:
                return False
            # 요약이 아직 안 읽은 메시지 전부, 오래된 것부터. 원문 창(최근 N개)은
            # 요약이 절대 읽지 않으므로 항상 이 목록의 꼬리에 있다.
            # 열만 고른다: Message 엔티티는 attachments·feedback을 selectin으로 딸려
            # 읽고, 요약기는 역할·본문·시각 셋만 쓴다.
            query = select(Message.role, Message.content, Message.created_at).where(
                Message.conversation_id == conversation_id
            )
            if conversation.summary_through_at is not None:
                query = query.where(Message.created_at > conversation.summary_through_at)
            rows = list(await db.execute(query.order_by(Message.created_at.asc())))
            fold = rows[: max(0, len(rows) - settings.history_window_messages)]
            if len(fold) < MIN_FOLD_MESSAGES:
                return False
            updated = await _summarize(
                llm_provider,
                conversation.summary,
                fold,
                model=settings.conversation_summary_model or settings.query_expansion_model,
            )
            if not updated:
                return False
            conversation.summary = updated
            conversation.summary_through_at = fold[-1].created_at
            await db.commit()
            log_event(
                logger,
                "conversation_summarized",
                conversation_id=str(conversation_id),
                folded_messages=len(fold),
                summary_chars=len(updated),
            )
            return True
    except Exception:
        logger.warning("conversation summary refresh failed", exc_info=True)
        return False


async def _summarize(llm_provider: LLMProvider, current: str | None, fold: list, *, model: str) -> str | None:
    """`fold`의 항목은 role·content 속성만 있으면 된다(Row든 Message든)."""
    lines = [f"{m.role}: {(m.content or '')[:_MESSAGE_CHARS]}" for m in fold if m.role in ("user", "assistant")]
    if not lines:
        return None
    user = (
        f"CURRENT SUMMARY:\n{current or '(none)'}\n\n"
        f"NEW TURNS:\n" + "\n\n".join(lines) + "\n\nUPDATED SUMMARY:"
    )
    result = await asyncio.wait_for(
        llm_provider.chat(
            [ChatMessage(role="system", content=_SYSTEM), ChatMessage(role="user", content=user)],
            temperature=0.0,
            model=model,
        ),
        timeout=_TIMEOUT_SECONDS,
    )
    text = (result.content or "").strip()
    # 상한을 조금 넘긴 것은 자르고, 크게 넘긴 것은 요약이 아니라 베껴 쓴 것이므로 버린다.
    if not text or len(text) > SUMMARY_MAX_CHARS * 2:
        return None
    return text[:SUMMARY_MAX_CHARS]

