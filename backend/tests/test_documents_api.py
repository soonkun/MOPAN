import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

MISSING_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def admin_client(client, app):
    app.state.arq_pool = AsyncMock()
    await client.post(
        "/api/auth/register", json={"email": "admin@example.com", "password": "pw123456"}
    )
    await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "pw123456"}
    )
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
        await ac.post(
            "/api/auth/login", json={"email": "member@example.com", "password": "pw123456"}
        )
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
    assert (await admin_client.get(f"/api/chunks/{uuid.uuid4()}")).status_code == 404


@pytest.mark.xfail(
    raises=ModuleNotFoundError,
    strict=True,
    reason="app.rag.parsers arrives in Task 8; strict so Task 8 cannot forget to drop this marker",
)
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
