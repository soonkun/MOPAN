import logging
import uuid

from anyio import to_thread
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
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
from app.schemas.collection import CollectionCreate, CollectionResponse
from app.schemas.document import BlockResponse, ChunkResponse, DocumentResponse

logger = logging.getLogger("mopan.documents")
router = APIRouter(prefix="/api", tags=["documents"])

ENQUEUE_FAILED_MESSAGE = "처리 작업을 큐에 등록하지 못했습니다. 잠시 후 다시 시도해 주세요."


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
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post("/collections", response_model=CollectionResponse)
async def create_collection(
    payload: CollectionCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    collection = Collection(name=payload.name, description=payload.description, created_by=admin.id)
    db.add(collection)
    await db.commit()
    await db.refresh(collection)
    return collection


@router.get("/collections", response_model=list[CollectionResponse])
async def list_collections(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.scalars(select(Collection).order_by(Collection.created_at))
    return list(result)


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
        raise HTTPException(status_code=404, detail="컬렉션을 찾을 수 없습니다.")

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
    # file in full before max_bytes can reject it. Capping that needs a
    # proxy-level client_max_body_size - deployment work, see Task 24.
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
            content=jsonable_encoder(_to_response(document, collection.name, admin.email, 0)),
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


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkResponse])
async def list_chunks(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await get_readable_document(db, document_id)
    result = await db.scalars(
        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
    )
    return list(result)


@router.get("/documents/{document_id}/structure", response_model=list[BlockResponse])
async def get_document_structure(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Left pane of the document detail view: the parsed original structure, so an
    admin can eyeball chunking quality against it. Re-parsed on demand (in a
    thread) rather than duplicating every document's text into a JSONB column."""
    # Imported here, not at module scope: app.rag.parsers lands in Task 8, and a
    # module-level import would stop app.main from importing at all until then.
    from app.rag.parsers import get_parser

    document = await get_readable_document(db, document_id)
    parser = get_parser(document.file_type)
    try:
        parsed = await to_thread.run_sync(parser.parse, document.storage_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="원본 파일을 더 이상 찾을 수 없습니다.") from exc
    return [
        BlockResponse(text=b.text, block_type=b.block_type, page=b.page, section=b.section)
        for b in parsed.blocks
    ]


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
        raise HTTPException(status_code=404, detail="청크를 찾을 수 없습니다.")
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
