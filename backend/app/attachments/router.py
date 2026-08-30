import logging
import uuid
from urllib.parse import quote

from anyio import to_thread
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments.service import attachment_root, get_owned_attachment, no_vision_message
from app.auth.dependencies import get_current_user
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.documents.storage import delete_document_files, save_upload_stream
from app.documents.validation import (
    ATTACHMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MAGIC_SNIFF_BYTES,
    UploadTooLarge,
    UploadValidationError,
    validate_magic_bytes,
    validate_upload_metadata,
)
from app.models.attachment import Attachment
from app.models.user import User
from app.rag.parsers import get_parser
from app.schemas.chat import AttachmentResponse

logger = logging.getLogger("mopan.attachments")
router = APIRouter(prefix="/api", tags=["attachments"])

UNREADABLE_MESSAGE = "첨부파일을 읽지 못했습니다. 파일이 손상되었는지 확인해 주세요."
NO_TEXT_MESSAGE = "첨부한 문서에서 읽을 수 있는 텍스트를 찾지 못했습니다. 다른 파일을 첨부해 주세요."
ALREADY_SENT_MESSAGE = "이미 전송된 첨부파일은 삭제할 수 없습니다."


@router.post("/attachments", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """Any authenticated user, unlike POST /api/documents. The admin gate there
    exists because a corpus document becomes evidence for everybody's answers; an
    attachment is scoped to one conversation owned by one user, so the poisoning
    argument does not apply to it."""
    filename = (file.filename or "").strip()
    try:
        extension = validate_upload_metadata(
            filename,
            file.content_type or "",
            file.size or 0,
            settings.max_attachment_size_mb,
            allowed=ATTACHMENT_EXTENSIONS,
        )
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    kind = "image" if extension in IMAGE_EXTENSIONS else "document"
    # Refused here rather than at answer time, so the user is told while attaching
    # rather than after composing a whole message around a thumbnail.
    #
    # Against the WHOLE allowlist, not the default model: with a per-request model
    # the user may well pick a vision model for this very question, and gating on
    # ANSWER_MODEL alone would refuse the upload for a model they never chose. It
    # is no longer the check that makes an image part unable to reach a blind
    # model - POST /api/chat owns that, where the choice is actually known - it is
    # the early "no model here can see at all" one.
    if kind == "image" and not settings.any_model_supports_vision:
        raise HTTPException(status_code=400, detail=no_vision_message(settings.answer_model))

    head = await file.read(MAGIC_SNIFF_BYTES)
    try:
        validate_magic_bytes(extension, head)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await file.seek(0)

    attachment = Attachment(
        user_id=user.id,
        filename=filename[:500],
        content_type=(file.content_type or "application/octet-stream")[:255],
        size_bytes=0,
        storage_path="",
        kind=kind,
    )
    db.add(attachment)
    await db.flush()

    root = attachment_root(settings.upload_dir)
    try:
        path, size = await save_upload_stream(
            root,
            str(attachment.id),
            extension,
            file,
            max_bytes=settings.max_attachment_size_mb * 1024 * 1024,
        )
    except UploadTooLarge as exc:
        await db.rollback()
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    attachment.storage_path = str(path)
    attachment.size_bytes = size

    if kind == "document":
        parser = get_parser(extension)
        try:
            parsed = await to_thread.run_sync(parser.parse, str(path))
        except Exception as exc:
            logger.exception("attachment parse failed")
            await db.rollback()
            await delete_document_files(root, str(attachment.id))
            raise HTTPException(status_code=400, detail=UNREADABLE_MESSAGE) from exc
        text = "\n".join(block.text for block in parsed.blocks if block.text.strip())
        if not text.strip():
            # A scanned PDF parses fine and yields nothing. Accepting it would put
            # an attachment chip on screen that contributes literally nothing to
            # the answer, with no way for the user to tell.
            await db.rollback()
            await delete_document_files(root, str(attachment.id))
            raise HTTPException(status_code=400, detail=NO_TEXT_MESSAGE)
        attachment.extracted_text = text

    await db.commit()
    log_event(logger, "attachment_uploaded", attachment_id=str(attachment.id), kind=kind, size_bytes=size)
    return attachment


@router.get("/attachments/{attachment_id}", response_model=AttachmentResponse)
async def get_attachment(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    return await get_owned_attachment(db, attachment_id, user)


@router.get("/attachments/{attachment_id}/content")
async def get_attachment_content(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Backs the composer thumbnail and the attachment chips on a reloaded
    transcript."""
    attachment = await get_owned_attachment(db, attachment_id, user)
    # .html is an accepted attachment type and /api/* is proxied same-origin by
    # Next, so serving a stored file back under its own Content-Type would be
    # stored XSS on the app's own origin. Only the four image types - none of them
    # script-bearing, SVG is deliberately not in IMAGE_MIME - render inline;
    # everything else is an octet-stream download. nosniff stops a browser
    # second-guessing either one.
    inline = attachment.kind == "image"
    disposition = "inline" if inline else "attachment"
    return FileResponse(
        attachment.storage_path,
        media_type=attachment.content_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(attachment.filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """The composer's X button. Without it every removed file would sit as an
    orphan until a cleanup job that does not exist yet."""
    attachment = await get_owned_attachment(db, attachment_id, user)
    if attachment.message_id is not None:
        # 409, not the 404 an unowned id gets: this row is the caller's own and
        # they can see it, so there is nothing to conceal - only a transcript to
        # avoid rewriting.
        raise HTTPException(status_code=409, detail=ALREADY_SENT_MESSAGE)
    await db.delete(attachment)
    await db.commit()
    await delete_document_files(attachment_root(settings.upload_dir), str(attachment_id))
