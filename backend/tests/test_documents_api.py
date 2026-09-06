import base64
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.models.chunk import EMBEDDING_DIM, Chunk

MISSING_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def admin_client(client, app):
    await client.post("/api/auth/register", json={"email": "admin@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "pw123456"})
    return client


@pytest_asyncio.fixture
async def collection_id(admin_client):
    response = await admin_client.post("/api/collections", json={"name": "General"})
    return response.json()["id"]


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


async def test_upload_creates_row_and_enqueues_job(admin_client, app, collection_id):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["filename"] == "note.txt"
    assert body["status"] == "uploaded"
    assert body["uploader_email"] == "admin@example.com"
    assert body["collection_name"] == "General"
    app.state.arq_pool.enqueue_job.assert_awaited_once_with("process_document", body["id"])


async def test_upload_requires_admin(member_client, collection_id):
    response = await member_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 403


async def test_create_collection_requires_admin(member_client):
    assert (await member_client.post("/api/collections", json={"name": "X"})).status_code == 403


async def test_delete_document_requires_admin(member_client, admin_client, collection_id):
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = upload.json()["id"]
    assert (await member_client.delete(f"/api/documents/{document_id}")).status_code == 403
    # The refusal must be real, not cosmetic: the row is still there afterwards.
    assert (await admin_client.get(f"/api/documents/{document_id}")).status_code == 200


async def test_admin_delete_removes_the_row_and_the_stored_file(admin_client, app, collection_id):
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = upload.json()["id"]
    stored = Path(app.state.settings.upload_dir) / document_id
    assert stored.exists()

    assert (await admin_client.delete(f"/api/documents/{document_id}")).status_code == 204
    assert (await admin_client.get(f"/api/documents/{document_id}")).status_code == 404
    assert not stored.exists()


async def test_enqueue_failure_marks_the_document_failed_and_drops_the_file(admin_client, app, collection_id):
    """A dropped job must not leave the row at "uploaded" forever, nor leak the file."""
    app.state.arq_pool.enqueue_job.side_effect = RuntimeError("redis down")

    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"]

    reread = await admin_client.get(f"/api/documents/{body['id']}")
    assert reread.json()["status"] == "failed"
    assert not (Path(app.state.settings.upload_dir) / body["id"]).exists()


async def test_members_can_read_the_shared_corpus(member_client, admin_client, collection_id):
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = upload.json()["id"]
    assert (await member_client.get("/api/collections")).status_code == 200
    assert (await member_client.get("/api/documents")).status_code == 200
    assert (await member_client.get(f"/api/documents/{document_id}")).status_code == 200
    assert (await member_client.get(f"/api/documents/{document_id}/chunks")).status_code == 200


async def test_upload_rejects_a_bad_extension(admin_client, collection_id):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("virus.exe", b"bad", "application/octet-stream")},
    )
    assert response.status_code == 400


async def test_upload_rejects_html_renamed_as_pdf(admin_client, collection_id):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("fake.pdf", b"<html><body>hi</body></html>", "application/pdf")},
    )
    assert response.status_code == 400


async def test_traversal_filename_stays_inside_the_upload_root(admin_client, app, collection_id):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("../../evil.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 202
    document_id = response.json()["id"]

    upload_root = Path(app.state.settings.upload_dir).resolve()
    stored = upload_root / document_id / "source.txt"
    assert stored.exists()
    assert stored.resolve().is_relative_to(upload_root)


async def test_upload_rejects_an_unknown_collection(admin_client):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": str(uuid.uuid4())},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 404


async def test_list_documents_requires_auth(client):
    assert (await client.get("/api/documents")).status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/collections"),
        ("GET", "/api/collections"),
        ("POST", "/api/documents"),
        ("GET", "/api/documents"),
        ("GET", f"/api/documents/{MISSING_ID}"),
        ("DELETE", f"/api/documents/{MISSING_ID}"),
        ("GET", f"/api/documents/{MISSING_ID}/chunks"),
        ("GET", f"/api/documents/{MISSING_ID}/download"),
        ("GET", f"/api/chunks/{MISSING_ID}"),
    ],
)
async def test_every_route_requires_authentication(client, method, path):
    """401 before anything else - no route may answer an anonymous caller, and
    none may leak existence through a 404/422 on the way to the auth check."""
    assert (await client.request(method, path)).status_code == 401


async def test_get_unknown_chunk_returns_404(admin_client):
    response = await admin_client.get(f"/api/chunks/{uuid.uuid4()}")
    assert response.status_code == 404
    # The detail is user-facing: it renders in the chat citation modal, labelled
    # 출처, when a cited chunk's document has been deleted. 청크 is internal
    # vocabulary the chat surface uses nowhere else.
    assert response.json()["detail"] == "출처 내용을 불러올 수 없습니다."


async def test_chunk_response_reports_embedding_state(admin_client, db, collection_id):
    """`embedded` is derived from the embedding column, not stored: the vector
    itself is 1536 floats and never goes on the wire."""
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])
    db.add_all(
        [
            Chunk(
                document_id=document_id,
                chunk_index=0,
                content="embedded chunk",
                token_count=2,
                char_count=14,
                chunk_metadata={"strategy": "semantic"},
                embedding=[0.0] * EMBEDDING_DIM,
            ),
            Chunk(
                document_id=document_id,
                chunk_index=1,
                content="unembedded chunk",
                token_count=2,
                char_count=16,
                chunk_metadata={},
                embedding=None,
            ),
        ]
    )
    await db.commit()

    response = await admin_client.get(f"/api/documents/{document_id}/chunks")
    assert response.status_code == 200
    # {total, items} 봉투: 만행짜리 표가 한 응답으로 내려가 브라우저를 죽인
    # 실사고 이후 페이지네이션 계약이 됐다.
    envelope = response.json()
    body = envelope["items"]
    assert envelope["total"] == 2
    assert [c["embedded"] for c in body] == [True, False]
    assert body[0]["chunk_metadata"] == {"strategy": "semantic"}
    assert "embedding" not in body[0]

    single = await admin_client.get(f"/api/chunks/{body[0]['id']}")
    assert single.json()["embedded"] is True


async def test_download_returns_the_stored_bytes_under_the_original_filename(
    admin_client, member_client, collection_id
):
    """The uploaded name is Korean and never touches the path on disk - the file
    is stored as source.md - so Content-Disposition is the only place it can come
    back from. Any authenticated user may fetch it: same authorization as
    GET /api/documents/{id}, because the corpus is shared by design."""
    body = "# 제목\n\n본문입니다.\n".encode()
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("심사기준 초안.md", body, "text/markdown")},
    )
    document_id = upload.json()["id"]

    response = await member_client.get(f"/api/documents/{document_id}/download")
    assert response.status_code == 200
    assert response.content == body
    # octet-stream for every type, not text/markdown: .html is an accepted upload
    # and /api/* is proxied same-origin by Next, so echoing a stored file back
    # under its own Content-Type would be stored XSS on the app's own origin.
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert disposition.endswith("filename*=UTF-8''" + quote("심사기준 초안.md"))


async def test_download_of_a_document_whose_file_is_gone_is_a_korean_404(
    admin_client, app, collection_id
):
    """Reachable, not theoretical: a locally-run backend and the Docker backend do
    not share UPLOAD_DIR (host path vs named volume), so a document uploaded to
    one is a row the other lists and a file it cannot open. Left to FileResponse
    this raises RuntimeError from inside the response and answers 500."""
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = upload.json()["id"]
    shutil.rmtree(Path(app.state.settings.upload_dir) / document_id)

    response = await admin_client.get(f"/api/documents/{document_id}/download")
    assert response.status_code == 404
    assert response.json()["detail"] == "원본 파일을 더 이상 찾을 수 없습니다."


# --- collection CRUD ----------------------------------------------------------


async def test_rename_collection_and_clear_its_description(admin_client, collection_id):
    renamed = await admin_client.patch(
        f"/api/collections/{collection_id}", json={"name": "사규", "description": "인사 규정"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "사규"
    assert renamed.json()["description"] == "인사 규정"

    # An OMITTED field must not be touched; an explicit null must clear it. A
    # plain model_dump() cannot tell those apart and would wipe the name here.
    cleared = await admin_client.patch(
        f"/api/collections/{collection_id}", json={"description": None}
    )
    assert cleared.status_code == 200
    assert cleared.json() == {**renamed.json(), "description": None}


async def test_collection_name_is_stripped_and_a_blank_name_is_rejected(admin_client, collection_id):
    stripped = await admin_client.patch(f"/api/collections/{collection_id}", json={"name": "  사규  "})
    assert stripped.json()["name"] == "사규"
    blank = await admin_client.patch(f"/api/collections/{collection_id}", json={"name": "   "})
    assert blank.status_code == 422
    # collections.name is NOT NULL, so an explicit null is a 422 and not a 409
    # blaming a duplicate name that does not exist.
    null = await admin_client.patch(f"/api/collections/{collection_id}", json={"name": None})
    assert null.status_code == 422


async def test_duplicate_collection_name_is_refused_on_create_and_rename(admin_client, collection_id):
    duplicate = await admin_client.post("/api/collections", json={"name": "General"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "같은 이름의 분류가 이미 있습니다. 다른 이름을 입력해 주세요."

    other = await admin_client.post("/api/collections", json={"name": "Other"})
    assert other.status_code == 200
    renamed = await admin_client.patch(f"/api/collections/{other.json()['id']}", json={"name": "General"})
    assert renamed.status_code == 409
    assert renamed.json()["detail"] == "같은 이름의 분류가 이미 있습니다. 다른 이름을 입력해 주세요."


async def test_delete_empty_collection(admin_client, collection_id):
    assert (await admin_client.delete(f"/api/collections/{collection_id}")).status_code == 204
    remaining = (await admin_client.get("/api/collections")).json()
    # Only 일반 is left - the one register_user seeds for the bootstrap admin.
    assert [c["name"] for c in remaining] == ["일반"]


async def test_the_last_collection_is_deletable(admin_client, collection_id):
    """Deliberate: an admin left with none creates one, which is the same click
    the upload form's empty state already offers. A floor of one would instead
    make a single mis-named collection permanent, and renaming is a PATCH away."""
    for collection in (await admin_client.get("/api/collections")).json():
        assert (await admin_client.delete(f"/api/collections/{collection['id']}")).status_code == 204
    assert (await admin_client.get("/api/collections")).json() == []
    assert (await admin_client.post("/api/collections", json={"name": "다시"})).status_code == 200


async def test_delete_refuses_while_the_collection_holds_documents(admin_client, app, collection_id):
    for name in ("a.txt", "b.txt"):
        await admin_client.post(
            "/api/documents",
            data={"collection_id": collection_id},
            files={"file": (name, b"hello", "text/plain")},
        )

    response = await admin_client.delete(f"/api/collections/{collection_id}")
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "문서 2개가 들어 있는 분류는 삭제할 수 없습니다. 먼저 문서를 삭제해 주세요."
    )
    # documents.collection_id is ON DELETE CASCADE and chunks cascade from
    # documents, so without the guard this call destroys both rows silently and
    # orphans their files under upload_dir.
    assert len((await admin_client.get("/api/documents")).json()) == 2
    names = [c["name"] for c in (await admin_client.get("/api/collections")).json()]
    assert "General" in names

    for document in (await admin_client.get("/api/documents")).json():
        await admin_client.delete(f"/api/documents/{document['id']}")
    assert (await admin_client.delete(f"/api/collections/{collection_id}")).status_code == 204


async def test_collection_writes_are_admin_only(member_client, admin_client, collection_id):
    renamed = await member_client.patch(f"/api/collections/{collection_id}", json={"name": "X"})
    assert renamed.status_code == 403
    assert (await member_client.delete(f"/api/collections/{collection_id}")).status_code == 403
    # The refusal must be real, not cosmetic: the row is unchanged and still there.
    survivors = (await admin_client.get("/api/collections")).json()
    assert [c["name"] for c in survivors if c["id"] == collection_id] == ["General"]


async def test_unknown_collection_id_is_404(admin_client):
    patched = await admin_client.patch(f"/api/collections/{MISSING_ID}", json={"name": "X"})
    assert patched.status_code == 404
    assert patched.json()["detail"] == "분류를 찾을 수 없습니다."
    deleted = await admin_client.delete(f"/api/collections/{MISSING_ID}")
    assert deleted.status_code == 404
    assert deleted.json()["detail"] == "분류를 찾을 수 없습니다."


# --- Chat attachments --------------------------------------------------------
#
# Same upload machinery as a corpus document (app/documents/validation.py,
# app/documents/storage.py) and a deliberately DIFFERENT permission rule: writing
# to /api/documents is admin-only because those documents become the evidence base
# for everybody's answers, while an attachment can only ever influence its own
# owner's answer. member_client below is a plain non-admin user throughout, and is
# 403 on /api/documents in test_upload_requires_admin.

# A real 1x1 PNG: `filetype` sniffs the signature, so a made-up byte string would
# be rejected by validate_magic_bytes before any of these tests measured anything.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def upload_attachment(client, name="note.txt", data=b"hello", content_type="text/plain"):
    return await client.post("/api/attachments", files={"file": (name, data, content_type)})


async def test_any_authenticated_user_may_attach(member_client):
    response = await upload_attachment(member_client, "spec.txt", b"turbidity is 4.2 NTU")
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "document"
    assert body["filename"] == "spec.txt"
    assert body["size_bytes"] == len(b"turbidity is 4.2 NTU")
    # Extracted at upload, not at answer time - but never echoed back: it is prompt
    # input, sometimes megabytes of it.
    assert body["has_text"] is True
    assert "extracted_text" not in body


async def test_an_image_attachment_is_stored_with_kind_image(member_client):
    body = (await upload_attachment(member_client, "shot.png", PNG_1X1, "image/png")).json()
    assert body["kind"] == "image"
    # NULL extracted_text: an image reaches the model as an image part, not text.
    assert body["has_text"] is False


async def test_attachment_routes_require_auth(client):
    attachment_id = uuid.uuid4()
    assert (await upload_attachment(client)).status_code == 401
    assert (await client.get(f"/api/attachments/{attachment_id}")).status_code == 401
    assert (await client.get(f"/api/attachments/{attachment_id}/content")).status_code == 401
    assert (await client.delete(f"/api/attachments/{attachment_id}")).status_code == 401


async def test_another_users_attachment_is_404_on_every_route(member_client, admin_client):
    """404, not 403, exactly as get_owned_conversation does it: a 403 would confirm
    that somebody else's attachment id exists."""
    attachment_id = (await upload_attachment(member_client, "private.txt", b"my own notes")).json()["id"]

    assert (await admin_client.get(f"/api/attachments/{attachment_id}")).status_code == 404
    assert (await admin_client.get(f"/api/attachments/{attachment_id}/content")).status_code == 404
    assert (await admin_client.delete(f"/api/attachments/{attachment_id}")).status_code == 404
    # The refusal is real, not cosmetic: the owner still has it.
    assert (await member_client.get(f"/api/attachments/{attachment_id}")).status_code == 200


async def test_an_unknown_attachment_id_is_the_same_404(member_client):
    response = await member_client.get(f"/api/attachments/{MISSING_ID}")
    assert response.status_code == 404
    assert response.json()["detail"] == "첨부파일을 찾을 수 없습니다."


async def test_an_oversize_attachment_is_refused_in_korean(member_client, app):
    """MAX_ATTACHMENT_SIZE_MB is its own setting, not MAX_UPLOAD_SIZE_MB: a corpus
    document is chunked and reaches the model a few hundred tokens at a time, an
    attachment reaches it whole in one request. Only the attachment limit is
    lowered here, and the "1MB" in the message is what proves it was the one
    consulted - asserting the 413 alone would pass against a handler reading
    MAX_UPLOAD_SIZE_MB, because save_upload_stream refuses it a second time
    anyway. (Staged: swapping the setting leaves the status 413 and changes only
    this string.)"""
    app.state.settings = app.state.settings.model_copy(
        update={"max_attachment_size_mb": 1, "max_upload_size_mb": 50}
    )
    response = await upload_attachment(member_client, "big.txt", b"x" * (2 * 1024 * 1024))
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail == "파일이 최대 크기 1MB를 초과했습니다."
    # Hangul, not English: frontend/lib/api.ts:detailText drops a detail with no
    # Hangul in it and shows a generic fallback, so English is invisible here.
    assert any("가" <= ch <= "힣" for ch in detail)


async def test_a_content_type_extension_mismatch_is_refused(member_client):
    response = await upload_attachment(member_client, "shot.png", PNG_1X1, "application/pdf")
    assert response.status_code == 400
    assert response.json()["detail"] == "Content-Type application/pdf은(는) .png 파일과 맞지 않습니다."


async def test_an_image_renamed_to_pdf_is_refused_by_its_magic_bytes(member_client):
    response = await upload_attachment(member_client, "shot.pdf", PNG_1X1, "application/pdf")
    assert response.status_code == 400
    assert "확장자와 맞지 않습니다" in response.json()["detail"]


async def test_an_unsupported_attachment_type_is_refused(member_client):
    response = await upload_attachment(member_client, "clip.mp4", b"\x00\x00\x00\x20ftypisom", "video/mp4")
    assert response.status_code == 400
    assert response.json()["detail"] == "지원하지 않는 파일 형식입니다: .mp4"


async def test_an_image_is_refused_when_the_answer_model_cannot_see(member_client, app):
    """Refused at UPLOAD, so the user is told while attaching rather than after
    composing a whole message around a thumbnail - and so an image part can never
    reach a text-only model at all."""
    app.state.settings = app.state.settings.model_copy(
        update={"answer_model": "text-only-1", "answer_model_supports_vision": False}
    )
    response = await upload_attachment(member_client, "shot.png", PNG_1X1, "image/png")
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "현재 답변 모델(text-only-1)은 이미지를 읽을 수 없습니다. "
        "이미지 대신 문서 파일을 첨부하거나 관리자에게 문의해 주세요."
    )
    # A document attachment still works - the model just cannot see pictures.
    assert (await upload_attachment(member_client, "note.txt", b"still fine")).status_code == 201


async def test_a_document_with_no_readable_text_is_refused(member_client):
    """A scanned PDF parses cleanly and yields nothing. Accepting it would put a
    chip on screen that contributes literally nothing to the answer, with no way
    for the user to tell."""
    response = await upload_attachment(member_client, "blank.txt", b"   \n  \n")
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "첨부한 문서에서 읽을 수 있는 텍스트를 찾지 못했습니다. 다른 파일을 첨부해 주세요."
    )


async def test_a_refused_attachment_leaves_no_row_and_no_file(member_client, app, db):
    from app.models.attachment import Attachment

    await upload_attachment(member_client, "blank.txt", b"   ")
    assert (await db.scalars(select(Attachment))).all() == []
    root = Path(app.state.settings.upload_dir) / "attachments"
    assert not root.exists() or list(root.iterdir()) == []


async def test_image_content_is_served_inline_and_html_is_not(member_client):
    """.html is an accepted attachment type and /api/* is proxied same-origin by
    Next, so serving a stored file back under its own Content-Type would be stored
    XSS on the app's own origin."""
    image_id = (await upload_attachment(member_client, "shot.png", PNG_1X1, "image/png")).json()["id"]
    hostile_page = b"<html><body><p>hi</p><script>alert(1)</script></body></html>"
    page_id = (await upload_attachment(member_client, "x.html", hostile_page, "text/html")).json()["id"]

    image = await member_client.get(f"/api/attachments/{image_id}/content")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.headers["content-disposition"].startswith("inline")
    assert image.content == PNG_1X1

    page = await member_client.get(f"/api/attachments/{page_id}/content")
    assert page.headers["content-type"] == "application/octet-stream"
    assert page.headers["content-disposition"].startswith("attachment")
    assert page.headers["x-content-type-options"] == "nosniff"


async def test_deleting_an_unclaimed_attachment_removes_the_row_and_the_file(member_client, app):
    attachment_id = (await upload_attachment(member_client, "note.txt", b"drop me")).json()["id"]
    stored = Path(app.state.settings.upload_dir) / "attachments" / attachment_id
    assert stored.exists()

    assert (await member_client.delete(f"/api/attachments/{attachment_id}")).status_code == 204
    assert (await member_client.get(f"/api/attachments/{attachment_id}")).status_code == 404
    assert not stored.exists()


async def test_attachments_live_under_their_own_subdirectory(member_client, admin_client, app, collection_id):
    """One UPLOAD_DIR, two per-id trees. `attachments` can never collide with a
    document's directory because that name is always a UUID."""
    attachment_id = (await upload_attachment(member_client, "note.txt", b"hello")).json()["id"]
    document = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    root = Path(app.state.settings.upload_dir)
    assert (root / "attachments" / attachment_id / "source.txt").exists()
    assert (root / document.json()["id"] / "source.txt").exists()


async def test_chunk_list_pages_and_ranks_by_similarity(admin_client, app, db, collection_id):
    """만행짜리 표가 한 응답으로 내려가 브라우저를 죽인 실사고의 계약:
    기본은 chunk_index 순 한 장(offset/limit)이고, q가 있으면 이 문서 안에서
    임베딩 코사인 순위다 - 질문이 검색을 탈 때와 같은 공간."""
    from unittest.mock import AsyncMock

    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("표.txt", "행들".encode(), "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])

    def vec(x: float) -> list[float]:
        return [x] + [0.0] * (EMBEDDING_DIM - 2) + [(1 - x * x) ** 0.5]

    db.add_all(
        [
            Chunk(
                document_id=document_id,
                chunk_index=i,
                content=f"행 {i}",
                token_count=2,
                char_count=3,
                chunk_metadata={},
                # 질의 벡터 [1,0,…]과의 코사인이 index 2 > 0 > 1 순이 되게.
                embedding=vec([0.5, 0.1, 0.9][i]),
            )
            for i in range(3)
        ]
    )
    await db.commit()

    first = (await admin_client.get(f"/api/documents/{document_id}/chunks?limit=2")).json()
    assert first["total"] == 3
    assert [c["chunk_index"] for c in first["items"]] == [0, 1]
    second = (
        await admin_client.get(f"/api/documents/{document_id}/chunks?limit=2&offset=2")
    ).json()
    assert [c["chunk_index"] for c in second["items"]] == [2]

    provider = AsyncMock()
    provider.embed = AsyncMock(return_value=[vec(1.0)])
    app.state.llm_provider = provider
    ranked = (
        await admin_client.get(f"/api/documents/{document_id}/chunks?q=희석배수")
    ).json()
    assert [c["chunk_index"] for c in ranked["items"]] == [2, 0, 1]
    provider.embed.assert_awaited_once_with(["희석배수"])
