import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

MAX_FOLDER_DEPTH = 8


class Folder(Base):
    """컬렉션 안의 폴더. path = 조상 id들 + 자기 id를 '/'로 이어 붙인 문자열('/a/b/'),
    하위 트리 = path LIKE '{path}%'. 마이그레이션 0020의 WHY 참조."""

    __tablename__ = "folders"
    __table_args__ = (
        UniqueConstraint("collection_id", "parent_id", "name", name="uq_folders_sibling_name"),
        CheckConstraint("depth BETWEEN 1 AND 8", name="ck_folders_depth"),
        Index("ix_folders_path", "path", postgresql_ops={"path": "text_pattern_ops"}),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("folders.id", ondelete="RESTRICT"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
