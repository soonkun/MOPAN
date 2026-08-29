import re

from app.core.tokens import count_tokens, decode_tokens, encode_tokens
from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate

# Latin and CJK terminators. Splitting on a lookbehind keeps the punctuation
# attached to the sentence it belongs to.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+")

# Cost of the newline that joins two pieces inside one candidate - here and in
# the semantic merge pass, which joins whole candidates the same way. Counting it
# is what keeps the running total an upper bound rather than an under-estimate.
#
# This is a measured bound, not a proof. cl100k's pre-tokeniser rule
# ` ?[^\s\p{L}\p{N}]+[\r\n]*` lets a trailing punctuation run absorb the newline,
# so count_tokens(a + "\n" + b) can exceed count_tokens(a) + 1 + count_tokens(b)
# by 1 per join. Measured: 3 of 65,640 realistic punctuation tails trigger it
# (";]/", "_#{", '"=>'), and 600 punctuation-heavy random documents produced zero
# violations - but a synthetic document alternating "x;]/" and Korean compounds it
# to a 571-token candidate under a 500-token limit. Harmless against the 8191
# embedding ceiling at the default; the max_chunk_tokens validator in Settings is
# what keeps the configured limit far enough below it for that to stay true.
NEWLINE_TOKENS = count_tokens("\n")

_REPLACEMENT = "�"


def split_sentences(text: str) -> list[str]:
    return [piece.strip() for piece in _SENTENCE_BOUNDARY.split(text) if piece.strip()]


def _hard_split(text: str, max_tokens: int) -> list[str]:
    """Last resort for a single sentence that alone exceeds the limit.

    Slicing the token stream on a fixed stride is not safe. cl100k tokenises
    Korean, emoji and other multi-byte characters into fragments *below* one
    character, so a stride boundary can land mid-character and decode to U+FFFD
    on both sides. Measured, that corrupts Korean at 58 of 64 max_tokens values
    and mixed script at 61 of 64 - silent data loss in the language this system
    targets. Back the boundary off until the piece decodes cleanly, which fixes
    both sides of the cut at once.
    """
    token_ids = encode_tokens(text)
    pieces: list[str] = []
    start = 0
    while start < len(token_ids):
        end = min(start + max_tokens, len(token_ids))
        piece = decode_tokens(token_ids[start:end])
        # Stopping at one token guarantees progress for a character wider than
        # the whole limit (a 4-token emoji under a 2-token limit), which no split
        # can render intact anyway.
        while end > start + 1 and piece.endswith(_REPLACEMENT):
            end -= 1
            piece = decode_tokens(token_ids[start:end])
        pieces.append(piece)
        start = end
    return pieces


def split_to_token_limit(text: str, max_tokens: int) -> list[str]:
    """Split on sentence boundaries until every piece fits under max_tokens."""
    if max_tokens < 1:
        # max_chunk_tokens is an operator-facing setting, so 0 is reachable from
        # configuration. Fail with a named cause rather than deep inside a slice.
        raise ValueError("max_tokens must be at least 1")
    if count_tokens(text) <= max_tokens:
        return [text]

    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in split_sentences(text):
        if current:
            # Count the joining space as part of the sentence that follows it.
            # cl100k attaches a leading space to the next word (" world" is one
            # token), so summing standalone sentence counts UNDER-estimates the
            # joined string - measured at up to 2 tokens per join, which is
            # exactly how an "impossible" over-limit piece escapes. BPE merges
            # never cross a pre-token boundary and the space opens one, so
            # count_tokens(" " + sentence) is the exact incremental cost.
            cost = count_tokens(f" {sentence}")
            if current_tokens + cost <= max_tokens:
                current.append(sentence)
                current_tokens += cost
                continue
            pieces.append(" ".join(current))
            current, current_tokens = [], 0

        standalone = count_tokens(sentence)
        if standalone > max_tokens:
            pieces.extend(_hard_split(sentence, max_tokens))
            continue
        current, current_tokens = [sentence], standalone

    if current:
        pieces.append(" ".join(current))
    # Whitespace-only text survives the size check but leaves no sentences to
    # rejoin, so the fallback has to be size-bounded too - returning `text` here
    # would hand back the very piece the caller asked us to break up.
    return pieces or _hard_split(text, max_tokens)


def build_size_bounded_candidates(blocks: list[Block], max_chunk_tokens: int) -> list[ChunkCandidate]:
    """Pass 1 of chunking. Opens a new candidate when a heading arrives OR when
    adding this piece would exceed max_chunk_tokens, and splits any single block
    that is too big on its own. The one exception is a candidate that so far holds
    nothing but headings: it absorbs forward instead of breaking, so a title
    followed straight by a section heading does not ship as a chunk of its own.

    Token counts accumulate incrementally, separator included. Re-encoding the
    whole accumulated string on every block append is O(n^2) tiktoken work over a
    document; omitting the separator instead makes the total an under-count, and
    an under-count is how a chunk gets past the limit it is supposed to enforce.
    See NEWLINE_TOKENS for the residual case where the separator costs 2, not 1.
    """
    candidates: list[ChunkCandidate] = []
    current: ChunkCandidate | None = None
    pending_break = False
    # True while `current` holds nothing but heading text. Such a candidate is not
    # a chunk, it is the title of the next one - see the absorb branch below.
    heading_only = False

    for block in blocks:
        # An empty or whitespace-only block would otherwise emit a zero-length
        # candidate, which costs an embedding call and retrieves nothing.
        pieces = [p for p in split_to_token_limit(block.text, max_chunk_tokens) if p.strip()]
        if not pieces:
            # ...but a heading with no text is still a section boundary, and
            # text_parser emits one for a bare "#" line. Dropping it outright
            # appended the next section's body to the previous candidate and
            # cited it under the previous section. Only its text is empty.
            pending_break = pending_break or block.block_type == "heading"
            continue

        for piece in pieces:
            piece_tokens = count_tokens(piece)
            over_limit = (
                current is not None
                and current.token_count + NEWLINE_TOKENS + piece_tokens > max_chunk_tokens
            )
            # A candidate holding nothing but headings is orphaned text, not a
            # chunk: `# Title` followed straight by `## Section` emitted the title
            # on its own, measured at 12 tokens / 10 characters on the markdown
            # document in the dev corpus. Every heading-then-heading file hits it.
            # So a section break is only honoured once the candidate has a body;
            # until then the next piece is absorbed. The size bound still wins,
            # which is what keeps a heading stack from growing past the limit.
            starts_new = (
                current is None
                or over_limit
                or (not heading_only and (pending_break or block.block_type == "heading"))
            )
            pending_break = False
            if starts_new:
                current = ChunkCandidate(
                    content=piece,
                    token_count=piece_tokens,
                    char_count=len(piece),
                    page=block.page,
                    section=block.section,
                )
                candidates.append(current)
                heading_only = block.block_type == "heading"
            else:
                current.content = f"{current.content}\n{piece}"
                current.token_count += NEWLINE_TOKENS + piece_tokens
                current.char_count = len(current.content)
                if heading_only:
                    # Absorbing forward past a heading-only candidate: the citation
                    # belongs to the section the body sits under, not to the title
                    # the candidate happened to open with.
                    if block.section is not None:
                        current.section = block.section
                    if block.page is not None:
                        current.page = block.page
                    heading_only = block.block_type == "heading"
                else:
                    if current.page is None:
                        current.page = block.page
                    if current.section is None:
                        current.section = block.section

    return candidates
