import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.auth.authorization import get_owned_conversation, get_readable_document
from app.auth.dependencies import require_admin
from app.core.security import SESSION_KEY_PREFIX, hash_password
from app.models.conversation import Conversation
from app.models.user import User


@pytest_asyncio.fixture
async def admin_client(client):
    """The first registered user is the bootstrap admin."""
    registered = await client.post(
        "/api/auth/register", json={"email": "admin@example.com", "password": "pw123456"}
    )
    assert registered.status_code == 200
    logged_in = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "pw123456"}
    )
    assert logged_in.status_code == 200
    return client


async def test_first_user_becomes_admin(client):
    response = await client.post(
        "/api/auth/register", json={"email": "first@example.com", "password": "pw123456"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


async def test_second_user_is_a_plain_user(admin_client):
    response = await admin_client.post(
        "/api/auth/register", json={"email": "second@example.com", "password": "pw123456"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "user"


async def test_register_login_me_logout(client):
    await client.post("/api/auth/register", json={"email": "a@example.com", "password": "pw123456"})
    login = await client.post("/api/auth/login", json={"email": "a@example.com", "password": "pw123456"})
    assert login.status_code == 200
    assert "mopan_session" in login.cookies

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"

    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_deletes_the_redis_session(client, fake_redis):
    await client.post("/api/auth/register", json={"email": "b@example.com", "password": "pw123456"})
    login = await client.post("/api/auth/login", json={"email": "b@example.com", "password": "pw123456"})
    session_id = login.cookies["mopan_session"]
    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is not None

    await client.post("/api/auth/logout")
    # Re-read the key: clearing the cookie alone would leave this session valid.
    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is None


async def test_email_is_case_insensitive(client):
    await client.post("/api/auth/register", json={"email": "Mixed@Example.COM", "password": "pw123456"})
    login = await client.post("/api/auth/login", json={"email": "mixed@example.com", "password": "pw123456"})
    assert login.status_code == 200


async def test_duplicate_registration_does_not_confirm_the_account_exists(client):
    await client.post("/api/auth/register", json={"email": "c@example.com", "password": "pw123456"})
    duplicate = await client.post(
        "/api/auth/register", json={"email": "c@example.com", "password": "pw123456"}
    )
    assert duplicate.status_code == 400
    assert "already" not in duplicate.json()["detail"].lower()


async def test_short_password_is_rejected(client):
    response = await client.post("/api/auth/register", json={"email": "d@example.com", "password": "short"})
    assert response.status_code == 422


async def test_long_password_is_rejected_not_a_500(client):
    response = await client.post("/api/auth/register", json={"email": "e@example.com", "password": "a" * 200})
    assert response.status_code == 422


async def test_multibyte_password_over_72_bytes_is_422_not_500(client):
    # 72 characters, 216 bytes. Pydantic max_length counts CHARACTERS, so a
    # character limit lets this through and hash_password raises -> 500.
    password = "가" * 72
    assert len(password) <= 72 < len(password.encode("utf-8"))
    response = await client.post("/api/auth/register", json={"email": "g@example.com", "password": password})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "email,password",
    [
        ("h@example.com", "sh0rtpw"),  # too short
        ("h@example.com", "가" * 72),  # over 72 bytes
        ("not-an-email", "Zq7-marker-Pw!"),  # invalid email, valid password
        ("h@example.com", "Zq7-marker-Pw!" + "x" * 200),  # over 72 bytes, ascii
    ],
)
async def test_validation_errors_do_not_echo_the_password(client, email, password):
    # FastAPI's default handler returns the rejected value under "input"; on this
    # route that is the plaintext password.
    response = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 422
    assert password not in response.text


async def test_malformed_json_does_not_echo_the_password(client):
    # The raw body is the "input" for a JSON decode error, so it carries the password.
    secret = "Zq7-marker-Pw!"
    response = await client.post(
        "/api/auth/register",
        content=f'{{"email": "h@example.com", "password": "{secret}"',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert secret not in response.text


async def test_me_requires_auth(client):
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_wrong_password(client):
    await client.post("/api/auth/register", json={"email": "f@example.com", "password": "pw123456"})
    response = await client.post("/api/auth/login", json={"email": "f@example.com", "password": "nope"})
    assert response.status_code == 401


async def test_login_unknown_email_matches_the_wrong_password_response(client):
    # Exercises the dummy_verify() branch. Identical body to the wrong-password
    # case, so the response reveals nothing about which emails exist.
    response = await client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "nope"})
    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}


async def test_self_registration_can_be_disabled(app, client):
    """Settings must come from app.state.settings, not the lru_cached get_settings()."""
    await client.post("/api/auth/register", json={"email": "i@example.com", "password": "pw123456"})
    app.state.settings = app.state.settings.model_copy(update={"allow_self_registration": False})
    blocked = await client.post("/api/auth/register", json={"email": "j@example.com", "password": "pw123456"})
    assert blocked.status_code == 400


async def test_production_refuses_to_bootstrap_an_admin_by_registration(app, client):
    """In production /api/auth/register must not hand admin to whoever POSTs first -
    the admin comes from scripts/create_admin.py."""
    app.state.settings = app.state.settings.model_copy(
        update={"environment": "production", "allow_self_registration": False}
    )
    response = await client.post(
        "/api/auth/register", json={"email": "landgrab@example.com", "password": "pw123456"}
    )
    assert response.status_code == 400


async def test_require_admin_rejects_a_plain_user():
    plain = User(email="plain@example.com", password_hash="x", role="user")
    with pytest.raises(HTTPException) as exc:
        await require_admin(plain)
    assert exc.value.status_code == 403

    admin = User(email="admin@example.com", password_hash="x", role="admin")
    assert await require_admin(admin) is admin


async def test_conversation_of_another_user_is_404_not_403(db):
    owner = User(email="owner@example.com", password_hash=hash_password("pw123456"))
    other = User(email="other@example.com", password_hash=hash_password("pw123456"))
    db.add_all([owner, other])
    await db.flush()
    conversation = Conversation(user_id=owner.id)
    db.add(conversation)
    await db.commit()

    assert (await get_owned_conversation(db, conversation.id, owner)).id == conversation.id

    with pytest.raises(HTTPException) as not_owned:
        await get_owned_conversation(db, conversation.id, other)
    assert not_owned.value.status_code == 404

    with pytest.raises(HTTPException) as missing:
        await get_owned_conversation(db, uuid.uuid4(), owner)
    assert missing.value.status_code == 404


async def test_missing_document_is_404(db):
    with pytest.raises(HTTPException) as exc:
        await get_readable_document(db, uuid.uuid4())
    assert exc.value.status_code == 404
