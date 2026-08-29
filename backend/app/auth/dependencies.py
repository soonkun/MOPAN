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
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    user_id = await get_session_user_id(redis, session_id)
    if not user_id:
        raise HTTPException(status_code=401, detail="세션이 만료되었습니다. 다시 로그인해 주세요.")

    try:
        parsed_user_id = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="세션이 올바르지 않습니다. 다시 로그인해 주세요."
        ) from exc

    user = await db.get(User, parsed_user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    if not user.is_active:
        # Belt as well as the braces in update_user, which revokes this user's
        # Redis sessions on deactivation. A session created before 0002 ran, or
        # one written by a process that has not been redeployed yet, would
        # otherwise stay valid for its full TTL. 401 rather than 403 on purpose:
        # frontend/lib/api.ts redirects to /login on 401 only, and there is
        # nothing this session can usefully do while it stays where it is.
        raise HTTPException(status_code=401, detail="비활성화된 계정입니다. 관리자에게 문의해 주세요.")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate for every write to the shared RAG corpus and for Slice 4/5 admin
    surfaces. Anyone who can upload can poison every other user's answers."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user
