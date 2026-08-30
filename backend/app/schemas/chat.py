import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    collection_ids: list[uuid.UUID] | None = None
    # Ids from POST /api/attachments. The count ceiling is
    # MAX_ATTACHMENTS_PER_MESSAGE and is enforced in the router, not here: it is
    # operator configuration, and a Field(max_length=...) would freeze it at
    # import time and answer with an English 422 body instead of Korean.
    attachment_ids: list[uuid.UUID] | None = None
    # The answer model, chosen per question. Validated against
    # Settings.selectable_models in the router, not here, for the same two reasons
    # attachment_ids' ceiling is: it is operator configuration that a
    # Field(pattern=...) would freeze at import time, and a 422 would answer in
    # English. None means the default, ANSWER_MODEL.
    model: str | None = Field(default=None, max_length=100)


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    kind: str
    # The text itself is never returned: it is prompt input, sometimes megabytes,
    # and the composer only needs to know whether the file gave up anything.
    has_text: bool = Field(validation_alias="extracted_text")
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("has_text", mode="before")
    @classmethod
    def _has_text_from_extract(cls, value: object) -> bool:
        return bool(value)


class AnswerModelResponse(BaseModel):
    """One entry of GET /api/models. `label` falls back to the id, so a model an
    operator allowlists that MODEL_LABELS has never heard of still renders."""

    id: str
    label: str
    is_default: bool


class ConversationResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list[dict]
    # Empty on every assistant message and on any user turn sent without files.
    # A reloaded transcript has no other way to show what was attached.
    attachments: list[AttachmentResponse] = []
    # What actually answered - the provider's resolved id, so "gpt-4o" comes back
    # as "gpt-4o-2024-08-06". None on every user turn, and on assistant turns
    # written before the answer model became a per-request choice. Without it a
    # reloaded conversation cannot say which model gave which answer, which is the
    # whole point of being able to pick one.
    model: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
