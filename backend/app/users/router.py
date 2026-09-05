import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.core.db import get_db_session
from app.core.logging import log_event
from app.core.redis import get_redis
from app.core.security import hash_password, revoke_user_sessions
from app.models.user import User
from app.schemas.auth import (
    AdminPasswordResetResponse,
    AdminUserCreateRequest,
    AdminUserCreateResponse,
    AdminUserResponse,
    UserUpdate,
)

logger = logging.getLogger("mopan.users")
router = APIRouter(prefix="/api", tags=["users"])

USER_NOT_FOUND_MESSAGE = "사용자를 찾을 수 없습니다."
LAST_ADMIN_MESSAGE = "마지막 관리자입니다. 다른 사용자를 관리자로 지정한 뒤에 변경해 주세요."
SELF_ROLE_MESSAGE = "자신의 권한은 변경할 수 없습니다. 다른 관리자에게 요청해 주세요."
SELF_DEACTIVATE_MESSAGE = "자신의 계정은 비활성화할 수 없습니다."
SELF_RESET_MESSAGE = "자신의 비밀번호는 계정 설정에서 변경해 주세요."
# 가입 화면과 달리 정직하게 말한다: 관리자는 어차피 사용자 목록을 보므로
# "이미 있다"가 계정 존재를 새로 알려주는 오라클이 아니다.
DUPLICATE_EMAIL_MESSAGE = "이미 등록된 이메일입니다."


@router.post("/users", response_model=AdminUserCreateResponse, status_code=201)
async def create_user(
    payload: AdminUserCreateRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """관리자 초대. 공개 터널을 열고 자가가입을 끈 배포(모바일 가입 화면이
    "회원가입이 비활성화되어 있습니다"로 끝나던 실사고)에서 계정이 생기는
    유일한 길이다. 임시 비밀번호는 재설정과 같은 계약: 응답에 딱 한 번."""
    existing = await db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=409, detail=DUPLICATE_EMAIL_MESSAGE)

    temporary = secrets.token_urlsafe(9)
    user = User(
        email=payload.email,
        password_hash=hash_password(temporary),
        role=payload.role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    log_event(
        logger, "user_created_by_admin", user_id=str(user.id), by=str(admin.id), role=user.role
    )
    return AdminUserCreateResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        nickname=user.nickname,
        is_active=user.is_active,
        created_at=user.created_at,
        temporary_password=temporary,
    )


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


@router.post("/users/{user_id}/password", response_model=AdminPasswordResetResponse)
async def reset_password(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
):
    """임시 비밀번호 발급. 이 시스템에는 이메일 발송이 없어 "비밀번호 찾기"가
    있을 수 없고, 비밀번호를 잊은 사용자의 유일한 출구가 관리자다.

    임시 비밀번호는 서버가 만들고 응답에 딱 한 번 실린다 - 저장은 해시뿐이라
    다시 보여줄 방법이 없고, 그래서 관리자가 아무 때나 남의 비밀번호를 "조회"
    하는 창구가 되지 못한다. 관리자가 임시값을 아는 것은 어쩔 수 없으므로
    사용자는 로그인 뒤 계정 설정에서 바로 바꾸는 것이 맞고, 화면이 그렇게
    안내한다. 자신의 것은 거절 - 계정 설정이 정도(正道)다."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND_MESSAGE)
    if user.id == admin.id:
        raise HTTPException(status_code=409, detail=SELF_RESET_MESSAGE)

    temporary = secrets.token_urlsafe(9)  # 12자, 가입 규칙(8자 이상)을 넉넉히 넘는다
    user.password_hash = hash_password(temporary)
    await db.commit()

    # 커밋 뒤에: 되돌아간 트랜잭션이 멀쩡한 세션을 끊으면 안 된다. 옛 비밀번호를
    # 아는 사람(도난 포함)의 세션이 살아 있으면 재설정이 재설정이 아니다.
    revoked = await revoke_user_sessions(redis, str(user.id))
    log_event(
        logger,
        "user_password_reset",
        user_id=str(user.id),
        by=str(admin.id),
        sessions_revoked=revoked,
    )
    return AdminPasswordResetResponse(temporary_password=temporary)
