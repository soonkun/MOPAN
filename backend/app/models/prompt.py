import uuid
from datetime import datetime

# sqlalchemy.text is aliased: this model has a column literally called `text`,
# and the class-body assignment shadows the imported name for every line after it.
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Prompt(Base):
    """One ROW PER VERSION, never an update in place.

    `Message.prompt_version` is only meaningful while the version it names still
    exists, and the owner iterates on wording: a change that makes answers worse
    has to be revertable by activating the row that was there before, not by
    retyping it from memory. So an edit INSERTs; nothing but `is_active` is ever
    written to a row that already exists.
    """

    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompts_name_version"),
        # "Exactly one active version per name" as a DB constraint rather than
        # app code: a partial unique index makes a second active row an
        # IntegrityError, so a half-finished activation cannot leave two rows
        # active and get_prompt cannot silently pick whichever one it saw first.
        # The at-LEAST-one half is not expressible here and does not need to be -
        # get_prompt falls back to the module constant when it finds no row.
        Index("uq_prompts_name_active", "name", unique=True, postgresql_where=sa_text("is_active")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # String, not Integer, because Message.prompt_version is String(50) and the
    # two have to compare: the observability seam Slice 5 reads joins a persisted
    # answer back to the exact text it was produced from.
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa_text("false")
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable, and the ONE deliberate exception this table adds to
    # tests/test_schema.py:NULLABLE_FK_EXCEPTIONS. Version 1 is written by
    # migration 0004, which runs on a database where no user exists yet - the
    # bootstrap admin registers afterwards - so there is nobody to attribute it
    # to. NULL means "the deployment's own default", and the screen shows 시스템.
    # SET NULL rather than RESTRICT: a deleted account must not be able to make
    # the version history unreadable, and history outliving its author is exactly
    # what a version log is for.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
