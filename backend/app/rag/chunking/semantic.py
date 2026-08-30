from anyio import to_thread

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
       as the result still fits under BOTH of pass 1's size bounds - the token
       ceiling and the character target.

    Pass 2 can only DELETE a boundary pass 1 drew; it never creates one. So the
    document detail view shows STRUCTURE-aware chunking, and only demonstrates
    semantic merging on a document where this pass actually fires. On the two
    documents in the dev database it fires zero times: over their 10 stored
    vectors all 8 adjacent-pair cosines measure 0.216-0.468 against the 0.75
    default, and the pass-1 output those vectors came from is the stored chunking
    unchanged (PDF 122/111/178/123 tokens, markdown 12/123/119/66/124/73 - and the
    honest spread of that markdown file is 66 to 124 tokens, not the 66-vs-119+
    contrast an earlier note drew, because chunk 5 is 73). Those rows predate the
    size pass's heading-orphan fix, which is where their 12-token first chunk came
    from; re-parsed today the same file yields 5 candidates, 136/119/66/124/73.

    That is structural, not a misconfiguration - and the merge pass can only ever
    delete a heading boundary, never repair a size split. Pass 1 closes A and opens
    B at piece p exactly when `A.tokens + NEWLINE_TOKENS + tokens(p) > max` OR
    `A.chars + 1 + len(p) > target`, and B is p possibly PREFIXED with A's overlap
    tail, so `B.tokens >= tokens(p)` and `B.chars >= len(p)`; pass 2 merges exactly
    when `A.tokens + NEWLINE_TOKENS + B.tokens <= max` AND
    `A.chars + 1 + B.chars <= target`. Same limits, same separator constants,
    bound for bound, so the merge predicate is still the negation of the split
    predicate: whichever bound forced the split is the conjunct that fails here,
    and a pair the size bound split can never be rejoined at any similarity. Which
    also means a candidate carrying an overlap prefix is never merged back into the
    candidate it overlaps - the merged text cannot duplicate that tail. Swept
    max_chunk_tokens 20..400 over a heading plus a 40-sentence body with cosine
    forced to 1.0 - 381 limits, zero rejoins. Which leaves only the other case:
    every merge this pass CAN perform joins two candidates pass 1 opened at
    different headings. Sweeping the threshold down over the stored embeddings
    confirms it: nothing merges anywhere until 0.45, and the first merges to
    appear are "3. 방제 시기"+"4. 보호 장비" (0.45) and "3. 방제 약제별
    효과"+"4. 결론 및 제언" (0.40) - two different sections glued together. At 0.35
    the PDF is down to 2 chunks (413 and 123 tokens) and the markdown to 4; by
    0.20 the markdown is 2. A lower threshold buys bigger chunks that mix topics,
    not better ones, so 0.75 stays. The 0.5/0.9/0.99 cases in test_chunking.py are
    synthetic one-hot vectors and pin none of this.

    The embedding call is not overhead. The pipeline reuses these vectors for
    every candidate the pass did not merge (see pipeline.py's `pending` list), so
    a zero-merge document costs exactly the one batched call it would have cost
    with no semantic pass at all.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.75,
        max_chunk_tokens: int = 1300,
        target_chars: int = 1000,
        overlap_chars: int = 150,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_chunk_tokens = max_chunk_tokens
        self.target_chars = target_chars
        self.overlap_chars = overlap_chars

    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]:
        # tiktoken assembly is CPU-bound and arq runs every job on one event
        # loop, so both passes go through a thread. Only the embed call in
        # between actually belongs on the loop.
        candidates = await to_thread.run_sync(
            build_size_bounded_candidates,
            blocks,
            self.max_chunk_tokens,
            self.target_chars,
            self.overlap_chars,
        )
        for candidate in candidates:
            candidate.metadata.setdefault("strategy", "semantic")
        # One candidate cannot merge with anything, so the embedding call would
        # buy nothing; the pipeline embeds it when it stores it.
        if len(candidates) <= 1:
            return candidates

        # One batched call for the whole document. Embedding each adjacent pair
        # separately would cost an API round trip per candidate.
        embeddings = await embed_fn([c.content for c in candidates])
        # A 1536-dimension cosine per adjacent pair, in pure Python.
        return await to_thread.run_sync(self._merge, candidates, embeddings)

    def _merge(self, candidates: list[ChunkCandidate], embeddings: list[list[float]]) -> list[ChunkCandidate]:
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
            combined_chars = previous.char_count + 1 + candidate.char_count
            previous_embedding = embedding

            fits = combined_tokens <= self.max_chunk_tokens and (
                not self.target_chars or combined_chars <= self.target_chars
            )
            if similarity >= self.similarity_threshold and fits:
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
