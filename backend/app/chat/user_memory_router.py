"""본인 기억의 조회·삭제. 관리자도 남의 기억은 못 본다 - 경로에 user_id가 없다."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.chat.user_memory import MAX_ITEMS, load_user_memory
from app.core.db import get_db_session
from app.models.user import User
from app.models.user_memory import UserMemory

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryItem(BaseModel):
    id: uuid.UUID
    content: str
    source_conversation_id: uuid.UUID | None
    created_at: datetime


class MemoryList(BaseModel):
    items: list[MemoryItem]
    max_items: int


@router.get("", response_model=MemoryList)
async def list_memory(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    rows = await load_user_memory(db, user.id)
    return MemoryList(
        items=[
            MemoryItem(id=r.id, content=r.content, source_conversation_id=r.source_conversation_id, created_at=r.created_at)
            for r in rows
        ],
        max_items=MAX_ITEMS,
    )


@router.delete("/{memory_id}", status_code=204)
async def delete_one(memory_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(delete(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user.id))
    await db.commit()
    if result.rowcount == 0:
        # 남의 것이든 없는 것이든 같은 답: 존재 여부를 흘리지 않는다.
        raise HTTPException(status_code=404, detail="기억을 찾을 수 없습니다.")


@router.delete("", status_code=204)
async def delete_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    await db.execute(delete(UserMemory).where(UserMemory.user_id == user.id))
    await db.commit()
