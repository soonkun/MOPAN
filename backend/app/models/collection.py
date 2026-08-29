import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        # The name is the ONLY thing that distinguishes one collection from
        # another in the upload dropdown and the document table's 분류 column -
        # two rows called 일반 leave an admin guessing which one they picked.
        # Names are stripped in app/schemas/collection.py so trailing whitespace
        # cannot walk around this. Case still can; that is deliberate, a
        # lower(name) expression index is not something alembic's autogenerate
        # comparison handles cleanly and the schema drift test would flag it.
        UniqueConstraint("name", name="uq_collections_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # RESTRICT: deleting a user must not silently delete a shared collection.
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
