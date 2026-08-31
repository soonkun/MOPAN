import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    # HOW THIS COLLECTION'S DOCUMENTS ARE CUT, when they are not cut as prose.
    # `{}` - the default - means "as prose", i.e. whatever CHUNKING_STRATEGY the
    # admin has selected deployment-wide, which is every collection's behaviour
    # until somebody changes this one.
    #
    # Per COLLECTION rather than per deployment because a collection is what holds
    # one kind of document. A goods-classification table wants cutting on its class
    # markers and a manual of prose does not, and they are in different collections
    # exactly so that they can differ.
    #
    # JSONB rather than columns because the keys belong to the strategy: the
    # section-marker cutter needs `marker`, `head_line` and `break_before`, and the
    # next strategy will need something else. `app/rag/chunking/table.py:resolve`
    # is the reader and the validator, and app/schemas/collection.py calls it on
    # the way in so that a pattern that cannot compile is rejected at the API and
    # not at the next upload.
    chunking: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
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
