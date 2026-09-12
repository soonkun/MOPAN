import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 한 항목의 상한. 기억은 "사실 한 줄"이지 문단이 아니다(app/chat/user_memory.py).
USER_MEMORY_CHARS = 300


class UserMemory(Base):
    """대화를 넘어 남는 사용자별 기억 한 줄(ChatGPT의 '메모리'와 같은 모양).

    계정에 묶이고(user_id, 계정 삭제 시 함께 삭제) 본인만 읽고 지운다. 어느 대화에서
    나왔는지(source_conversation_id)는 화면의 '출처' 표시용이며, 그 대화가 지워져도
    기억은 남는다(SET NULL)."""

    __tablename__ = "user_memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(String(USER_MEMORY_CHARS), nullable=False)
    source_conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    # clock_timestamp(): 한 턴에서 붙는 여러 줄이 now()면 같은 시각을 받아 순서가 흔들린다(messages와 같은 이유).
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()"))
