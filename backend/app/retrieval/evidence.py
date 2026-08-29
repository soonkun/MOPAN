from dataclasses import dataclass, field
from typing import Literal

SourceType = Literal["rag", "mcp"]


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    content: str
    page: int | None = None
    section: str | None = None
    # Per-stage scores kept separate. Collapsing them into one `score` means
    # Slice 5's trace view has to change the retrieval return type.
    vector_rank: int | None = None
    keyword_rank: int | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None


@dataclass
class Evidence:
    """The unit `answer()` consumes. Slice 2/3 add source_type="mcp" items from
    tool calls; `answer()` itself does not change."""

    source_type: SourceType
    ref: str
    content: str
    score: float | None = None
    metadata: dict = field(default_factory=dict)


def chunk_to_evidence(chunk: RetrievedChunk) -> Evidence:
    return Evidence(
        source_type="rag",
        ref=f"chunk:{chunk.chunk_id}",
        content=chunk.content,
        # `is not None`, not truthiness: 0.0 is a legitimate cross-encoder verdict
        # ("this candidate is irrelevant"), and falling back to the RRF score there
        # would silently overrule the reranker on exactly the candidate it rejected.
        score=chunk.rerank_score if chunk.rerank_score is not None else chunk.rrf_score,
        metadata={
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "filename": chunk.filename,
            "page": chunk.page,
            "section": chunk.section,
            "vector_rank": chunk.vector_rank,
            "keyword_rank": chunk.keyword_rank,
            "rrf_score": chunk.rrf_score,
            "rerank_score": chunk.rerank_score,
        },
    )
