import uuid

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    collection_ids: list[uuid.UUID] | None = None
    top_n: int | None = Field(default=None, ge=1, le=50)


class EvidenceResponse(BaseModel):
    source_type: str
    ref: str
    content: str
    score: float | None
    metadata: dict


class SearchResponse(BaseModel):
    query: str
    results: list[EvidenceResponse]
