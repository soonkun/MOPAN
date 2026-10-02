import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import log_event
from app.core.security import dummy_verify, hash_password, verify_password
from app.models.collection import Collection
from app.models.user import User

logger = logging.getLogger("mopan.auth")

DEFAULT_COLLECTION_NAME = "일반"


class AuthError(Exception):
    """Raised for any failed registration or authentication. The message is
    intentionally generic - specific reasons leak account existence."""


async def register_user(db: AsyncSession, settings: Settings, email: str, password: str) -> User:
    email = email.strip().lower()
    user_count = await db.scalar(select(func.count()).select_from(User)) or 0

    # Outside production the first account bootstraps the system: it becomes admin
    # and gets a default collection, so `docker compose up` -> open browser ->
    # register works with no seeding step. In production that would be a land-grab -
    # an unauthenticated endpoint handing admin over the shared RAG corpus to
    # whoever POSTs first - so there the admin must come from scripts/create_admin.py.
    is_first_user = user_count == 0 and settings.environment != "production"
    if not is_first_user and not settings.allow_self_registration:
        raise AuthError(
            "회원가입이 닫혀 있습니다. 관리자에게 계정을 요청해 주세요(관리 → 사용자 → 사용자 추가)."
        )

    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        # Says nothing about the address: "already registered" hands an account
        # enumeration oracle to anyone who can POST a guess. It is deliberately
        # NOT the disabled-registration message above either - that one names a
        # real, checkable cause, and reusing it here would name a false one.
        # 실사고 2026-09-28: 이미 가입한 사람이 다시 가입하다 이 문구를 "가입이 막혔다"로 읽었다. 로그인으로 안내하는 꼬리는
        # 존재 여부를 새로 말하지 않는다(모든 실패에 붙는 일반 안내).
        log_event(logger, "register_duplicate_email")
        raise AuthError("회원가입을 완료하지 못했습니다. 이미 가입한 이메일이라면 로그인 화면에서 로그인해 주세요.")

    user = User(
        email=email,
        password_hash=hash_password(password),
        role="admin" if is_first_user else "user",
    )
    db.add(user)
    await db.flush()

    if is_first_user:
        db.add(Collection(name=DEFAULT_COLLECTION_NAME, created_by=user.id))

    await db.commit()
    log_event(logger, "user_registered", user_id=str(user.id), role=user.role)

    if is_first_user:
        # 부팅 시딩(app/main.py lifespan)은 관리자가 없으면 물러난다. 첫 관리자가
        # 지금 생겼으니 동봉 MCP를 여기서 등록한다 - 실패해도 가입은 성공이다.
        from app.mcp.seed import seed_bundled_servers

        await seed_bundled_servers(db, settings)

    # refresh는 시딩 뒤에: 시딩이 실패하면 안에서 rollback하는데, 그 rollback이
    # 방금 커밋된 user를 expire시켜 응답 직렬화가 세션 밖 lazy IO로 터진다
    # (MissingGreenlet 실사고). 여기서 다시 읽으면 어느 경로든 살아 있는 값이다.
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    email = email.strip().lower()
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        dummy_verify()  # equalise response time with the "wrong password" path
        raise AuthError("invalid credentials")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid credentials")
    if not user.is_active:
        # Checked AFTER the password so the bcrypt cost is paid on this branch too,
        # and raised as the same AuthError the router renders as "이메일 또는
        # 비밀번호가 올바르지 않습니다.": a distinct "deactivated" message would
        # confirm to anyone guessing that the address is registered here.
        log_event(logger, "login_rejected_inactive", user_id=str(user.id))
        raise AuthError("inactive account")
    return user
