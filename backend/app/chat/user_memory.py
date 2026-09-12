"""대화를 넘어 남는 사용자별 기억 - ChatGPT '메모리'와 같은 모양(app/models/user_memory.py).

멀티턴 기억(memory.py)은 대화 하나 안에서만 산다. 여기서는 매 턴 뒤 값싼 모델이 그 턴에서
사용자에 관해 새로 드러난 사실(누구인지, 무슨 일·프로젝트를 하는지, 어떻게 답받길 원하는지,
진행 중인 일의 이름·날짜)만 한 줄씩 뽑아 계정에 붙인다. 질문 자체·문서 내용·어시스턴트의
답은 기억이 아니다. 모든 대화의 답변 시스템 프롬프트에 "[About the user]"로 실린다.

경계: 계정(user_id) 안에서만 읽고 쓴다. 본인이 /api/memory에서 보고 지운다. 항목 수·글자
수 상한이 있고, 이미 있는 사실은 다시 넣지 않는다. 실패는 전부 "기억 그대로" - 답변 경로는
결과를 보지 않는다(memory.py와 같은 계약).
"""

import logging
import re
import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat import memory
from app.core.config import Settings
from app.core.logging import log_event
from app.llm.base import ChatMessage, LLMProvider
from app.models.user_memory import USER_MEMORY_CHARS, UserMemory

logger = logging.getLogger("mopan.chat")

MAX_ITEMS = 50          # 계정당. 넘치면 오래된 것부터 지운다.
MAX_NEW_PER_TURN = 4    # 한 턴에서 뽑는 상한 - 그 이상은 사실이 아니라 요약이다.
INJECT_CHARS = 2000     # 프롬프트에 싣는 총 글자 상한(최신 것부터).
_ANSWER_CHARS = 400     # 추출기에 보여 주는 답변 앞부분 - 되묻기에 대한 답을 해석할 맥락.
_TIMEOUT_SECONDS = 30.0

_SYSTEM = (
    "You extract durable facts ABOUT THE USER from one turn of a conversation, for a memory that "
    "persists across future conversations. Keep only facts that will still matter next week: who the "
    "user is (role, organization), what they are working on (project/product names, deadlines, "
    "dates), stable preferences about how they want answers, and constraints they stated. "
    "Do NOT record: the question itself, document contents, the assistant's answer, greetings, "
    "one-off requests, or anything already in CURRENT MEMORY (same fact in other words counts as "
    "already there). Write each fact as one short Korean sentence in the third person (\"사용자는 "
    "...\"), one per line, no bullets, no numbering, at most "
    f"{MAX_NEW_PER_TURN} lines, each under {USER_MEMORY_CHARS // 2} characters. If there is nothing "
    "new worth remembering, reply with exactly: none"
)


async def load_user_memory(db: AsyncSession, user_id: uuid.UUID) -> list[UserMemory]:
    """본인 것만, 오래된 순. 라우터·추출기 둘 다 이 한 함수로 읽어 경계가 한 곳이다."""
    rows = await db.scalars(
        select(UserMemory).where(UserMemory.user_id == user_id).order_by(UserMemory.created_at.asc(), UserMemory.id)
    )
    return list(rows)


def memory_lines(rows: list[UserMemory]) -> list[str]:
    """프롬프트에 실을 줄들 - 최신 것부터 INJECT_CHARS 안에서, 원래 순서로 되돌려."""
    picked: list[str] = []
    used = 0
    for row in reversed(rows):
        if used + len(row.content) > INJECT_CHARS:
            break
        picked.append(row.content)
        used += len(row.content)
    return list(reversed(picked))


def schedule_extract(
    sessionmaker: async_sessionmaker[AsyncSession],
    llm_provider: LLMProvider,
    *,
    settings: Settings,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    question: str,
    answer: str,
) -> None:
    memory.spawn(
        extract_user_memory(
            sessionmaker, llm_provider, settings=settings, user_id=user_id,
            conversation_id=conversation_id, question=question, answer=answer,
        )
    )


async def extract_user_memory(
    sessionmaker: async_sessionmaker[AsyncSession],
    llm_provider: LLMProvider,
    *,
    settings: Settings,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    question: str,
    answer: str,
) -> int:
    """붙인 항목 수. 실패는 경고 로그 + 0."""
    try:
        async with sessionmaker() as db:
            existing = await load_user_memory(db, user_id)
            new = await propose(
                llm_provider,
                [r.content for r in existing],
                question,
                answer,
                model=settings.user_memory_model or settings.conversation_summary_model or settings.query_expansion_model,
            )
            if not new:
                return 0
            for content in new:
                db.add(UserMemory(user_id=user_id, content=content, source_conversation_id=conversation_id))
            await db.flush()
            # 상한 초과분은 오래된 것부터. 한 문장으로: 이 사용자의 행 중 최신 MAX_ITEMS개 밖의 것.
            keep = select(UserMemory.id).where(UserMemory.user_id == user_id).order_by(
                UserMemory.created_at.desc(), UserMemory.id.desc()
            ).limit(MAX_ITEMS)
            await db.execute(delete(UserMemory).where(UserMemory.user_id == user_id, UserMemory.id.not_in(keep)))
            await db.commit()
            log_event(logger, "user_memory_added", user_id=str(user_id), added=len(new))
            return len(new)
    except Exception:
        logger.warning("user memory extraction failed", exc_info=True)
        return 0


async def propose(
    llm_provider: LLMProvider, existing: list[str], question: str, answer: str, *, model: str
) -> list[str]:
    """모델에게 새 사실만 묻고, 형식·중복·상한을 여기서 다시 거른다 - 모델의 약속은 믿지 않는다."""
    import asyncio

    user = (
        "CURRENT MEMORY:\n" + ("\n".join(existing) if existing else "(none)") + "\n\n"
        f"USER SAID:\n{question}\n\n"
        f"ASSISTANT REPLIED (context only, first part):\n{answer[:_ANSWER_CHARS]}\n\n"
        "NEW FACTS ABOUT THE USER:"
    )
    result = await asyncio.wait_for(
        llm_provider.chat(
            [ChatMessage(role="system", content=_SYSTEM), ChatMessage(role="user", content=user)],
            temperature=0.0,
            model=model,
        ),
        timeout=_TIMEOUT_SECONDS,
    )
    return parse_facts(result.content or "", existing)


def _norm(text: str) -> str:
    return re.sub(r"[\s.。!?]+", "", text).lower()


def parse_facts(raw: str, existing: list[str]) -> list[str]:
    text = raw.strip()
    if not text or text.lower() == "none":
        return []
    seen = {_norm(e) for e in existing}
    out: list[str] = []
    for line in text.splitlines():
        fact = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip('"')
        if not fact or fact.lower() == "none" or len(fact) > USER_MEMORY_CHARS:
            continue
        key = _norm(fact)
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
        if len(out) >= MAX_NEW_PER_TURN:
            break
    return out


async def count_for(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(await db.scalar(select(func.count(UserMemory.id)).where(UserMemory.user_id == user_id)) or 0)
