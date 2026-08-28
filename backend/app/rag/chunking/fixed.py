from app.core.tokens import count_tokens
from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn


class FixedChunking(ChunkingStrategy):
    """Character-window baseline, kept so admins can compare it against the
    semantic strategy in the document detail view."""

    def __init__(self, chunk_size: int = 800, overlap: int = 100):
        # Both values are admin-configurable, so an invalid pair is reachable
        # from configuration - fail here rather than with `range() arg 3 must not
        # be zero` in the middle of a document.
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]:
        # Track where each block starts in the concatenated text so a window can
        # inherit the page/section of the block it begins in. Without this,
        # Fixed-chunked documents lose all citation provenance.
        offsets: list[tuple[int, Block]] = []
        parts: list[str] = []
        cursor = 0
        for block in blocks:
            offsets.append((cursor, block))
            parts.append(block.text)
            cursor += len(block.text) + 1  # +1 for the joining newline

        full_text = "\n".join(parts)
        if not full_text.strip():
            return []

        def block_at(position: int) -> Block:
            found = offsets[0][1]
            for start, block in offsets:
                if start <= position:
                    found = block
                else:
                    break
            return found

        candidates: list[ChunkCandidate] = []
        step = self.chunk_size - self.overlap
        for start in range(0, len(full_text), step):
            piece = full_text[start : start + self.chunk_size]
            if not piece.strip():
                continue
            origin = block_at(start)
            candidates.append(
                ChunkCandidate(
                    content=piece,
                    token_count=count_tokens(piece),
                    char_count=len(piece),
                    page=origin.page,
                    section=origin.section,
                    metadata={"strategy": "fixed"},
                )
            )
            if start + self.chunk_size >= len(full_text):
                break
        return candidates
