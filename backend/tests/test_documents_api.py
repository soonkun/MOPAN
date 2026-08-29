import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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
        ("GET", f"/api/documents/{MISSING_ID}/structure"),
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
    body = response.json()
    assert [c["embedded"] for c in body] == [True, False]
    assert body[0]["chunk_metadata"] == {"strategy": "semantic"}
    assert "embedding" not in body[0]

    single = await admin_client.get(f"/api/chunks/{body[0]['id']}")
    assert single.json()["embedded"] is True


async def test_document_structure_returns_parsed_blocks(admin_client, collection_id):
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("doc.md", b"# Title\n\nA paragraph.\n", "text/markdown")},
    )
    document_id = upload.json()["id"]
    response = await admin_client.get(f"/api/documents/{document_id}/structure")
    assert response.status_code == 200
    blocks = response.json()
    assert blocks[0]["block_type"] == "heading"
    assert blocks[0]["text"] == "Title"


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
