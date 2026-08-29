import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation
from app.models.document import Document
from app.models.user import User


async def get_owned_conversation(db: AsyncSession, conversation_id: uuid.UUID, user: User) -> Conversation:
    """404, not 403, when the row is missing OR not owned - a 403 would confirm
    that somebody else's conversation id exists."""
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
    return conversation


async def get_readable_document(db: AsyncSession, document_id: uuid.UUID) -> Document:
    """Documents are a shared corpus: any authenticated user may read one, which
    is what makes citation click-through work for everyone. Writes are admin-only
    (see require_admin)."""
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return document
