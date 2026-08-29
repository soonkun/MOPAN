from abc import ABC, abstractmethod

from app.retrieval.evidence import RetrievedChunk


class Reranker(ABC):
    """Operates on domain objects, never ORM models, and may reorder AND rescore.
    It is called on the full candidate set before top-N truncation, otherwise a
    real cross-encoder could never promote anything.

    The RETURNED ORDER IS AUTHORITATIVE: the caller truncates the list as it comes
    back and never re-sorts by `rerank_score`, so an implementation that sets
    scores without reordering is a silent no-op."""

    @abstractmethod
    async def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]: ...


class NoneReranker(Reranker):
    """Slice 1 default: keeps the RRF-fused order as-is.

    It deliberately leaves `rerank_score` at None rather than copying the RRF
    score into it. A trace that shows a reranker score is claiming a reranker
    ran; "no reranker" has to stay distinguishable from "the reranker agreed".
    """

    async def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return candidates
