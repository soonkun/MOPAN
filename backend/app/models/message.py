import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.attachment import Attachment
from app.models.base import Base
from app.models.feedback import MessageFeedback

MESSAGE_ROLES = ("user", "assistant")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    # viewonly: the claim is a single conditional UPDATE (app/attachments/service.py)
    # whose `message_id IS NULL` predicate is the double-claim guard, and a writable
    # relationship would offer a second path that skips it. lazy="selectin" because
    # MessageResponse serialises this and the session is async, where a lazy load
    # at attribute access raises MissingGreenlet.
    attachments: Mapped[list[Attachment]] = relationship(
        lazy="selectin", order_by=Attachment.created_at, viewonly=True
    )

    # A list, not uselist=False, even though a message can only ever carry one
    # rating today: a conversation is owner-scoped, so the only person who can
    # rate this message is its owner, and uq_message_feedback_message_user makes
    # that one row. Modelling it as one-to-one would bake that reasoning into the
    # mapper, and the day an admin view is allowed to rate somebody else's answer
    # a second row would raise inside serialisation instead of being ignored.
    # viewonly and selectin for the same two reasons `attachments` is: the write
    # is a single ON CONFLICT statement whose constraint is the guard, and a lazy
    # load at attribute access raises MissingGreenlet on an async session.
    feedback: Mapped[list[MessageFeedback]] = relationship(lazy="selectin", viewonly=True)

    # Observability seam (Slice 5 reads these; Slice 4 fills prompt_version).
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # WHICH WORKFLOW ANSWERED, beside the model and the prompt version because it
    # is the same kind of fact: what this answer was produced under. NULL means no
    # workflow was named - the app behaving exactly as it did before any of this
    # existed - which is also every row written before migration 0008.
    #
    # A NAME, not a foreign key into `workflows`, and that is the deliberate part:
    # `model` and `prompt_name` are already denormalised strings for this reason.
    # A workflow is configuration an admin deletes when it stops being useful, and
    # a transcript that answers "which workflow said this" with a 404 - or worse,
    # cascades the message away with it - is not a record. uq_workflows_name makes
    # the name identify one row while it exists, and the string outlives it.
    workflow_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # WHICH VERSION of it. An integer for the same reason the name is a string:
    # `workflow_versions` rows go away with their workflow, and "answered by
    # 현장 도우미 v2" has to stay readable afterwards. NULL on every row written
    # before Slice 6 and on every answer no workflow produced.
    workflow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    usage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What the columns above could NOT hold, and the reason the trace screen was
    # not free: `citations` records only the evidence the model actually cited,
    # and the per-stage scores Slice 1 kept separate (`vector_rank`,
    # `keyword_rank`, `rrf_score`, `rerank_score`) live in Evidence.metadata in
    # memory and reached no column at all. Above all, evidence that was RETRIEVED
    # and then CUT by ANSWER_CONTEXT_TOKEN_BUDGET left no record anywhere - and
    # "it was rank 9 and the budget stopped at 8" is the single most common
    # answer to "why did it not use my document".
    #
    # JSONB, not a `trace_evidence` table, and that is the Slice 3 seam: an
    # execution plan and its MCP steps are a new key in this object, not a
    # migration. The shape is written by chat.service.build_trace and read by
    # app/schemas/observability.py, which tolerates {} for every message written
    # before this column existed.
    trace: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    # clock_timestamp(), NOT now(): now() is transaction start time, so the user
    # and assistant messages written in one commit would share a timestamp and
    # history ordering would be non-deterministic.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
