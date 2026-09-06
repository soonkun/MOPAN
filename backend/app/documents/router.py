import logging
import uuid
from pathlib import Path
from urllib.parse import quote

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import get_readable_document
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
from app.models.chunk import Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.schemas.collection import CollectionCreate, CollectionResponse, CollectionUpdate
from app.llm.base import LLMProvider
from app.schemas.document import (
    ChunkListResponse,
    ChunkResponse,
    DocumentReprocess,
    DocumentResponse,
)

logger = logging.getLogger("mopan.documents")
router = APIRouter(prefix="/api", tags=["documents"])

ENQUEUE_FAILED_MESSAGE = "처리 작업을 큐에 등록하지 못했습니다. 잠시 후 다시 시도해 주세요."
# 분류, not 컬렉션: 분류 is the word the documents screen already puts in front of
# the user - the table column header, the upload label and the filter all say it.
# The management screen shows the same rows, so it has to say the same word.
COLLECTION_NOT_FOUND_MESSAGE = "분류를 찾을 수 없습니다."
DUPLICATE_COLLECTION_MESSAGE = "같은 이름의 분류가 이미 있습니다. 다른 이름을 입력해 주세요."


def _document_list_query():
    # chunk_count via a correlated subquery, not one extra SELECT per row.
    chunk_count = (
        select(func.count(Chunk.id))
        .where(Chunk.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    return (
        select(Document, Collection.name, User.email, chunk_count)
        .join(Collection, Collection.id == Document.collection_id)
        .join(User, User.id == Document.uploaded_by)
    )


def _to_response(document, collection_name, uploader_email, chunk_count) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        collection_id=document.collection_id,
        collection_name=collection_name,
        filename=document.filename,
        file_type=document.file_type,
        size_bytes=document.size_bytes,
        status=document.status,
        error_message=document.error_message,
        uploader_email=uploader_email,
        chunk_count=chunk_count or 0,
        structure=document.structure or {},
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post("/collections", response_model=CollectionResponse)
async def create_collection(
    payload: CollectionCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    collection = Collection(
        name=payload.name,
        description=payload.description,
        chunking=payload.chunking or {},
        created_by=admin.id,
    )
    db.add(collection)
    try:
        await db.commit()
    except IntegrityError as exc:
        # uq_collections_name. Caught rather than pre-checked with a SELECT: the
        # check-then-insert version still loses to a concurrent insert and turns
        # into the same 500, just less often.
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_COLLECTION_MESSAGE) from exc
    await db.refresh(collection)
    return collection


@router.get("/collections", response_model=list[CollectionResponse])
async def list_collections(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.scalars(select(Collection).order_by(Collection.created_at))
    return list(result)


@router.patch("/collections/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: uuid.UUID,
    payload: CollectionUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail=COLLECTION_NOT_FOUND_MESSAGE)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(collection, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_COLLECTION_MESSAGE) from exc
    await db.refresh(collection)
    return collection


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Refuses while the collection still holds documents. The last remaining
    collection IS deletable: an admin left with none simply creates one, which is
    the same click the empty state already offers, whereas a floor of one would
    make a single mis-named collection permanent."""
    # FOR UPDATE, not a bare get: inserting a document takes FOR KEY SHARE on the
    # collections row it references, which conflicts with this lock. Without it a
    # concurrent upload commits between the count below and the DELETE, and
    # documents.collection_id is ON DELETE CASCADE with chunks cascading from
    # documents - so the row, its chunks and the admin's just-uploaded file (left
    # orphaned under upload_dir) all disappear with no error anywhere.
    collection = await db.get(Collection, collection_id, with_for_update=True)
    if collection is None:
        raise HTTPException(status_code=404, detail=COLLECTION_NOT_FOUND_MESSAGE)

    document_count = await db.scalar(
        select(func.count(Document.id)).where(Document.collection_id == collection_id)
    )
    if document_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"문서 {document_count}개가 들어 있는 분류는 삭제할 수 없습니다. "
                "먼저 문서를 삭제해 주세요."
            ),
        )

    await db.delete(collection)
    await db.commit()
    log_event(logger, "collection_deleted", collection_id=str(collection_id))


@router.post("/documents", response_model=DocumentResponse, status_code=202)
async def upload_document(
    collection_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    arq_pool: ArqRedis = Depends(get_arq_pool),
):
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail=COLLECTION_NOT_FOUND_MESSAGE)

    filename = (file.filename or "").strip()
    try:
        extension = validate_upload_metadata(
            filename, file.content_type or "", file.size or 0, settings.max_upload_size_mb
        )
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

    document = Document(
        collection_id=collection_id,
        filename=filename[:500],
        file_type=extension,
        size_bytes=0,
        storage_path="",
        status="uploaded",
        uploaded_by=admin.id,
    )
    db.add(document)
    await db.flush()

    # MEMORY is bounded here, DISK is not. Starlette spools each multipart part to
    # a SpooledTemporaryFile(max_size=1MB) before this handler runs, and
    # save_upload_stream writes in CHUNK_BYTES pieces, so nothing ever holds the
    # whole body in RAM. But an oversized body is still written to the spool's temp
    # file in full before max_bytes can reject it. Capping that needs a body limit
    # on a proxy in front of this app, and there is none: Task 24 exposes the
    # stack with `cloudflared tunnel --url http://localhost:3000` straight to
    # Next, whose middlewareClientMaxBodySize only bounds its own rewrite hop. If
    # a reverse proxy is ever added, raise its limit (nginx: client_max_body_size)
    # to match settings.max_upload_size_mb.
    try:
        path, size = await save_upload_stream(
            settings.upload_dir,
            str(document.id),
            extension,
            file,
            max_bytes=settings.max_upload_size_mb * 1024 * 1024,
        )
    except UploadTooLarge as exc:
        await db.rollback()
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    document.storage_path = str(path)
    document.size_bytes = size
    await db.commit()

    try:
        await enqueue_document_processing(arq_pool, str(document.id))
    except Exception:
        # Never return success for a job that was silently dropped: the document
        # would sit at "uploaded" forever with no explanation. The stored file is
        # unreachable too - nothing will ever parse it - so drop it rather than
        # leak disk under a row that has no retry route in Slice 1.
        logger.exception("failed to enqueue document processing")
        document.status = "failed"
        document.error_message = ENQUEUE_FAILED_MESSAGE
        await db.commit()
        await delete_document_files(settings.upload_dir, str(document.id))
        await db.refresh(document)
        return JSONResponse(
            status_code=503,
            # `detail` as well as the document body: the client reads `detail` for
            # the banner text, and without it a 503 with a perfectly good Korean
            # error_message rendered as the browser's own "Service Unavailable".
            content={
                **jsonable_encoder(_to_response(document, collection.name, admin.email, 0)),
                "detail": ENQUEUE_FAILED_MESSAGE,
            },
        )

    await db.refresh(document)
    log_event(logger, "document_uploaded", document_id=str(document.id), size_bytes=size)
    return _to_response(document, collection.name, admin.email, 0)


@router.get("/documents", response_model=list[DocumentResponse])
async def list_documents(
    collection_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    query = _document_list_query().order_by(Document.created_at.desc())
    if collection_id is not None:
        query = query.where(Document.collection_id == collection_id)
    rows = (await db.execute(query)).all()
    return [_to_response(*row) for row in rows]


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    row = (await db.execute(_document_list_query().where(Document.id == document_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return _to_response(*row)


@router.post("/documents/{document_id}/reprocess", response_model=DocumentResponse, status_code=202)
async def reprocess_document(
    document_id: uuid.UUID,
    payload: DocumentReprocess | None = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    arq_pool: ArqRedis = Depends(get_arq_pool),
):
    """Run the pipeline over the stored file again, optionally recording first
    what a person says this document's character is.

    THE FILE IS NOT TOUCHED - this is the button behind 성격 바꾸기, and re-cutting
    a document that is already here is the whole point. It is also why `structure`
    is MERGED rather than replaced: `detected`, the level counts and the citation
    numbers are what the screen shows beside the person's choice, and the pipeline
    reads `override` back off this same dict on the next run and keeps it.
    """
    row = (await db.execute(_document_list_query().where(Document.id == document_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    document, collection_name, uploader_email, chunk_count = row

    if payload is not None:
        # A new dict, not a key assignment: `structure` is a plain JSONB column
        # with no MutableDict on it, so an in-place mutation is a change
        # SQLAlchemy never flushes.
        document.structure = {**(document.structure or {}), "override": payload.character}
    document.status = "uploaded"
    document.error_message = None
    await db.commit()

    try:
        await enqueue_document_processing(arq_pool, str(document.id))
    except Exception:
        # Same contract as the upload endpoint: a silently dropped job leaves the
        # row at "uploaded" forever with nothing on screen to explain it. The
        # stored file STAYS, unlike upload's - the document and its chunks are
        # still here and still fine, so deleting the original would turn a queue
        # hiccup into an unrecoverable one.
        logger.exception("failed to enqueue document reprocessing")
        document.status = "failed"
        document.error_message = ENQUEUE_FAILED_MESSAGE
        await db.commit()
        await db.refresh(document)
        return JSONResponse(
            status_code=503,
            content={
                **jsonable_encoder(_to_response(document, collection_name, uploader_email, chunk_count)),
                "detail": ENQUEUE_FAILED_MESSAGE,
            },
        )

    await db.refresh(document)
    log_event(logger, "document_reprocess_enqueued", document_id=str(document.id))
    return _to_response(document, collection_name, uploader_email, chunk_count)


@router.get("/documents/{document_id}/chunks", response_model=ChunkListResponse)
async def list_chunks(
    document_id: uuid.UUID,
    request: Request,
    q: str | None = None,
    offset: int = 0,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """한 장씩, 그리고 유사도로 찾기.

    전량 반환이던 시절 만행짜리 표(19,994청크)가 한 응답으로 내려가 브라우저를
    죽였다(소유자 실사고). 기본은 chunk_index 순 100개씩이고, 스크롤 끝에서
    다음 장을 청한다.

    `q`가 있으면 이 문서 안에서 질문이 검색을 탈 때와 같은 공간(임베딩 코사인)
    으로 순위를 매겨 돌려준다 - 정확일치가 아니라 유사도라, 표현이 달라도
    "이 내용이 어느 청크에 있나"에 답한다. 임베딩 호출이 한 번 들지만 질문
    한 개 값이다. 아직 임베딩이 없는 청크(색인 중)는 순위를 매길 수 없어
    빠진다."""
    await get_readable_document(db, document_id)
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    total = (
        await db.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
        )
    ) or 0
    query = q.strip() if q else ""
    if query:
        # q가 있을 때만 읽는다(늦은 접근): lifespan이 만든 하나이고, 목록만
        # 넘기는 대부분의 호출은 임베딩과 무관하다. chat/router.py의 동명
        # 의존성을 import하면 채팅 라우터 전체가 끌려온다.
        llm_provider: LLMProvider = request.app.state.llm_provider
        vector = (await llm_provider.embed([query]))[0]
        # 정확 스캔 강제(실측 0행 실사고): HNSW 인덱스는 전역 최근접 ef_search
        # 개를 먼저 뽑고 WHERE를 나중에 거르므로, 코퍼스 전체의 이웃이 전부 이
        # 문서 밖이면 결과가 비어 버린다. 한 문서의 청크는 수천 행이라 정확
        # 코사인 정렬이 밀리초면 되고, SET LOCAL이라 이 트랜잭션 밖(검색 본선의
        # 전역 HNSW 질의)에는 손대지 않는다.
        await db.execute(sql_text("SET LOCAL enable_indexscan = off"))
        rows = await db.scalars(
            select(Chunk)
            .where(Chunk.document_id == document_id, Chunk.embedding.is_not(None))
            .order_by(Chunk.embedding.cosine_distance(vector))
            .offset(offset)
            .limit(limit)
        )
    else:
        rows = await db.scalars(
            select(Chunk)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
            .offset(offset)
            .limit(limit)
        )
    return ChunkListResponse(
        total=total, items=[ChunkResponse.model_validate(row) for row in list(rows)]
    )


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """The stored original, under the name it was uploaded with. Same
    authorization as GET /api/documents/{id} - any authenticated user - because
    the corpus is shared by design and every one of them can already read the
    chunks this file was cut into.

    Headers follow GET /api/attachments/{id}/content: octet-stream and
    Content-Disposition: attachment for EVERY type, because .html is an accepted
    upload and /api/* is proxied same-origin by Next, so echoing a stored file
    back under its own Content-Type would be stored XSS on the app's own origin.
    nosniff stops a browser second-guessing that."""
    document = await get_readable_document(db, document_id)
    # Checked rather than left to FileResponse, which raises RuntimeError from
    # inside the response and answers 500. This case is reachable and has already
    # bitten this project: a locally-run backend and the Docker backend do not
    # share UPLOAD_DIR (host path vs named volume), so a document uploaded to one
    # is a row the other lists and a file it cannot open. The first clause covers
    # a row whose upload never completed: storage_path is "" there, and Path("")
    # is the current directory rather than a missing path.
    if not document.storage_path or not Path(document.storage_path).is_file():
        raise HTTPException(status_code=404, detail="원본 파일을 더 이상 찾을 수 없습니다.")
    return FileResponse(
        document.storage_path,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(document.filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/chunks/{chunk_id}", response_model=ChunkResponse)
async def get_chunk(
    chunk_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Backs citation click-through: the modal shows the full chunk, not the
    300-character snippet."""
    chunk = await db.get(Chunk, chunk_id)
    if chunk is None:
        # Worded for where it is actually read: this detail renders inside the
        # chat citation modal, which is labelled 출처. 청크 is an internal word
        # the chat surface never uses anywhere else.
        raise HTTPException(status_code=404, detail="출처 내용을 불러올 수 없습니다.")
    return chunk


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    document = await get_readable_document(db, document_id)
    await db.delete(document)  # chunks cascade via ON DELETE CASCADE
    await db.commit()
    await delete_document_files(settings.upload_dir, str(document_id))
