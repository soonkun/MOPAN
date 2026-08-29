import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.core.db import get_db_session
from app.core.logging import log_event
from app.core.redis import get_redis
from app.core.security import revoke_user_sessions
from app.models.user import User
from app.schemas.auth import AdminUserResponse, UserUpdate

logger = logging.getLogger("mopan.users")
router = APIRouter(prefix="/api", tags=["users"])

USER_NOT_FOUND_MESSAGE = "사용자를 찾을 수 없습니다."
LAST_ADMIN_MESSAGE = "마지막 관리자입니다. 다른 사용자를 관리자로 지정한 뒤에 변경해 주세요."
SELF_ROLE_MESSAGE = "자신의 권한은 변경할 수 없습니다. 다른 관리자에게 요청해 주세요."
SELF_DEACTIVATE_MESSAGE = "자신의 계정은 비활성화할 수 없습니다."


@router.get("/users", response_model=list[AdminUserResponse])
async def list_users(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.scalars(select(User).order_by(User.created_at))
    return list(result)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND_MESSAGE)

    # exclude_none as well as exclude_unset: both columns are NOT NULL, so an
    # explicit null can only mean "no change" (see UserUpdate).
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    role = changes.get("role", user.role)
    is_active = changes.get("is_active", user.is_active)

    if user.role == "admin" and user.is_active and (role != "admin" or not is_active):
        # FOR UPDATE, and re-read under the lock: two admins demoting each other
        # at the same instant would otherwise both see a count of two, both
        # commit, and leave the system with no administrator at all - a state
        # nothing in the UI can undo. Postgres re-evaluates the WHERE clause
        # after taking each row lock, so the loser of the race sees the count the
        # winner left behind.
        #
        # This runs BEFORE the self-checks below deliberately. The only way to
        # reach it is a self-demotion (any OTHER active admin target implies the
        # acting admin is a second one), and "you are the last admin, promote
        # someone else first" names a cause the admin can act on, where "you
        # cannot change yourself" would leave them looking for another admin who
        # does not exist.
        active_admin_ids = set(
            (
                await db.scalars(
                    select(User.id)
                    .where(User.role == "admin", User.is_active.is_(True))
                    .with_for_update()
                )
            ).all()
        )
        if user.id in active_admin_ids and len(active_admin_ids) <= 1:
            raise HTTPException(status_code=409, detail=LAST_ADMIN_MESSAGE)

    if user.id == admin.id:
        if role != user.role:
            raise HTTPException(status_code=409, detail=SELF_ROLE_MESSAGE)
        if is_active != user.is_active:
            raise HTTPException(status_code=409, detail=SELF_DEACTIVATE_MESSAGE)

    for field, value in changes.items():
        setattr(user, field, value)
    await db.commit()

    if not user.is_active:
        # After the commit, so a rolled-back transaction cannot log a user out for
        # a change that never happened. A deactivated account holding a live
        # session cookie is not deactivated - see revoke_user_sessions.
        revoked = await revoke_user_sessions(redis, str(user.id))
        log_event(logger, "user_deactivated", user_id=str(user.id), sessions_revoked=revoked)

    await db.refresh(user)
    return user
