import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.rag.chunking.hierarchy import CHARACTERS


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
    # What the detector found and what a person said about it, verbatim from the
    # column. `{}` means nothing has been detected yet - see
    # app/models/document.py. Passed through as a dict rather than typed field by
    # field because the keys belong to the detector
    # (`app/rag/chunking/hierarchy.py:Detection.as_json`) and modelling them here
    # would be a second definition to keep in step.
    structure: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentReprocess(BaseModel):
    """POST /api/documents/{id}/reprocess. An OMITTED body means "just run it
    again"; an explicit `character` writes `structure.override`, and an explicit
    null there clears it back to whatever the content says.

    `CHARACTERS` is imported rather than restated so the select on screen, the
    422 here and the pipeline's `override in CHARACTERS` check cannot drift."""

    character: Literal[*CHARACTERS] | None = None


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
    # Read off the embedding column rather than stored separately, and a bool
    # rather than the vector itself: 1536 floats per chunk is not something the
    # UI can use, and a second column would be a second thing to keep true.
    embedded: bool = Field(validation_alias="embedding")

    model_config = {"from_attributes": True}

    @field_validator("embedded", mode="before")
    @classmethod
    def _embedded_from_vector(cls, value: object) -> bool:
        return value is not None
