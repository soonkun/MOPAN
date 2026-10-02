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


async def get_readable_document(
    db: AsyncSession, document_id: uuid.UUID, user: User | None = None
) -> Document:
    """공용 문서(owner_id NULL)는 모든 사용자가 읽는다 - 인용 클릭이 누구에게나 열리는 이유.
    개인 문서는 소유자만. 남의 개인 문서는 403이 아니라 404 - 그 id가 존재한다는 사실도 알리지
    않는다(대화와 같은 규칙)."""
    document = await db.get(Document, document_id)
    foreign = user is not None and document is not None and document.owner_id not in (None, user.id)
    if document is None or foreign:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return document


def can_manage_document(document: Document, user: User) -> bool:
    """지우기·다시 처리: 관리자 또는 그 개인 문서의 소유자."""
    return user.role == "admin" or document.owner_id == user.id
