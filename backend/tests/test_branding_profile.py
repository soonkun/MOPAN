"""브랜딩(화면 문구·마스코트)과 프로필(닉네임·계정 삭제)."""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.user import User

# 1x1 투명 PNG - 업로드 검사용 최소 실물.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f4e0000000049454e44ae426082"
)


@pytest_asyncio.fixture
async def admin(client):
    """첫 가입자 = 관리자."""
    await client.post("/api/auth/register", json={"email": "boss@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "boss@example.com", "password": "pw123456"})
    return client


@pytest_asyncio.fixture
async def member(client):
    """두 번째 가입자 = 일반 사용자. 반환 후 그 사람으로 로그인된 상태다."""
    await client.post("/api/auth/register", json={"email": "boss@example.com", "password": "pw123456"})
    await client.post("/api/auth/register", json={"email": "kim@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "kim@example.com", "password": "pw123456"})
    return client


# ---------------------------------------------------------------- 브랜딩


async def test_branding_defaults_are_nulls_not_copies(admin):
    """행이 없을 때의 응답이 곧 계약: 전부 null/빈 목록이고, 기본 문구의 원본은
    프런트다. 마이그레이션이 문구를 데이터로 굳히면 코드의 문구와 어긋난다."""
    body = (await admin.get("/api/branding")).json()
    assert body == {
        "app_title": None,
        "tagline_primary": None,
        "tagline_secondary": None,
        "suggested_questions": [],
        "has_custom_mascot": False,
    }


async def test_branding_roundtrip_and_blank_means_default(admin):
    put = await admin.put(
        "/api/branding",
        json={
            "app_title": "  농약 도우미  ",
            "tagline_primary": "농약 안전 사용의 기준을 답합니다.",
            "tagline_secondary": "",
            "suggested_questions": ["빈 용기는 어떻게 버리나요?", "  ", "혼용 가능한 약제는?"],
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["app_title"] == "농약 도우미"
    # 빈 문자열은 "기본값으로"다 - 빈 제목을 원한 관리자는 없다.
    assert body["tagline_secondary"] is None
    # 공백 줄은 접힌다.
    assert body["suggested_questions"] == ["빈 용기는 어떻게 버리나요?", "혼용 가능한 약제는?"]

    again = (await admin.get("/api/branding")).json()
    assert again["app_title"] == "농약 도우미"


async def test_branding_write_needs_admin_but_read_does_not(member):
    put = await member.put("/api/branding", json={"app_title": "탈취"})
    assert put.status_code == 403
    assert (await member.get("/api/branding")).status_code == 200


async def test_too_many_suggested_questions_are_refused_in_korean(admin):
    put = await admin.put(
        "/api/branding",
        json={"suggested_questions": [f"질문 {i}" for i in range(7)]},
    )
    assert put.status_code == 400
    assert "추천 질문" in put.json()["detail"]


async def test_mascot_upload_serve_and_reset(admin):
    assert (await admin.get("/api/branding/mascot")).status_code == 404

    upload = await admin.post(
        "/api/branding/mascot", files={"file": ("m.png", PNG, "image/png")}
    )
    assert upload.status_code == 204
    served = await admin.get("/api/branding/mascot")
    assert served.status_code == 200
    assert served.content == PNG
    assert (await admin.get("/api/branding")).json()["has_custom_mascot"] is True

    assert (await admin.delete("/api/branding/mascot")).status_code == 204
    assert (await admin.get("/api/branding/mascot")).status_code == 404


async def test_mascot_refuses_non_image_content_type(admin):
    upload = await admin.post(
        "/api/branding/mascot", files={"file": ("m.svg", b"<svg/>", "image/svg+xml")}
    )
    assert upload.status_code == 400


# ---------------------------------------------------------------- 프로필


async def test_nickname_roundtrip_and_blank_clears_it(member):
    patched = await member.patch("/api/auth/me", json={"nickname": "  김프로  "})
    assert patched.status_code == 200
    assert patched.json()["nickname"] == "김프로"
    assert (await member.get("/api/auth/me")).json()["nickname"] == "김프로"

    cleared = await member.patch("/api/auth/me", json={"nickname": ""})
    assert cleared.json()["nickname"] is None


async def test_delete_me_requires_the_password(member):
    refused = await member.request(
        "DELETE", "/api/auth/me", json={"password": "wrong-password"}
    )
    assert refused.status_code == 403
    # 계정은 멀쩡하다.
    assert (await member.get("/api/auth/me")).status_code == 200


async def test_the_last_admin_cannot_delete_their_account(admin):
    refused = await admin.request("DELETE", "/api/auth/me", json={"password": "pw123456"})
    assert refused.status_code == 400
    assert "마지막 관리자" in refused.json()["detail"]


async def test_delete_me_anonymises_and_locks_out(member, db):
    """행은 남고(문서·분류 FK가 RESTRICT라 모델 스스로 삭제를 금지한다) 개인의
    것 - 대화, 이메일, 호칭, 로그인 - 만 사라진다."""
    user = await db.scalar(select(User).where(User.email == "kim@example.com"))
    db.add(Conversation(user_id=user.id, title="지울 대화"))
    await db.commit()

    gone = await member.request("DELETE", "/api/auth/me", json={"password": "pw123456"})
    assert gone.status_code == 204

    # 세션은 즉시 죽는다.
    assert (await member.get("/api/auth/me")).status_code in (401, 403)
    # 다시 로그인도 안 된다.
    login = await member.post(
        "/api/auth/login", json={"email": "kim@example.com", "password": "pw123456"}
    )
    assert login.status_code == 401

    # populate_existing: 같은 세션의 정체성 맵이 낡은 속성을 돌려주지 않게.
    row = await db.scalar(
        select(User).where(User.id == user.id).execution_options(populate_existing=True)
    )
    assert row is not None  # 행은 남는다
    assert row.is_active is False
    assert row.email.startswith("deleted-")
    assert row.nickname is None
    conversations = (
        await db.scalars(select(Conversation).where(Conversation.user_id == user.id))
    ).all()
    assert conversations == []
