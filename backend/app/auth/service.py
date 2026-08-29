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
        raise AuthError("회원가입이 비활성화되어 있습니다.")

    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        # Says nothing about the address: "already registered" hands an account
        # enumeration oracle to anyone who can POST a guess. It is deliberately
        # NOT the disabled-registration message above either - that one names a
        # real, checkable cause, and reusing it here would name a false one.
        log_event(logger, "register_duplicate_email")
        raise AuthError("회원가입을 완료하지 못했습니다.")

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
    await db.refresh(user)
    log_event(logger, "user_registered", user_id=str(user.id), role=user.role)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    email = email.strip().lower()
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        dummy_verify()  # equalise response time with the "wrong password" path
        raise AuthError("invalid credentials")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid credentials")
    return user
