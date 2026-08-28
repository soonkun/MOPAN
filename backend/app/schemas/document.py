import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    collection_name: str | None = None
    filename: str
    file_type: str
    size_bytes: int
    status: str
    error_message: str | None
    uploader_email: str | None = None
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChunkResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    token_count: int
    char_count: int
    page: int | None
    section: str | None
    chunk_metadata: dict

    model_config = {"from_attributes": True}


class BlockResponse(BaseModel):
    text: str
    block_type: str
    page: int | None
    section: str | None
