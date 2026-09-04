import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.auth.authorization import get_owned_conversation, get_readable_document
from app.auth.dependencies import require_admin
from app.core.security import SESSION_KEY_PREFIX, hash_password
from app.models.conversation import Conversation
from app.models.user import User

MISSING_ID = uuid.uuid4()


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
    assert duplicate.json()["detail"] == "회원가입을 완료하지 못했습니다."


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
    assert response.json() == {"detail": "이메일 또는 비밀번호가 올바르지 않습니다."}


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


# --- user management (GET /api/users, PATCH /api/users/{id}) -------------------


async def _user_id(admin_client, email: str) -> str:
    listing = await admin_client.get("/api/users")
    assert listing.status_code == 200
    return next(u["id"] for u in listing.json() if u["email"] == email)


@pytest_asyncio.fixture
async def member_client(admin_client, app):
    """A second, non-admin account on its own cookie jar."""
    await admin_client.post(
        "/api/auth/register", json={"email": "member@example.com", "password": "pw123456"}
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post("/api/auth/login", json={"email": "member@example.com", "password": "pw123456"})
        yield ac


@pytest_asyncio.fixture
async def two_admins(admin_client):
    """admin@example.com plus a promoted second@example.com, so the self-guards
    are reachable without the last-admin guard firing first."""
    await admin_client.post(
        "/api/auth/register", json={"email": "second@example.com", "password": "pw123456"}
    )
    second_id = await _user_id(admin_client, "second@example.com")
    promoted = await admin_client.patch(f"/api/users/{second_id}", json={"role": "admin"})
    assert promoted.status_code == 200
    return admin_client


async def test_list_users_returns_the_admin_fields_sorted_by_created_at(admin_client):
    await admin_client.post(
        "/api/auth/register", json={"email": "later@example.com", "password": "pw123456"}
    )
    response = await admin_client.get("/api/users")
    assert response.status_code == 200
    body = response.json()
    assert [u["email"] for u in body] == ["admin@example.com", "later@example.com"]
    assert body[0]["role"] == "admin"
    assert body[1]["role"] == "user"
    assert all(u["is_active"] is True for u in body)
    assert set(body[0]) == {"id", "email", "role", "nickname", "is_active", "created_at"}


async def test_user_management_is_admin_only(member_client, admin_client):
    member_id = await _user_id(admin_client, "member@example.com")
    assert (await member_client.get("/api/users")).status_code == 403
    patched = await member_client.patch(f"/api/users/{member_id}", json={"role": "admin"})
    assert patched.status_code == 403
    # The refusal must be real, not cosmetic.
    assert (await admin_client.get("/api/users")).json()[1]["role"] == "user"


async def test_unknown_user_id_is_404(admin_client):
    response = await admin_client.patch(f"/api/users/{MISSING_ID}", json={"role": "admin"})
    assert response.status_code == 404
    assert response.json()["detail"] == "사용자를 찾을 수 없습니다."


async def test_admin_can_promote_and_demote_another_user(two_admins):
    second_id = await _user_id(two_admins, "second@example.com")
    demoted = await two_admins.patch(f"/api/users/{second_id}", json={"role": "user"})
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "user"
    assert demoted.json()["is_active"] is True


async def test_admin_cannot_demote_themselves(two_admins):
    """Two admins exist, so this is the self-guard and not the last-admin guard."""
    admin_id = await _user_id(two_admins, "admin@example.com")
    response = await two_admins.patch(f"/api/users/{admin_id}", json={"role": "user"})
    assert response.status_code == 409
    assert response.json()["detail"] == "자신의 권한은 변경할 수 없습니다. 다른 관리자에게 요청해 주세요."
    assert (await two_admins.get("/api/auth/me")).json()["role"] == "admin"


async def test_admin_cannot_deactivate_themselves(two_admins):
    admin_id = await _user_id(two_admins, "admin@example.com")
    response = await two_admins.patch(f"/api/users/{admin_id}", json={"is_active": False})
    assert response.status_code == 409
    assert response.json()["detail"] == "자신의 계정은 비활성화할 수 없습니다."
    assert (await two_admins.get("/api/auth/me")).status_code == 200


@pytest.mark.parametrize("change", [{"role": "user"}, {"is_active": False}])
async def test_the_last_active_admin_cannot_lose_admin(admin_client, change):
    """Only one admin exists here. The check runs before the self-guard because
    "promote someone else first" is the actionable cause."""
    admin_id = await _user_id(admin_client, "admin@example.com")
    response = await admin_client.patch(f"/api/users/{admin_id}", json=change)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "마지막 관리자입니다. 다른 사용자를 관리자로 지정한 뒤에 변경해 주세요."
    )
    assert (await admin_client.get("/api/auth/me")).json()["role"] == "admin"


async def test_a_deactivated_admin_does_not_count_towards_the_last_admin_check(two_admins):
    """Two admin rows, one inactive, is still ONE admin - counting rows by role
    alone would let the remaining one demote themselves."""
    second_id = await _user_id(two_admins, "second@example.com")
    deactivated = await two_admins.patch(f"/api/users/{second_id}", json={"is_active": False})
    assert deactivated.status_code == 200

    admin_id = await _user_id(two_admins, "admin@example.com")
    response = await two_admins.patch(f"/api/users/{admin_id}", json={"role": "user"})
    assert response.status_code == 409
    assert response.json()["detail"].startswith("마지막 관리자입니다.")


async def test_deactivating_a_user_kills_their_live_session(member_client, admin_client, fake_redis):
    member_id = await _user_id(admin_client, "member@example.com")
    session_id = member_client.cookies["mopan_session"]
    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is not None
    assert (await member_client.get("/api/auth/me")).status_code == 200

    response = await admin_client.patch(f"/api/users/{member_id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    # The Redis key is gone, not merely shadowed by the is_active check in
    # get_current_user: a 24-hour session that survives deactivation is the bug.
    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is None
    assert (await member_client.get("/api/auth/me")).status_code == 401


async def test_a_session_that_predates_deactivation_is_rejected_by_get_current_user(
    member_client, admin_client, fake_redis, db
):
    """The second half of the guard. Deactivate WITHOUT going through the router,
    so the Redis session survives - exactly the state a session created before
    migration 0002 ran is in."""
    member_id = await _user_id(admin_client, "member@example.com")
    session_id = member_client.cookies["mopan_session"]
    member = await db.get(User, uuid.UUID(member_id))
    member.is_active = False
    await db.commit()

    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is not None
    response = await member_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "비활성화된 계정입니다. 관리자에게 문의해 주세요."


async def test_a_deactivated_user_cannot_log_in_and_the_message_hides_the_account(
    member_client, admin_client
):
    member_id = await _user_id(admin_client, "member@example.com")
    await admin_client.patch(f"/api/users/{member_id}", json={"is_active": False})

    response = await member_client.post(
        "/api/auth/login", json={"email": "member@example.com", "password": "pw123456"}
    )
    assert response.status_code == 401
    # Byte-identical to the unknown-email response: a "deactivated account"
    # message would confirm that this address is registered.
    assert response.json() == {"detail": "이메일 또는 비밀번호가 올바르지 않습니다."}


async def test_reactivating_a_user_lets_them_log_in_again(member_client, admin_client):
    member_id = await _user_id(admin_client, "member@example.com")
    await admin_client.patch(f"/api/users/{member_id}", json={"is_active": False})
    reactivated = await admin_client.patch(f"/api/users/{member_id}", json={"is_active": True})
    assert reactivated.status_code == 200

    login = await member_client.post(
        "/api/auth/login", json={"email": "member@example.com", "password": "pw123456"}
    )
    assert login.status_code == 200


# --- 관리자 비밀번호 재설정 (POST /api/users/{id}/password) --------------------


async def test_admin_password_reset_issues_a_working_temporary_password(
    member_client, admin_client
):
    """임시값은 응답에 한 번 실리고, 옛 비밀번호와 살아 있던 세션은 그 즉시
    죽는다 - 세 가지가 다 참이어야 "재설정"이다."""
    member_id = await _user_id(admin_client, "member@example.com")
    reset = await admin_client.post(f"/api/users/{member_id}/password")
    assert reset.status_code == 200
    temporary = reset.json()["temporary_password"]
    assert len(temporary) >= 8  # 가입 규칙의 하한을 임시값도 지킨다

    assert (await member_client.get("/api/auth/me")).status_code in (401, 403)
    old = await member_client.post(
        "/api/auth/login", json={"email": "member@example.com", "password": "pw123456"}
    )
    assert old.status_code == 401
    fresh = await member_client.post(
        "/api/auth/login", json={"email": "member@example.com", "password": temporary}
    )
    assert fresh.status_code == 200


async def test_admin_cannot_reset_their_own_password_here(admin_client):
    admin_id = await _user_id(admin_client, "admin@example.com")
    refused = await admin_client.post(f"/api/users/{admin_id}/password")
    assert refused.status_code == 409
    assert "계정 설정" in refused.json()["detail"]


async def test_password_reset_is_admin_only(member_client, admin_client):
    member_id = await _user_id(admin_client, "member@example.com")
    assert (await member_client.post(f"/api/users/{member_id}/password")).status_code == 403
