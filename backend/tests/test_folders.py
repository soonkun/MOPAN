"""폴더 트리(0020): 생성·이름 충돌·자손으로 이동 거부·하위 트리 경로 갱신·비어 있지 않은 폴더 삭제 거부,
탐색기 목록의 폴더 필터·페이지, 일괄 이동."""
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def admin_client(client, app):
    await client.post("/api/auth/register", json={"email": "admin@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "pw123456"})
    return client


@pytest_asyncio.fixture
async def cid(admin_client):
    return (await admin_client.post("/api/collections", json={"name": "규정"})).json()["id"]


async def _folder(client, cid, name, parent=None):
    r = await client.post(f"/api/collections/{cid}/folders", json={"name": name, "parent_id": parent})
    assert r.status_code == 201, r.text
    return r.json()


async def _upload(client, cid, name, folder_id=None):
    data = {"collection_id": cid}
    if folder_id:
        data["folder_id"] = folder_id
    r = await client.post("/api/documents", data=data, files={"file": (name, name.encode("utf-8"), "text/plain")})
    assert r.status_code == 202, r.text
    return r.json()


async def test_tree_paths_move_and_guards(admin_client, cid):
    a = await _folder(admin_client, cid, "인사")
    b = await _folder(admin_client, cid, "복무", a["id"])
    c = await _folder(admin_client, cid, "휴가", b["id"])
    assert b["path"] == f"/{a['id']}/{b['id']}/" and c["depth"] == 3
    # 형제 이름 충돌
    assert (await admin_client.post(f"/api/collections/{cid}/folders", json={"name": "인사"})).status_code == 409
    # 자기 자손 밑으로 이동 거부
    assert (await admin_client.patch(f"/api/folders/{a['id']}", json={"parent_id": c["id"]})).status_code == 400
    # 복무를 루트로: 하위 트리(휴가) 경로·깊이가 따라간다
    moved = (await admin_client.patch(f"/api/folders/{b['id']}", json={"move_to_root": True})).json()
    assert moved["parent_id"] is None and moved["depth"] == 1 and moved["path"] == f"/{b['id']}/"
    tree = {f["id"]: f for f in (await admin_client.get(f"/api/collections/{cid}/folders")).json()}
    assert tree[c["id"]]["path"] == f"/{b['id']}/{c['id']}/" and tree[c["id"]]["depth"] == 2
    # 문서가 든 폴더는 지울 수 없다
    await _upload(admin_client, cid, "복무규정.txt", c["id"])
    assert (await admin_client.delete(f"/api/folders/{c['id']}")).status_code == 409
    assert (await admin_client.delete(f"/api/folders/{a['id']}")).status_code == 204


async def test_search_filters_pages_and_bulk_move(admin_client, cid):
    root_doc = await _upload(admin_client, cid, "루트.txt")
    f = await _folder(admin_client, cid, "보고서")
    sub = await _folder(admin_client, cid, "2025", f["id"])
    d1 = await _upload(admin_client, cid, "a보고서.txt", f["id"])
    d2 = await _upload(admin_client, cid, "b보고서.txt", sub["id"])
    page = (await admin_client.get(f"/api/documents/search?collection_id={cid}&folder_id={f['id']}")).json()
    assert page["total"] == 1 and page["items"][0]["folder_path"] == "보고서"
    page = (await admin_client.get(f"/api/documents/search?collection_id={cid}&folder_id={f['id']}&recursive=true&sort=name&order=asc")).json()
    assert [i["filename"] for i in page["items"]] == ["a보고서.txt", "b보고서.txt"]
    assert page["items"][1]["folder_path"] == "보고서/2025"
    root = (await admin_client.get(f"/api/documents/search?collection_id={cid}&root_only=true")).json()
    assert [i["id"] for i in root["items"]] == [root_doc["id"]]
    paged = (await admin_client.get(f"/api/documents/search?collection_id={cid}&limit=2&offset=2&sort=name&order=asc")).json()
    assert paged["total"] == 3 and len(paged["items"]) == 1
    # 일괄 이동: 루트 문서와 d1을 2025로
    r = (await admin_client.post("/api/documents/bulk", json={"ids": [root_doc["id"], d1["id"]], "action": "move", "folder_id": sub["id"]})).json()
    assert set(r["done"]) == {root_doc["id"], d1["id"]} and r["failed"] == []
    tree = {x["id"]: x for x in (await admin_client.get(f"/api/collections/{cid}/folders")).json()}
    assert tree[sub["id"]]["document_count"] == 3
    # 다른 분류로는 못 옮긴다
    other = (await admin_client.post("/api/collections", json={"name": "다른"})).json()["id"]
    r = (await admin_client.post("/api/documents/bulk", json={"ids": [d2["id"]], "action": "move", "collection_id": other})).json()
    assert r["done"] == [] and "다른 분류" in r["failed"][0]["reason"]
    # 일괄 삭제
    r = (await admin_client.post("/api/documents/bulk", json={"ids": [d2["id"], d1["id"]], "action": "delete"})).json()
    assert len(r["done"]) == 2


async def test_folder_scope_reaches_search_tools_listing_and_chat_schema(admin_client, cid, db):
    """3단계: 폴더 → 문서 집합, 도구 목록의 폴더 라벨, 검색 함수의 document_ids 필터."""
    import uuid as _uuid

    from sqlalchemy import text

    from app.documents.folders import documents_in_folders
    from app.retrieval.keyword_search import keyword_search

    top = await _folder(admin_client, cid, "규정")
    sub = await _folder(admin_client, cid, "인사", top["id"])
    inside = await _upload(admin_client, cid, "인사규정.txt", sub["id"])
    outside = await _upload(admin_client, cid, "회계규정.txt")
    ids = await documents_in_folders(db, [_uuid.UUID(top["id"])])
    assert ids == [_uuid.UUID(inside["id"])]  # 하위 폴더 포함, 루트 문서 제외
    assert await documents_in_folders(db, [_uuid.uuid4()]) == []
    tools = (await admin_client.get("/api/tools")).json()
    rag = next(t for t in tools if t["kind"] == "rag")
    labels = {f["path_label"] for c in rag["collections"] for f in c.get("folders", [])}
    assert {"규정", "규정/인사"} <= labels
    # 두 문서에 같은 단어의 청크를 넣고 document_ids로 좁히면 폴더 안 것만 나온다.
    for doc in (inside, outside):
        await db.execute(text(
            "INSERT INTO chunks (id, document_id, chunk_index, content, content_tsv, token_count, char_count, chunk_metadata) "
            "VALUES (:id, :doc, 0, :c, to_tsvector('simple', :c), 2, 6, '{}'::jsonb)"), {"id": str(_uuid.uuid4()), "doc": doc["id"], "c": "휴가 규정"})
    await db.commit()
    hits = await keyword_search(db, "휴가", 10, None, tokenizer="simple", document_ids=ids)
    assert len(hits) == 1
    assert len(await keyword_search(db, "휴가", 10, None, tokenizer="simple")) == 2


async def test_stage4_totals_stale_and_cross_collection_move(admin_client, cid, db, app):
    from sqlalchemy import text

    top = await _folder(admin_client, cid, "규정")
    sub = await _folder(admin_client, cid, "인사", top["id"])
    d_sub = await _upload(admin_client, cid, "인사규정.txt", sub["id"])
    d_top = await _upload(admin_client, cid, "총칙.txt", top["id"])
    tree = {f["id"]: f for f in (await admin_client.get(f"/api/collections/{cid}/folders")).json()}
    assert tree[top["id"]]["document_count"] == 1 and tree[top["id"]]["total_document_count"] == 2
    assert tree[top["id"]]["total_size_bytes"] == tree[sub["id"]]["total_size_bytes"] + tree[top["id"]]["document_count"] * len("총칙.txt".encode())
    assert tree[top["id"]]["last_updated_at"] is not None
    # 점검 필요: 시행일을 5년 전으로 두면 걸리고, 기본 필터에서는 stale 플래그만 붙는다
    await db.execute(text("UPDATE documents SET effective_date = CURRENT_DATE - 2000 WHERE id = :id"), {"id": d_sub["id"]})
    await db.commit()
    page = (await admin_client.get(f"/api/documents/search?collection_id={cid}&stale_only=true")).json()
    assert [i["id"] for i in page["items"]] == [d_sub["id"]] and page["items"][0]["stale"] is True
    page = (await admin_client.get(f"/api/documents/search?collection_id={cid}&recursive=true&folder_id={top['id']}")).json()
    assert {i["id"]: i["stale"] for i in page["items"]} == {d_sub["id"]: True, d_top["id"]: False}
    # 분류 간 이동: reindex 없이는 거절, reindex면 옮기고 다시 처리 큐
    other = (await admin_client.post("/api/collections", json={"name": "다른"})).json()["id"]
    r = (await admin_client.post("/api/documents/bulk", json={"ids": [d_top["id"]], "action": "move", "collection_id": other})).json()
    assert r["done"] == [] and "다시 색인" in r["failed"][0]["reason"]
    app.state.arq_pool.enqueue_job.reset_mock()
    r = (await admin_client.post("/api/documents/bulk", json={"ids": [d_top["id"]], "action": "move", "collection_id": other, "reindex": True})).json()
    assert r["done"] == [d_top["id"]]
    moved = (await admin_client.get(f"/api/documents/{d_top['id']}")).json()
    assert moved["collection_id"] == other and moved["folder_id"] is None and moved["status"] == "uploaded"
    app.state.arq_pool.enqueue_job.assert_awaited_with("process_document", d_top["id"])
