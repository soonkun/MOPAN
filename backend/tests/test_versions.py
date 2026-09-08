"""규정 현행화(0021): 새 버전 교체 → 옛 버전은 검색 세 경로에서 빠진다(불변식), 되돌리기, 실패 시 복귀,
중복 파일 409/force, 탐색기 current_only."""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.documents.versions import revert_current_on_failure
from app.models.document import Document
from app.retrieval.keyword_search import keyword_search
from app.retrieval.vector_store import PgVectorStore


@pytest_asyncio.fixture
async def admin_client(client, app):
    await client.post("/api/auth/register", json={"email": "admin@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "pw123456"})
    return client


@pytest_asyncio.fixture
async def cid(admin_client):
    return (await admin_client.post("/api/collections", json={"name": "규정"})).json()["id"]


async def _upload(client, cid, name, data=b"v1 content", **form):
    return await client.post("/api/documents", data={"collection_id": cid, **form}, files={"file": (name, data, "text/plain")})


async def _chunk(db, document_id, content, embedding=None):
    await db.execute(
        text(
            "INSERT INTO chunks (id, document_id, chunk_index, content, content_tsv, token_count, char_count, chunk_metadata, embedding) "
            "VALUES (:id, :doc, 0, :content, to_tsvector('simple', :content), 3, :n, '{}'::jsonb, :emb)"
        ),
        {"id": str(uuid.uuid4()), "doc": document_id, "content": content, "n": len(content), "emb": embedding},
    )
    await db.commit()


async def test_new_version_replaces_current_and_old_chunks_leave_every_search_path(admin_client, cid, db):
    v1 = (await _upload(admin_client, cid, "복무규정.txt")).json()
    emb = "[" + ",".join(["0.01"] * 1536) + "]"
    await _chunk(db, v1["id"], "복무 규정 제1조 휴가 연 15일", emb)
    await db.execute(text("UPDATE documents SET status='indexed' WHERE id=:id"), {"id": v1["id"]})
    await db.commit()
    # 새 버전
    r = await admin_client.post(f"/api/documents/{v1['id']}/versions", files={"file": ("복무규정_개정.txt", b"v2 xx", "text/plain")}, data={"effective_date": "2026-10-01"})
    assert r.status_code == 202, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["is_current"] is True and v2["effective_date"] == "2026-10-01"
    versions = (await admin_client.get(f"/api/documents/{v1['id']}/versions")).json()
    assert [(v["version"], v["is_current"]) for v in versions] == [(2, True), (1, False)]
    # 불변식: 옛 버전 청크는 두 팔 어디에도 없다(표 조회 SQL도 같은 조건 - examples_mcp/main.py).
    assert await keyword_search(db, "휴가", 10, None, tokenizer="simple") == []
    assert await PgVectorStore(db).search([0.01] * 1536, 10, None) == []
    # 탐색기: current_only 기본은 v2만, 끄면 둘 다
    page = (await admin_client.get(f"/api/documents/search?collection_id={cid}")).json()
    assert [i["version"] for i in page["items"]] == [2]
    page = (await admin_client.get(f"/api/documents/search?collection_id={cid}&current_only=false&sort=name&order=asc")).json()
    assert page["total"] == 2
    # 되돌리기: v1은 indexed라 현행 복귀 가능, 이후 검색에 다시 보인다
    act = await admin_client.post(f"/api/documents/{v1['id']}/versions/{v1['id']}/activate")
    assert act.status_code == 200 and act.json()["is_current"] is True
    assert len(await keyword_search(db, "휴가", 10, None, tokenizer="simple")) == 1
    # 색인 안 끝난 v2(uploaded)는 현행이 될 수 없다
    assert (await admin_client.post(f"/api/documents/{v1['id']}/versions/{v2['id']}/activate")).status_code == 400


async def test_failed_new_version_reverts_to_previous(admin_client, cid, db):
    v1 = (await _upload(admin_client, cid, "a.txt")).json()
    v2 = (await admin_client.post(f"/api/documents/{v1['id']}/versions", files={"file": ("a2.txt", b"v2", "text/plain")})).json()
    doc = await db.get(Document, uuid.UUID(v2["id"]))
    assert doc.is_current
    assert await revert_current_on_failure(db, doc) is True
    await db.commit()
    prev = await db.get(Document, uuid.UUID(v1["id"]))
    assert prev.is_current is True and doc.is_current is False and doc.superseded_at is not None


async def test_duplicate_upload_is_409_unless_forced_and_same_content_version_is_refused(admin_client, cid):
    first = await _upload(admin_client, cid, "one.txt", b"same bytes")
    assert first.status_code == 202
    dup = await _upload(admin_client, cid, "two.txt", b"same bytes")
    assert dup.status_code == 409
    detail = dup.json()["detail"]
    assert detail["existing_document_id"] == first.json()["id"] and "one.txt" in detail["message"]
    forced = await _upload(admin_client, cid, "two.txt", b"same bytes", force="true")
    assert forced.status_code == 202
    # 새 버전이 현행과 같은 내용이면 빈 개정 - 거절
    same = await admin_client.post(f"/api/documents/{first.json()['id']}/versions", files={"file": ("one_v2.txt", b"same bytes", "text/plain")})
    assert same.status_code == 409
