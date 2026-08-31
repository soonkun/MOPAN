from dataclasses import dataclass, field
from typing import Literal

# "attachment" is text the user attached to this one turn. It rides the Evidence
# type rather than a parallel channel so that it inherits, for free, every defence
# build_prompt already applies to corpus text: the per-request nonce fence,
# _strip_fence_markers, and one shared token budget instead of a second one added
# on top.
SourceType = Literal["rag", "mcp", "attachment"]


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    filename: str
    content: str
    page: int | None = None
    section: str | None = None
    # The chunk's position in its document, which is what neighbour expansion
    # addresses a neighbour BY. Optional because a Reranker or a test may build a
    # RetrievedChunk by hand; expansion skips an item that has none rather than
    # guessing an index.
    chunk_index: int | None = None
    # Per-stage scores kept separate. Collapsing them into one `score` means
    # Slice 5's trace view has to change the retrieval return type.
    vector_rank: int | None = None
    keyword_rank: int | None = None
    # Did the DENSE arm and the KEYWORD arm both return this chunk, for any query
    # variant? Not derivable from the two ranks above: those are the original
    # query's positions, kept that way so a trace explains itself, while under
    # query expansion the arms may only have agreed on a rewrite. The
    # weak-evidence detector reads this and nothing else for its second signal.
    corroborated: bool = False
    # Did ANY candidate in the fused set - not just the top_n that survived the
    # truncation - get found by both arms? The field above answers "was THIS
    # chunk corroborated", which is a different question from the one the
    # weak-evidence detector asks, and reading the first for the second is what
    # made the detector fire on 상표등록출원서 + 류 + 지정상품: the corroborated
    # chunk sat at fused rank 8-9 and RETRIEVAL_TOP_N=5 cut it, so the delivered
    # five carried False five times over while retrieval had in fact agreed.
    # Same value on every item of one search, because it is a property of the
    # search and not of the chunk.
    candidates_corroborated: bool = False
    # HOW MANY QUERIES PRODUCED `rrf_score`. 1 for an unexpanded search, N+1 when
    # the retry expanded. It is here because the RRF score is NOT comparable
    # across those two cases and the weak-evidence threshold is a constant: every
    # variant adds two ranked lists, so a chunk one arm alone found at rank 1 for
    # all four variants scores 4/61 = 0.0656 - four times a threshold that was
    # calibrated on two lists, where the same chunk scores 1/61 = 0.0164. Without
    # this the retry marks its own homework with a ruler that grew, and a
    # measured live case turned a clarification into a confidently wrong answer.
    variants: int = 1
    rrf_score: float = 0.0
    rerank_score: float | None = None
    # What neighbour expansion folded into `content`, one entry per neighbour.
    # Empty means this item is the stored chunk and nothing else - which is what
    # every item is when NEIGHBOR_EXPANSION is off.
    neighbors: list[dict] = field(default_factory=list)


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
            "corroborated": chunk.corroborated,
            "candidates_corroborated": chunk.candidates_corroborated,
            "variants": chunk.variants,
            "rrf_score": chunk.rrf_score,
            "rerank_score": chunk.rerank_score,
            # The identity fields above all still name the PRIMARY chunk after
            # expansion; this is the only place that says the content is wider
            # than that chunk, and it is what the trace screen shows.
            "neighbors": chunk.neighbors,
        },
    )
