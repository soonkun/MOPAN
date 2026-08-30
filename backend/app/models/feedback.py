import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

FEEDBACK_RATINGS = ("up", "down")


class MessageFeedback(Base):
    """One row per (message, user). A rating is CHANGEABLE, so this is the one
    table in the project that is updated in place rather than versioned - the
    opposite of `prompts`, and for the opposite reason: a rating is a current
    opinion, not a historical record, and a user who clicks down after up means
    the second one.

    Joining it to the trace needs nothing extra: `message_id` is the assistant
    row that carries `messages.trace`, so "every down-vote since Tuesday, with
    the evidence the budget cut from each" is one join.
    """

    __tablename__ = "message_feedback"
    __table_args__ = (
        # The uniqueness IS the "one per user per message" rule. In app code it
        # would be a check-then-insert with a race between the two halves; here a
        # double click that gets two requests in flight loses the second to the
        # constraint instead of writing two rows that disagree.
        UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_user"),
        CheckConstraint("rating in ('up', 'down')", name="ck_message_feedback_rating_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rating: Mapped[str] = mapped_column(String(10), nullable=False)
    # Nullable rather than defaulted to "": the comment is optional, and an empty
    # string would be indistinguishable from "the user cleared what they wrote".
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
