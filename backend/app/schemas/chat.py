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
    created_at: datetime

    model_config = {"from_attributes": True}
