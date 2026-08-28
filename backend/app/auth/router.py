from fastapi import APIRouter, Depends, HTTPException, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import SESSION_COOKIE_NAME, get_current_user
from app.auth.service import AuthError, authenticate_user, register_user
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.redis import get_redis
from app.core.security import create_session, delete_session
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest, UserResponse

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
        raise HTTPException(status_code=401, detail="invalid credentials") from exc

    session_id = await create_session(redis, str(user.id))
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
