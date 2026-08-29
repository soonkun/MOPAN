import fakeredis.aioredis
import pytest

from app.core.security import (
    MAX_PASSWORD_BYTES,
    SESSION_KEY_PREFIX,
    USER_SESSIONS_KEY_PREFIX,
    create_session,
    delete_session,
    dummy_verify,
    get_session_user_id,
    hash_password,
    revoke_user_sessions,
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
    session_id = await create_session(redis, "user-123", 3600)
    assert await get_session_user_id(redis, session_id) == "user-123"

    await delete_session(redis, session_id)
    assert await get_session_user_id(redis, session_id) is None
    await redis.aclose()


async def test_session_uses_the_ttl_it_was_given():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session_id = await create_session(redis, "user-123", 1234)
    # Exact TTL, not just > 0: a regression to a literal would still be "> 0".
    assert await redis.ttl(f"{SESSION_KEY_PREFIX}{session_id}") == 1234
    # The reverse index has to expire too, or a set of dead session ids outlives
    # every session it names.
    assert await redis.ttl(f"{USER_SESSIONS_KEY_PREFIX}user-123") == 1234
    await redis.aclose()


async def test_revoke_user_sessions_drops_every_session_of_one_user_only():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    # Two devices for the same account: revoking one cookie is not a deactivation.
    first = await create_session(redis, "user-123", 3600)
    second = await create_session(redis, "user-123", 3600)
    other = await create_session(redis, "user-456", 3600)

    assert await revoke_user_sessions(redis, "user-123") == 2
    assert await get_session_user_id(redis, first) is None
    assert await get_session_user_id(redis, second) is None
    assert await get_session_user_id(redis, other) == "user-456"
    assert await redis.exists(f"{USER_SESSIONS_KEY_PREFIX}user-123") == 0
    await redis.aclose()


async def test_revoke_user_sessions_is_a_no_op_for_a_user_with_none():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    # DELETE with no keys is a Redis error, not a no-op, so the empty case needs
    # its own branch.
    assert await revoke_user_sessions(redis, "never-logged-in") == 0
    await redis.aclose()
