"""폴더 트리·문서 탐색기 API. 계획: docs/superpowers/plans/2026-09-08-document-folders.md (1단계)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Date as sa_Date
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.documents.service import enqueue_document_processing, get_arq_pool
from app.documents.storage import delete_document_files
from app.models.chunk import Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.folder import MAX_FOLDER_DEPTH, Folder
from app.models.user import User
from app.schemas.document import DocumentResponse

logger = logging.getLogger("mopan.documents")
router = APIRouter(prefix="/api", tags=["folders"])

FOLDER_NOT_FOUND = "폴더를 찾을 수 없습니다."
BULK_LIMIT = 50


class FolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # 생략 = 그대로, null = 루트로, 값 = 그 폴더 밑으로.
    parent_id: uuid.UUID | None = None
    move_to_root: bool = False


class FolderResponse(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    path: str
    depth: int
    document_count: int
    child_count: int
    # 하위 트리 합계(4단계): 트리 노드에 보이는 숫자. 현행 문서만 센다.
    total_document_count: int = 0
    total_size_bytes: int = 0
    last_updated_at: datetime | None = None


class BulkBody(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=BULK_LIMIT)
    action: str = Field(pattern="^(move|delete|reprocess)$")
    folder_id: uuid.UUID | None = None
    collection_id: uuid.UUID | None = None  # move의 목적지 컬렉션(폴더 없이 루트로 갈 때 필수)
    # 다른 분류로의 이동(4단계): 청킹 전략이 다르니 재색인을 동반한다. 화면이 확인을 받은 뒤 true로 보낸다.
    reindex: bool = False


class BulkResponse(BaseModel):
    done: list[uuid.UUID]
    failed: list[dict]


class DocumentPage(BaseModel):
    total: int
    items: list[DocumentResponse]


# --- helpers -------------------------------------------------------------------

async def folder_paths(db: AsyncSession, collection_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """폴더 id → 사람이 읽는 경로('인사/복무'). 컬렉션 하나의 폴더를 한 번 읽어 만든다."""
    rows = (await db.execute(select(Folder.id, Folder.parent_id, Folder.name).where(Folder.collection_id == collection_id))).all()
    by_id = {r.id: r for r in rows}
    out: dict[uuid.UUID, str] = {}

    def build(fid):
        if fid in out:
            return out[fid]
        r = by_id[fid]
        out[fid] = f"{build(r.parent_id)}/{r.name}" if r.parent_id and r.parent_id in by_id else r.name
        return out[fid]

    for fid in by_id:
        build(fid)
    return out


async def documents_in_folders(db: AsyncSession, folder_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """폴더들(각각 하위 트리 포함)에 든 문서 id. 채팅의 folder_ids → 검색 document_ids. 모르는 폴더는 무시."""
    from sqlalchemy import or_

    folders = (await db.scalars(select(Folder).where(Folder.id.in_(folder_ids)))).all()
    if not folders:
        return []
    sub = select(Folder.id).where(or_(*[Folder.path.like(f"{f.path}%") for f in folders]))
    return list((await db.scalars(select(Document.id).where(Document.folder_id.in_(sub)))).all())


async def _sibling_taken(db: AsyncSession, cid: uuid.UUID, parent_id: uuid.UUID | None, name: str, *, exclude: uuid.UUID | None = None) -> bool:
    """루트(parent_id NULL)에서는 UNIQUE 제약이 NULL을 서로 다르게 보아 막지 못한다 - 코드가 검사한다."""
    q = select(func.count()).where(Folder.collection_id == cid, Folder.name == name)
    q = q.where(Folder.parent_id.is_(None)) if parent_id is None else q.where(Folder.parent_id == parent_id)
    if exclude is not None:
        q = q.where(Folder.id != exclude)
    return bool(await db.scalar(q))


async def _folder_or_404(db: AsyncSession, fid: uuid.UUID, *, lock: bool = False) -> Folder:
    folder = await db.get(Folder, fid, with_for_update=lock)
    if folder is None:
        raise HTTPException(status_code=404, detail=FOLDER_NOT_FOUND)
    return folder


def _folder_response(f: Folder, docs: int, children: int, totals: dict | None = None) -> FolderResponse:
    t = (totals or {}).get(f.id, {})
    return FolderResponse(
        id=f.id, collection_id=f.collection_id, parent_id=f.parent_id, name=f.name, path=f.path, depth=f.depth,
        document_count=docs, child_count=children,
        total_document_count=t.get("count", docs), total_size_bytes=t.get("size", 0), last_updated_at=t.get("updated"),
    )


async def _subtree_totals(db: AsyncSession, folders: list[Folder]) -> dict:
    """폴더별 하위 트리 합계. 현행 문서를 한 번 읽고 조상 사슬로 접어 올린다(폴더 수십 개, 문서 수백 개 규모)."""
    by_id = {f.id: f for f in folders}
    rows = (
        await db.execute(
            select(Document.folder_id, func.count(), func.coalesce(func.sum(Document.size_bytes), 0), func.max(Document.updated_at))
            .where(Document.folder_id.in_(list(by_id)), Document.is_current.is_(True))
            .group_by(Document.folder_id)
        )
    ).all()
    totals: dict = {f.id: {"count": 0, "size": 0, "updated": None} for f in folders}
    for fid, count, size, updated in rows:
        cur = fid
        while cur is not None and cur in by_id:
            t = totals[cur]
            t["count"] += count
            t["size"] += int(size or 0)
            if updated and (t["updated"] is None or updated > t["updated"]):
                t["updated"] = updated
            cur = by_id[cur].parent_id
    return totals


# --- folders -------------------------------------------------------------------

@router.get("/collections/{cid}/folders", response_model=list[FolderResponse])
async def list_folders(cid: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    if await db.get(Collection, cid) is None:
        raise HTTPException(status_code=404, detail="분류를 찾을 수 없습니다.")
    doc_counts = dict(
        (await db.execute(select(Document.folder_id, func.count()).where(Document.collection_id == cid, Document.folder_id.is_not(None)).group_by(Document.folder_id))).all()
    )
    child_counts = dict(
        (await db.execute(select(Folder.parent_id, func.count()).where(Folder.collection_id == cid, Folder.parent_id.is_not(None)).group_by(Folder.parent_id))).all()
    )
    folders = (await db.scalars(select(Folder).where(Folder.collection_id == cid).order_by(Folder.depth, Folder.name))).all()
    totals = await _subtree_totals(db, list(folders))
    return [_folder_response(f, doc_counts.get(f.id, 0), child_counts.get(f.id, 0), totals) for f in folders]


@router.post("/collections/{cid}/folders", response_model=FolderResponse, status_code=201)
async def create_folder(
    cid: uuid.UUID, payload: FolderBody, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)
):
    if await db.get(Collection, cid) is None:
        raise HTTPException(status_code=404, detail="분류를 찾을 수 없습니다.")
    parent = None
    if payload.parent_id is not None:
        parent = await _folder_or_404(db, payload.parent_id)
        if parent.collection_id != cid:
            raise HTTPException(status_code=400, detail="다른 분류의 폴더 밑에는 만들 수 없습니다.")
        if parent.depth >= MAX_FOLDER_DEPTH:
            raise HTTPException(status_code=400, detail=f"폴더는 {MAX_FOLDER_DEPTH}단계까지만 만들 수 있습니다.")
    if await _sibling_taken(db, cid, payload.parent_id, payload.name.strip()):
        raise HTTPException(status_code=409, detail="같은 위치에 같은 이름의 폴더가 있습니다.")
    folder = Folder(
        id=uuid.uuid4(), collection_id=cid, parent_id=payload.parent_id, name=payload.name.strip(),
        depth=(parent.depth + 1) if parent else 1, created_by=admin.id, path="",
    )
    folder.path = f"{parent.path if parent else '/'}{folder.id}/"
    db.add(folder)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="같은 위치에 같은 이름의 폴더가 있습니다.")
    await db.refresh(folder)
    log_event(logger, "folder_created", folder_id=str(folder.id), collection_id=str(cid), admin_id=str(admin.id))
    return _folder_response(folder, 0, 0)


@router.patch("/folders/{fid}", response_model=FolderResponse)
async def update_folder(
    fid: uuid.UUID, payload: FolderPatch, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)
):
    folder = await _folder_or_404(db, fid, lock=True)
    if payload.name is not None:
        folder.name = payload.name.strip()
    if payload.move_to_root or payload.parent_id is not None:
        new_parent = None
        if not payload.move_to_root:
            new_parent = await _folder_or_404(db, payload.parent_id)
            if new_parent.collection_id != folder.collection_id:
                raise HTTPException(status_code=400, detail="다른 분류로는 옮길 수 없습니다.")
            if new_parent.id == folder.id or new_parent.path.startswith(folder.path):
                raise HTTPException(status_code=400, detail="폴더를 자기 자신이나 그 하위 폴더 밑으로 옮길 수 없습니다.")
        subtree = (await db.scalars(select(Folder).where(Folder.path.like(f"{folder.path}%")).with_for_update())).all()
        deepest = max(f.depth for f in subtree) - folder.depth
        new_depth = (new_parent.depth + 1) if new_parent else 1
        if new_depth + deepest > MAX_FOLDER_DEPTH:
            raise HTTPException(status_code=400, detail=f"옮기면 폴더 깊이가 {MAX_FOLDER_DEPTH}단계를 넘습니다.")
        old_prefix, delta = folder.path, new_depth - folder.depth
        new_prefix = f"{new_parent.path if new_parent else '/'}{folder.id}/"
        for f in subtree:
            f.path = new_prefix + f.path[len(old_prefix):]
            f.depth += delta
        folder.parent_id = new_parent.id if new_parent else None
    if await _sibling_taken(db, folder.collection_id, folder.parent_id, folder.name, exclude=folder.id):
        await db.rollback()
        raise HTTPException(status_code=409, detail="같은 위치에 같은 이름의 폴더가 있습니다.")
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="같은 위치에 같은 이름의 폴더가 있습니다.")
    await db.refresh(folder)
    docs = await db.scalar(select(func.count()).where(Document.folder_id == folder.id)) or 0
    children = await db.scalar(select(func.count()).where(Folder.parent_id == folder.id)) or 0
    return _folder_response(folder, docs, children)


@router.delete("/folders/{fid}", status_code=204)
async def delete_folder(fid: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    folder = await _folder_or_404(db, fid, lock=True)
    docs = await db.scalar(select(func.count()).where(Document.folder_id == fid)) or 0
    children = await db.scalar(select(func.count()).where(Folder.parent_id == fid)) or 0
    if docs or children:
        raise HTTPException(status_code=409, detail=f"비어 있지 않은 폴더는 지울 수 없습니다 (문서 {docs}개, 하위 폴더 {children}개). 먼저 옮기거나 지워 주세요.")
    await db.delete(folder)
    await db.commit()


# --- document explorer ---------------------------------------------------------

SORT_COLUMNS = {
    "name": Document.filename,
    "created_at": Document.created_at,
    "updated_at": Document.updated_at,
    "size": Document.size_bytes,
    "status": Document.status,
}


@router.get("/documents/search", response_model=DocumentPage)
async def search_documents(
    collection_id: uuid.UUID | None = None,
    folder_id: uuid.UUID | None = None,
    root_only: bool = False,
    recursive: bool = False,
    q: str | None = None,
    status: str | None = None,
    current_only: bool = True,
    stale_only: bool = False,
    sort: str = "created_at",
    order: str = "desc",
    offset: int = 0,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """탐색기용 목록: 폴더·검색어·상태 필터, 정렬, 페이지. 기존 GET /documents는 그대로 둔다.
    root_only=true는 컬렉션 루트(folder_id NULL)의 문서만, folder_id는 그 폴더(recursive면 하위 포함)."""
    limit = max(1, min(limit, 200))
    chunk_count = select(func.count(Chunk.id)).where(Chunk.document_id == Document.id).correlate(Document).scalar_subquery()
    base = (
        select(Document, Collection.name, User.email, chunk_count)
        .join(Collection, Collection.id == Document.collection_id)
        .join(User, User.id == Document.uploaded_by)
    )
    if collection_id is not None:
        base = base.where(Document.collection_id == collection_id)
    if folder_id is not None:
        if recursive:
            folder = await _folder_or_404(db, folder_id)
            sub = select(Folder.id).where(Folder.path.like(f"{folder.path}%"))
            base = base.where(Document.folder_id.in_(sub))
        else:
            base = base.where(Document.folder_id == folder_id)
    elif root_only:
        base = base.where(Document.folder_id.is_(None))
    if q and q.strip():
        needle = f"%{q.strip()}%"
        base = base.where(Document.filename.ilike(needle) | User.email.ilike(needle))
    if status:
        base = base.where(Document.status == status)
    if current_only:
        base = base.where(Document.is_current.is_(True))
    # 점검 필요(4단계): 시행일(없으면 등록일)이 기준 일수를 넘은 현행 문서.
    stale_before = datetime.now(UTC) - timedelta(days=settings.document_stale_days)
    stale_expr = func.coalesce(Document.effective_date, func.cast(Document.created_at, sa_Date)) < stale_before.date()
    if stale_only:
        base = base.where(Document.is_current.is_(True), stale_expr)
    total = await db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    column = SORT_COLUMNS.get(sort, Document.created_at)
    if sort == "chunks":
        column = chunk_count
    ordered = base.order_by(column.asc() if order == "asc" else column.desc(), Document.id).offset(offset).limit(limit)
    rows = (await db.execute(ordered)).all()
    paths: dict[uuid.UUID, dict[uuid.UUID, str]] = {}
    items = []
    for document, collection_name, uploader_email, count in rows:
        if document.folder_id and document.collection_id not in paths:
            paths[document.collection_id] = await folder_paths(db, document.collection_id)
        items.append(
            DocumentResponse(
                id=document.id, collection_id=document.collection_id, collection_name=collection_name,
                filename=document.filename, file_type=document.file_type, size_bytes=document.size_bytes,
                status=document.status, error_message=document.error_message, uploader_email=uploader_email,
                chunk_count=count or 0, structure=document.structure or {}, folder_id=document.folder_id,
                lineage_id=document.lineage_id, version=document.version, is_current=document.is_current,
                superseded_at=document.superseded_at, effective_date=document.effective_date,
                stale=document.is_current and (document.effective_date or document.created_at.date()) < stale_before.date(),
                folder_path=paths.get(document.collection_id, {}).get(document.folder_id) if document.folder_id else None,
                created_at=document.created_at, updated_at=document.updated_at,
            )
        )
    return DocumentPage(total=total, items=items)


@router.post("/documents/bulk", response_model=BulkResponse)
async def bulk_documents(
    payload: BulkBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    arq_pool=Depends(get_arq_pool),
):
    """이동(같은 분류 안에서만)·삭제·재처리. 한 건씩 판정해 done/failed로 나눈다 - 하나가 막혀도 나머지는 간다."""
    docs = {d.id: d for d in (await db.scalars(select(Document).where(Document.id.in_(payload.ids)))).all()}
    done, failed, reindex_ids = [], [], []
    target = None
    if payload.action == "move":
        if payload.folder_id is not None:
            target = await _folder_or_404(db, payload.folder_id)
        elif payload.collection_id is None:
            raise HTTPException(status_code=400, detail="이동할 폴더나 분류를 지정해 주세요.")
    for did in payload.ids:
        doc = docs.get(did)
        if doc is None:
            failed.append({"id": str(did), "reason": "문서를 찾을 수 없습니다."})
            continue
        if payload.action == "move":
            dest_collection = target.collection_id if target else payload.collection_id
            if doc.collection_id != dest_collection:
                # 컬렉션이 다르면 청킹 전략이 달라 재색인이 필요하다(4단계). 화면이 확인을 받아 reindex=true로
                # 보낸 경우에만 옮기고 다시 처리 큐에 넣는다. 처리 중 문서는 두 잡이 겹치므로 거절.
                if not payload.reindex:
                    failed.append({"id": str(did), "reason": "다른 분류로 옮기면 다시 색인해야 합니다. 확인 후 다시 시도해 주세요."})
                    continue
                if doc.status not in ("indexed", "failed", "uploaded"):
                    failed.append({"id": str(did), "reason": "처리 중인 문서는 옮길 수 없습니다."})
                    continue
                doc.collection_id = dest_collection
                doc.folder_id = target.id if target else None
                doc.status = "uploaded"
                doc.error_message = None
                reindex_ids.append(did)
                done.append(did)
                continue
            doc.folder_id = target.id if target else None
            done.append(did)
        elif payload.action == "delete":
            await db.delete(doc)
            done.append(did)
        else:
            doc.status = "uploaded"
            doc.error_message = None
            done.append(did)
    await db.commit()
    if payload.action == "delete":
        for did in done:
            await delete_document_files(settings.upload_dir, str(did))
    for did in (done if payload.action == "reprocess" else reindex_ids):
        try:
            await enqueue_document_processing(arq_pool, str(did))
        except Exception:
            logger.exception("bulk enqueue failed")
    log_event(logger, "documents_bulk", action=payload.action, done=len(done), failed=len(failed), admin_id=str(admin.id))
    return BulkResponse(done=done, failed=failed)
