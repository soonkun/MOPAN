import fakeredis.aioredis
import pytest

from app.core.config import get_settings
from app.core.security import (
    MAX_PASSWORD_BYTES,
    SESSION_KEY_PREFIX,
    create_session,
    delete_session,
    dummy_verify,
    get_session_user_id,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password():
    hashed = hash_password("correct-horse")
    assert hashed != "correct-horse"
    assert verify_password("correct-horse", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_verify_password_returns_false_for_a_corrupt_hash():
    # Must not raise: a malformed stored hash is a 401, not a 500.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_hash_password_rejects_passwords_over_the_bcrypt_limit():
    with pytest.raises(ValueError):
        hash_password("a" * (MAX_PASSWORD_BYTES + 1))


def test_dummy_verify_runs_without_error():
    # Used on the "user not found" path so login timing does not reveal which
    # email addresses exist.
    dummy_verify()


async def test_session_lifecycle():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session_id = await create_session(redis, "user-123")
    assert await get_session_user_id(redis, session_id) == "user-123"

    await delete_session(redis, session_id)
    assert await get_session_user_id(redis, session_id) is None
    await redis.aclose()


async def test_session_has_a_ttl():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session_id = await create_session(redis, "user-123")
    # Exact TTL, not just > 0: a regression to ex=1 would still be "> 0".
    assert await redis.ttl(f"{SESSION_KEY_PREFIX}{session_id}") == get_settings().session_ttl_seconds
    await redis.aclose()
