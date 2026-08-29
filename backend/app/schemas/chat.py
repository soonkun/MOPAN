import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    collection_ids: list[uuid.UUID] | None = None


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
    created_at: datetime

    model_config = {"from_attributes": True}
