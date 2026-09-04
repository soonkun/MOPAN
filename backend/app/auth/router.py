import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from redis.asyncio import Redis
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import SESSION_COOKIE_NAME, get_current_user
from app.auth.service import AuthError, authenticate_user, register_user
from app.core.security import hash_password, verify_password
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.redis import get_redis
from app.core.security import create_session, delete_session
from app.models.conversation import Conversation
from app.models.user import User
from app.schemas.auth import (
    DeleteAccountRequest,
    PasswordChangeRequest,
    LoginRequest,
    ProfileUpdateRequest,
    RegisterRequest,
    UserResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    try:
        return await register_user(db, settings, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_app_settings),
):
    try:
        user = await authenticate_user(db, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.") from exc

    session_id = await create_session(redis, str(user.id), settings.session_ttl_seconds)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        # The browser reaches the API through the Next.js same-origin proxy, so
        # Lax is correct even behind a Cloudflare Tunnel.
        samesite="lax",
        secure=settings.environment == "production",
        path="/",
    )
    return user


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    redis: Redis = Depends(get_redis),
):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        # Actually revoke server-side. Clearing the cookie alone leaves a valid
        # session id usable by anyone who captured it.
        await delete_session(redis, session_id)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserResponse)
async def update_me(
    payload: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """본인 프로필. 지금은 닉네임 하나 - 새 대화 화면과 잡담 응답이 "OO님"
    이라고 부를 때 쓰는 호칭이다. 생략된 키는 건드리지 않는다(PATCH)."""
    if payload.nickname is not None:
        nickname = payload.nickname.strip()
        if len(nickname) > 60:
            raise HTTPException(status_code=400, detail="닉네임은 60자 이하여야 합니다.")
        user.nickname = nickname or None
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/me/password", status_code=204)
async def change_password(
    payload: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """본인 비밀번호 변경. 새 비밀번호의 규칙(길이·바이트 한도)은 가입과 같은
    스키마가 지킨다 - 두 벌의 규칙은 반드시 어긋난다."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=403, detail="현재 비밀번호가 올바르지 않습니다.")
    user.password_hash = hash_password(payload.new_password)
    await db.commit()


@router.delete("/me", status_code=204)
async def delete_me(
    payload: DeleteAccountRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
):
    """계정 삭제 - 정확히는 비활성화 + 익명화.

    행을 DELETE하지 않는 이유는 User 모델이 스스로 적고 있다:
    documents.uploaded_by와 collections.created_by가 ON DELETE RESTRICT라,
    행 삭제는 실패하거나 공유 코퍼스를 함께 지운다. 그래서 이 계정이 만든
    문서·분류·워크플로우는 남고(화면에는 만든 사람이 비워져 보인다), 개인의
    것 - 대화 이력, 이메일, 호칭, 로그인 능력 - 만 지워진다. 프런트의 확인
    창이 같은 문장을 말한다.

    비밀번호를 다시 받는 이유: 파괴적 동작은 세션 쿠키만으로 충분하지 않다.
    마지막 활성 관리자는 거부한다 - 관리자가 0명인 배포는 아무도 사용자를
    만들거나 살릴 수 없는, 잠긴 집이다.
    """
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=403, detail="비밀번호가 올바르지 않습니다.")
    if user.role == "admin":
        others = await db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True), User.id != user.id)
        )
        if not others:
            raise HTTPException(
                status_code=400,
                detail="마지막 관리자 계정은 삭제할 수 없습니다. 먼저 다른 관리자를 지정하세요.",
            )

    # 대화는 개인의 것이라 지운다. 메시지·추적은 대화에 CASCADE로 딸려 있다.
    await db.execute(sa_delete(Conversation).where(Conversation.user_id == user.id))
    # 익명화. 이메일은 유니크·소문자 제약을 지키는 자리표시자로 바꾸고,
    # 비밀번호는 아무도 모르는 값의 해시가 된다 - 로그인 경로는 is_active로
    # 이미 막혀 있지만, 자물쇠는 두 개가 낫다.
    user.email = f"deleted-{user.id.hex}@deleted.invalid"
    user.nickname = None
    user.password_hash = hash_password(uuid.uuid4().hex)
    user.is_active = False
    await db.commit()

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id:
        await delete_session(redis, session_id)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
