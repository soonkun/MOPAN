import uuid

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.redis import get_redis
from app.core.security import get_session_user_id
from app.models.user import User

SESSION_COOKIE_NAME = "mopan_session"


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> User:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="not authenticated")

    user_id = await get_session_user_id(redis, session_id)
    if not user_id:
        raise HTTPException(status_code=401, detail="session expired")

    try:
        parsed_user_id = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="invalid session") from exc

    user = await db.get(User, parsed_user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate for every write to the shared RAG corpus and for Slice 4/5 admin
    surfaces. Anyone who can upload can poison every other user's answers."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user
