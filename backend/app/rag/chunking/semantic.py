from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.structure import NEWLINE_TOKENS, build_size_bounded_candidates


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class StructureSemanticChunking(ChunkingStrategy):
    """Two passes.

    1. Size-bounded structure pass (Task 9): headings and the token limit both
       open new candidates, and oversized blocks are split on sentence
       boundaries. Without this a heading-less PDF becomes one chunk holding the
       entire document, which then exceeds the embedding model's input limit.
    2. Semantic merge pass: adjacent candidates whose embeddings are similar
       enough that splitting them would break one idea in two get merged, as long
       as the result still fits under the token limit.
    """

    def __init__(self, similarity_threshold: float = 0.75, max_chunk_tokens: int = 500):
        self.similarity_threshold = similarity_threshold
        self.max_chunk_tokens = max_chunk_tokens

    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]:
        candidates = build_size_bounded_candidates(blocks, self.max_chunk_tokens)
        for candidate in candidates:
            candidate.metadata.setdefault("strategy", "semantic")
        # One candidate cannot merge with anything, so the embedding call would
        # buy nothing; the pipeline embeds it when it stores it.
        if len(candidates) <= 1:
            return candidates

        # One batched call for the whole document. Embedding each adjacent pair
        # separately would cost an API round trip per candidate.
        embeddings = await embed_fn([c.content for c in candidates])

        merged: list[ChunkCandidate] = []
        # The previous candidate's OWN pass-1 embedding. Reading it off
        # merged[-1] instead does not work: a merged candidate has its embedding
        # cleared, so the fallback compares the incoming candidate with itself
        # (similarity 1.0) and absorbs everything after the first merge.
        previous_embedding: list[float] = []
        for candidate, embedding in zip(candidates, embeddings, strict=True):
            # Keep the pass-1 embedding: if this candidate is never merged, its
            # text is final and the pipeline can store this vector directly
            # instead of paying to embed the whole corpus a second time.
            candidate.embedding = embedding

            if not merged:
                merged.append(candidate)
                previous_embedding = embedding
                continue

            previous = merged[-1]
            similarity = _cosine_similarity(previous_embedding, embedding)
            # Charge the joining newline to the candidate that follows it, the
            # same accounting the size pass uses. Summing the two token counts
            # omits it, and an under-count here re-breaks the bound pass 1 just
            # enforced - measured at 9 tokens under an 8-token limit.
            combined_tokens = previous.token_count + NEWLINE_TOKENS + candidate.token_count
            previous_embedding = embedding

            if similarity >= self.similarity_threshold and combined_tokens <= self.max_chunk_tokens:
                previous.content = f"{previous.content}\n{candidate.content}"
                previous.token_count = combined_tokens
                previous.char_count = len(previous.content)
                previous.section = previous.section or candidate.section
                previous.page = previous.page if previous.page is not None else candidate.page
                # The merged text is new, so the stored vector no longer describes
                # it. None tells the pipeline to embed this one.
                previous.embedding = None
            else:
                merged.append(candidate)

        return merged
