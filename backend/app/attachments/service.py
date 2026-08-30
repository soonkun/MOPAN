import base64
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.storage import read_upload
from app.documents.validation import IMAGE_MIME, extension_of
from app.models.attachment import Attachment
from app.models.user import User
from app.retrieval.evidence import Evidence

# 404 for missing, for someone else's, and for already-claimed alike - the same
# rule get_owned_conversation follows, for the same reason: a 403 or a 409 here
# would let an id probe confirm that an attachment exists.
NOT_FOUND_MESSAGE = "첨부파일을 찾을 수 없습니다."
FILE_GONE_MESSAGE = "첨부파일의 원본을 더 이상 찾을 수 없습니다."


def no_vision_message(model: str) -> str:
    """Shared by both gates: POST /api/attachments refuses an image no allowlisted
    model could ever read, and POST /api/chat refuses one sent WITH a model that
    cannot read it. Same sentence, because to the user it is the same refusal."""
    return (
        f"현재 답변 모델({model})은 이미지를 읽을 수 없습니다. "
        "이미지 대신 문서 파일을 첨부하거나 관리자에게 문의해 주세요."
    )


def attachment_root(upload_dir: Path) -> Path:
    """A subdirectory of UPLOAD_DIR, reusing the documents per-id layout wholesale
    (app/documents/storage.py). "attachments" can never collide with a document's
    directory name because that name is always a UUID."""
    return Path(upload_dir) / "attachments"


async def get_owned_attachment(db: AsyncSession, attachment_id: uuid.UUID, user: User) -> Attachment:
    attachment = await db.get(Attachment, attachment_id)
    if attachment is None or attachment.user_id != user.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE)
    return attachment


async def load_claimable(
    db: AsyncSession, attachment_ids: list[uuid.UUID], user: User
) -> list[Attachment]:
    """Every id must resolve to an unclaimed attachment owned by this user, or the
    whole request is refused. Called BEFORE /api/chat creates its conversation, so
    a bad id cannot leave a titled, empty conversation in the sidebar."""
    if not attachment_ids:
        return []
    unique = list(dict.fromkeys(attachment_ids))
    rows = (
        await db.scalars(
            select(Attachment).where(
                Attachment.id.in_(unique),
                Attachment.user_id == user.id,
                Attachment.message_id.is_(None),
            )
        )
    ).all()
    if len(rows) != len(unique):
        raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE)
    by_id = {row.id: row for row in rows}
    return [by_id[attachment_id] for attachment_id in unique]


async def claim(
    db: AsyncSession, attachment_ids: list[uuid.UUID], user_id: uuid.UUID, message_id: uuid.UUID
) -> None:
    """One conditional UPDATE, not a read-then-write: `message_id IS NULL` in the
    WHERE clause is what makes a double claim lose a race instead of quietly
    re-pointing an attachment that is already part of another turn."""
    if not attachment_ids:
        return
    result = await db.execute(
        update(Attachment)
        .where(
            Attachment.id.in_(attachment_ids),
            Attachment.user_id == user_id,
            Attachment.message_id.is_(None),
        )
        .values(message_id=message_id)
    )
    if result.rowcount != len(set(attachment_ids)):
        raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE)


def to_evidence(attachments: list[Attachment]) -> list[Evidence]:
    """Attachment text enters the prompt as ordinary Evidence, which is the whole
    security argument: it lands inside the same nonce fence, goes through the same
    _strip_fence_markers, and competes for the same token budget as corpus text. A
    user pasting "ignore previous instructions" in a PDF therefore gets exactly as
    far as an admin pasting it into a corpus document - nowhere."""
    return [
        Evidence(
            source_type="attachment",
            ref=f"attachment:{a.id}",
            content=a.extracted_text,
            # No retrieval score exists: nothing ranked this, the user chose it.
            score=None,
            metadata={"attachment_id": str(a.id), "filename": a.filename},
        )
        for a in attachments
        if a.kind == "document" and a.extracted_text
    ]


async def to_image_urls(attachments: list[Attachment]) -> list[str]:
    """data: URLs, not file paths or a public URL: the provider would have to be
    able to reach this host to fetch a URL, and these files are owner-scoped."""
    urls = []
    for attachment in attachments:
        if attachment.kind != "image":
            continue
        try:
            raw = await read_upload(attachment.storage_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=FILE_GONE_MESSAGE) from exc
        mime = IMAGE_MIME.get(extension_of(attachment.filename), attachment.content_type)
        urls.append(f"data:{mime};base64,{base64.b64encode(raw).decode()}")
    return urls
