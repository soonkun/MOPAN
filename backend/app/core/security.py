import secrets

import bcrypt
from redis.asyncio import Redis

SESSION_KEY_PREFIX = "session:"
MIN_PASSWORD_LENGTH = 8
# bcrypt silently TRUNCATES at 72 bytes (verified against bcrypt 4.2.0: hashpw of
# a 73-byte password succeeds, and checkpw then matches any longer string sharing
# the first 72 bytes). It does not raise. So this limit has to be enforced here -
# do not delete the check in hash_password believing the library covers it.
MAX_PASSWORD_BYTES = 72

# Pre-computed hash of a value nobody will submit, used to burn the same CPU on
# the "no such user" branch as on a real verification.
_DUMMY_HASH = bcrypt.hashpw(b"mopan-dummy-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def dummy_verify() -> None:
    """Call on the user-not-found path to avoid a response-time oracle."""
    bcrypt.checkpw(b"mopan-dummy-password", _DUMMY_HASH.encode())


async def create_session(redis: Redis, user_id: str, ttl_seconds: int) -> str:
    # TTL is a parameter, not a get_settings() read: that accessor is lru_cached and
    # would ignore the live Settings on app.state. Callers pass get_app_settings().
    session_id = secrets.token_urlsafe(32)
    await redis.set(f"{SESSION_KEY_PREFIX}{session_id}", user_id, ex=ttl_seconds)
    return session_id


async def get_session_user_id(redis: Redis, session_id: str) -> str | None:
    return await redis.get(f"{SESSION_KEY_PREFIX}{session_id}")


async def delete_session(redis: Redis, session_id: str) -> None:
    await redis.delete(f"{SESSION_KEY_PREFIX}{session_id}")
