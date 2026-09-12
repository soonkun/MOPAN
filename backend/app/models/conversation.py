import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(500), nullable=False, default="New Chat", server_default=text("'New Chat'")
    )
    # 멀티턴 기억(app/chat/memory.py): 원문 창 밖으로 밀려난 오래된 턴의 롤링
    # 요약과, 그 요약이 어느 메시지(created_at)까지 읽었는지. 요약은 그 시각
    # 이후의 메시지만 새로 접으므로 갱신이 중간에 죽어도 다음 턴이 이어받는다.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_through_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
