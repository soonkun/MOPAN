"""규정 현행화 - 문서 버전(계보) API. 계획 2단계. 불변식: is_current=false 문서의 청크는 어떤
검색 경로에도 나오지 않는다(vector_store·keyword_search·table_lookup이 각자 조건을 건다)."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.documents.service import enqueue_document_processing, get_arq_pool
from app.documents.storage import delete_document_files, save_upload_stream
from app.documents.validation import (
    MAGIC_SNIFF_BYTES,
    UploadTooLarge,
    UploadValidationError,
    validate_magic_bytes,
    validate_upload_metadata,
)
from app.models.document import Document
from app.models.user import User

logger = logging.getLogger("mopan.documents")
router = APIRouter(prefix="/api", tags=["document-versions"])


class VersionResponse(BaseModel):
    id: uuid.UUID
    version: int
    is_current: bool
    status: str
    filename: str
    size_bytes: int
    effective_date: date | None
    superseded_at: datetime | None
    created_at: datetime
    uploader_email: str | None


async def revert_current_on_failure(db: AsyncSession, document: Document) -> bool:
    """색인에 실패한 새 버전은 현행이 될 수 없다 - 직전 버전을 다시 현행으로. 파이프라인과
    worker.mark_failed가 부른다. 되돌린 게 있으면 True."""
    if document.version <= 1 or not document.is_current:
        return False
    previous = await db.scalar(
        select(Document)
        .where(Document.lineage_id == document.lineage_id, Document.id != document.id, Document.version < document.version)
        .order_by(Document.version.desc())
        .limit(1)
    )
    if previous is None:
        return False
    document.is_current = False
    document.superseded_at = datetime.now(UTC)
    await db.flush()
    previous.is_current = True
    previous.superseded_at = None
    return True


def _version_response(doc: Document, email: str | None) -> VersionResponse:
    return VersionResponse(
        id=doc.id, version=doc.version, is_current=doc.is_current, status=doc.status, filename=doc.filename,
        size_bytes=doc.size_bytes, effective_date=doc.effective_date, superseded_at=doc.superseded_at,
        created_at=doc.created_at, uploader_email=email,
    )


@router.get("/documents/{document_id}/versions", response_model=list[VersionResponse])
async def list_versions(document_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    doc = await db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    rows = (
        await db.execute(
            select(Document, User.email).outerjoin(User, User.id == Document.uploaded_by)
            .where(Document.lineage_id == doc.lineage_id).order_by(Document.version.desc())
        )
    ).all()
    return [_version_response(d, e) for d, e in rows]


@router.post("/documents/{document_id}/versions", response_model=VersionResponse, status_code=202)
async def upload_version(
    document_id: uuid.UUID,
    file: UploadFile = File(...),
    effective_date: date | None = Form(default=None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    arq_pool: ArqRedis = Depends(get_arq_pool),
):
    """새 버전으로 교체. 새 행·새 청크가 생기고 옛 현행은 내려간다. 색인이 실패하면
    revert_current_on_failure가 옛 버전을 다시 올린다 - 색인 없는 규정이 현행이 되면 안 된다."""
    base = await db.get(Document, document_id, with_for_update=True)
    if base is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    current = await db.scalar(select(Document).where(Document.lineage_id == base.lineage_id, Document.is_current.is_(True)).with_for_update())
    latest_version = await db.scalar(select(Document.version).where(Document.lineage_id == base.lineage_id).order_by(Document.version.desc()).limit(1)) or 1
    filename = (file.filename or "").strip()
    try:
        extension = validate_upload_metadata(filename, file.content_type or "", file.size or 0, settings.max_upload_size_mb)
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    head = await file.read(MAGIC_SNIFF_BYTES)
    try:
        validate_magic_bytes(extension, head)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await file.seek(0)
    new = Document(
        collection_id=base.collection_id, folder_id=base.folder_id, filename=filename[:500], file_type=extension,
        size_bytes=0, storage_path="", status="uploaded", uploaded_by=admin.id,
        lineage_id=base.lineage_id, version=latest_version + 1, is_current=False, effective_date=effective_date,
    )
    db.add(new)
    await db.flush()
    try:
        path, size, sha256 = await save_upload_stream(settings.upload_dir, str(new.id), extension, file, max_bytes=settings.max_upload_size_mb * 1024 * 1024)
    except UploadTooLarge as exc:
        await db.rollback()
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    if current is not None and current.sha256 == sha256:
        await db.rollback()
        await delete_document_files(settings.upload_dir, str(new.id))
        raise HTTPException(status_code=409, detail="현행 버전과 내용이 같은 파일입니다. 바뀐 파일을 올려 주세요.")
    new.storage_path, new.size_bytes, new.sha256 = str(path), size, sha256
    # 교체: 옛 현행을 내리고 새 버전을 올린다(부분 유니크라 순서가 중요 - flush 사이).
    if current is not None:
        current.is_current = False
        current.superseded_at = datetime.now(UTC)
        await db.flush()
    new.is_current = True
    await db.commit()
    try:
        await enqueue_document_processing(arq_pool, str(new.id))
    except Exception:
        logger.exception("failed to enqueue version processing")
        new.status = "failed"
        new.error_message = "작업 대기열에 넣지 못했습니다."
        await revert_current_on_failure(db, new)
        await db.commit()
    await db.refresh(new)
    log_event(logger, "document_version_uploaded", document_id=str(new.id), lineage_id=str(new.lineage_id), version=new.version, admin_id=str(admin.id))
    return _version_response(new, admin.email)


@router.post("/documents/{document_id}/versions/{version_id}/activate", response_model=VersionResponse)
async def activate_version(
    document_id: uuid.UUID, version_id: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)
):
    """되돌리기. 그 버전을 현행으로, 계보의 나머지는 내린다. 색인이 끝난 버전만 현행이 될 수 있다."""
    base = await db.get(Document, document_id)
    target = await db.get(Document, version_id, with_for_update=True)
    if base is None or target is None or target.lineage_id != base.lineage_id:
        raise HTTPException(status_code=404, detail="그 버전을 찾을 수 없습니다.")
    if target.status != "indexed":
        raise HTTPException(status_code=400, detail="색인이 끝난 버전만 현행으로 만들 수 있습니다.")
    await db.execute(
        update(Document).where(Document.lineage_id == base.lineage_id, Document.is_current.is_(True))
        .values(is_current=False, superseded_at=datetime.now(UTC))
    )
    await db.flush()
    target.is_current = True
    target.superseded_at = None
    await db.commit()
    await db.refresh(target)
    log_event(logger, "document_version_activated", document_id=str(target.id), version=target.version, admin_id=str(admin.id))
    return _version_response(target, admin.email)
