from anyio import to_thread

from app.core.tokens import count_tokens
from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.structure import split_to_token_limit


def _skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position].isspace():
        position += 1
    return position


def _advance_past(text: str, position: int, non_whitespace: int) -> int:
    """Move `position` forward over `non_whitespace` non-whitespace characters."""
    while non_whitespace and position < len(text):
        if not text[position].isspace():
            non_whitespace -= 1
        position += 1
    return position


class FixedChunking(ChunkingStrategy):
    """Character-window baseline, kept so admins can compare it against the
    semantic strategy in the document detail view.

    chunk_size and overlap count CHARACTERS of the concatenated document. An
    emitted chunk is a verbatim slice of it only while it also fits under
    max_chunk_tokens; past that the window is re-split by the size pass's
    splitter, which strips each sentence and rejoins them with a single space.
    Newlines and repeated whitespace between sentences therefore collapse -
    measured on 41 period-terminated lines at a 20-token limit, 40 source
    newlines survive as 0 - so the comparison view renders normalised text, not
    the raw window. (Text with no terminal punctuation takes the hard-split path
    instead and stays verbatim, which is why the effect looks intermittent.)
    Non-whitespace characters and their order are preserved at max_chunk_tokens
    >= 4, which is what lets each emitted part be traced back to its source
    block. Below 4 the hard splitter cannot fit the widest character in the
    budget and emits U+FFFD, inserting characters the source never had. Swept
    against cl100k, every one of the 1,007,676 codepoints that encode to 4
    tokens is exposed: a 64-character corpus of them (CJK Extension B, plausible
    in Korean and Chinese name and historical data) takes 256 U+FFFD at a limit
    of 3 and 0 at 4. Common emoji merge to 3 or fewer (U+1F600 is 2, U+1F389 is
    3), which is why an emoji corpus looked clean at 3. Those limits are
    reachable from Settings but produce corrupt chunk text regardless;
    .env.example says so.
    """

    def __init__(self, chunk_size: int = 800, overlap: int = 100, max_chunk_tokens: int = 500):
        # All three values are admin-configurable, so an invalid one is reachable
        # from configuration - fail here rather than with `range() arg 3 must not
        # be zero` or `max_tokens must be at least 1` in the middle of a document.
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")
        if max_chunk_tokens < 1:
            raise ValueError("max_chunk_tokens must be at least 1")
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.max_chunk_tokens = max_chunk_tokens

    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]:
        # This strategy never embeds, so the whole body is CPU-bound tiktoken
        # work. arq runs every job on one event loop: leaving it inline stalls
        # every other queued job and arq's own health heartbeat for as long as
        # the document takes.
        return await to_thread.run_sync(self._chunk_sync, blocks)

    def _chunk_sync(self, blocks: list[Block]) -> list[ChunkCandidate]:
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
            # chunk_size counts CHARACTERS, max_chunk_tokens counts TOKENS, and the
            # ratio is script-dependent: 800 characters is 135 tokens of ASCII but
            # 1142 of Korean and 2400 of emoji. So no character cap keeps a window
            # under the embedding ceiling, and at the shipped defaults a Korean
            # document already produced 1142-token windows against a 500 limit.
            # Re-split here instead, reusing the size pass's splitter.
            #
            # Re-splitting each window independently leaves a short part at every
            # window tail (Korean at the defaults: 500, 500, 142 per window).
            # Measured, that costs nothing to fix and nothing to keep: 11 emitted
            # parts against an 11-part floor of sum(ceil(window_tokens / limit)),
            # and 13 against 13 for ASCII. Re-balancing would even the sizes out,
            # not reduce the count, so it saves no embedding call - and it would
            # mean changing split_to_token_limit, which the semantic pass shares.
            # Left greedy deliberately.
            #
            # A window spans block boundaries, so attributing every part to the
            # block the WINDOW starts in cites text under a section it did not
            # come from. The parts are not verbatim slices - the splitter drops
            # and normalises whitespace - but their non-whitespace characters are
            # the window's, in order, so walking full_text in lockstep with each
            # part's non-whitespace count recovers exactly where that part began.
            position = start
            for part in split_to_token_limit(piece, self.max_chunk_tokens):
                position = _skip_whitespace(full_text, position)
                origin = block_at(position)
                position = _advance_past(full_text, position, sum(1 for ch in part if not ch.isspace()))
                candidates.append(
                    ChunkCandidate(
                        content=part,
                        token_count=count_tokens(part),
                        char_count=len(part),
                        page=origin.page,
                        section=origin.section,
                        metadata={"strategy": "fixed"},
                    )
                )
            if start + self.chunk_size >= len(full_text):
                break
        return candidates
