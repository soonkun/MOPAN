"""Chunking for a CLASSIFICATION TABLE - a document built out of code-headed
sections rather than of prose.

WHY THIS EXISTS. 유사상품 심사기준 is 1,011 pages of two-column goods tables cut
into ~930 sections, each opened by "[제9류/G390802] 소프트웨어" and followed by
that section's 상품의 범위, its 타류·타유사군 exclusions and its 포함되는 상품
list. Chunked as prose it produced 9,510 chunks that answered nothing: the
question "어플 이름을 출원하려는데 류와 지정상품은?" never pulled a single one of
them into the fused top 300 in EITHER arm, measured. Two reasons, both structural:

  1. The header travelled alone. `[제9류/G390802] 소프트웨어` and its goods list
     landed in different chunks - the header at the tail of a chunk that was
     mostly the PREVIOUS section's robots - so neither piece could be found by a
     question that names a product.
  2. The goods list never named its class. Chunks 2 through 6 of a section are
     word runs of goods names with no 류 and no 유사군코드 anywhere in them, so
     even a chunk that IS the answer cannot be recognised as one.

So a retrievable unit here is a SECTION: its marker plus its goods, and the
marker repeated on every piece the size bound forces off the end. That repetition
is what `chunk_overlap` does for prose - carry enough of the previous chunk that
the next one still makes sense - and it replaces it here, because in a table the
previous 150 characters are somebody else's goods and the marker is the context
that is actually missing.

WHAT SELECTS IT. The blocks, not the filename: a document is a classification
table when at least MIN_SECTIONS of its blocks OPEN with a class/similarity-group
marker. Measured over the whole corpus, that is 931 blocks in 유사상품 심사기준
and 0 in each of the other six documents, so no prose document can trip it and no
document has to be named anywhere. Anything else is handed straight to the
admin-selected strategy, which is why this wraps that strategy rather than
replacing it in `get_chunking_strategy`.

NO SEMANTIC MERGE PASS, deliberately. The wrapped strategy's second pass merges
adjacent candidates whose embeddings are similar - and in a goods table every
adjacent pair is similar, so it would glue section 390801's robots back onto
section 390802's software header and undo the boundary this module exists to
draw. There is no prose here for it to protect.
"""

from anyio import to_thread

from app.core.tokens import count_tokens
from app.rag.blocks import CLASS_GROUP_MARKER, Block
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.structure import NEWLINE_TOKENS, build_size_bounded_candidates

# 931 in the one document that is one, 0 in the other six. Two orders of
# magnitude of headroom either way, so the exact value is not load-bearing; what
# it has to exclude is a prose document that QUOTES a few of these codes.
MIN_SECTIONS = 20

# What CLOSES a section, as opposed to opening one. Each 니스 class opens with a
# preamble - its scope, its 특히 포함되는 상품 and its 특히 포함되지 않는 상품 -
# and that preamble carries no code of its own, so without this it would be
# swallowed by the LAST section of the PREVIOUS class and ship stamped
# "[제1류/G5301] 특수세라믹제조용 합성물" over a list of paints. A wrong class code
# on a chunk is worse here than no code at all: the whole point of the code is
# that an answer can be grounded in it. "본류에는" ("this class contains") opens
# 50 blocks in the document and nothing else does.
#
# ponytail: the preamble's TITLE lines run ahead of "본류에는" and still land in
# the previous section - ~4 blocks x 45 classes. Fixing that needs the class
# number, which appears on these pages only in the running header the furniture
# rule strips.
CLASS_PREAMBLE = "본류에는"


def _marker(block: Block) -> str:
    """The section header this block opens, or "" if it opens none."""
    text = block.text.strip()
    return text if CLASS_GROUP_MARKER.match(text) else ""


def is_classification_table(blocks: list[Block]) -> bool:
    return sum(1 for block in blocks if _marker(block)) >= MIN_SECTIONS


def _sections(blocks: list[Block]) -> list[tuple[Block | None, list[Block]]]:
    """(header, body) per section, in document order.

    A None header means "no code applies here": the front matter before the first
    marker, and each class preamble. Those are ordinary prose and are chunked as
    such, with no prefix. An empty section is dropped rather than emitted.
    """
    sections: list[tuple[Block | None, list[Block]]] = [(None, [])]
    for block in blocks:
        if _marker(block):
            sections.append((block, []))
        elif block.text.startswith(CLASS_PREAMBLE):
            sections.append((None, [block]))
        else:
            sections[-1][1].append(block)
    return [s for s in sections if s[0] is not None or s[1]]


class ClassificationTableChunking(ChunkingStrategy):
    def __init__(
        self,
        fallback: ChunkingStrategy,
        max_chunk_tokens: int = 1300,
        target_chars: int = 1000,
        overlap_chars: int = 150,
    ):
        self.fallback = fallback
        self.max_chunk_tokens = max_chunk_tokens
        self.target_chars = target_chars
        self.overlap_chars = overlap_chars

    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]:
        if not is_classification_table(blocks):
            return await self.fallback.chunk(blocks, embed_fn)
        # Same reason the other strategies thread their passes: tiktoken is
        # CPU-bound and arq runs every queued job on one event loop.
        return await to_thread.run_sync(self._chunk, blocks)

    def _chunk(self, blocks: list[Block]) -> list[ChunkCandidate]:
        candidates: list[ChunkCandidate] = []
        for header, body in _sections(blocks):
            if header is None:
                candidates.extend(
                    build_size_bounded_candidates(
                        body, self.max_chunk_tokens, self.target_chars, self.overlap_chars
                    )
                )
                continue
            candidates.extend(self._section(header, body))
        return candidates

    def _section(self, header: Block, body: list[Block]) -> list[ChunkCandidate]:
        prefix = header.text.strip()
        # What the prefix costs, taken out of both budgets BEFORE the body is cut,
        # so that prefix + piece still honours the limits rather than overshooting
        # them by the header on every chunk. The `max(1, ...)` is for a header
        # pathologically longer than the whole limit: one oversized chunk beats
        # dividing by a negative budget.
        prefix_tokens = count_tokens(prefix) + NEWLINE_TOKENS
        pieces = build_size_bounded_candidates(
            body,
            max(1, self.max_chunk_tokens - prefix_tokens),
            max(1, self.target_chars - len(prefix) - 1),
            # No overlap: the marker below IS this document's continuity carrier,
            # and a character tail would additionally repeat somebody else's goods.
            0,
        )
        # A marker with nothing under it - the last line of the document, or a
        # header whose body the parser dropped - is still a section. Shipping it
        # alone beats losing the code entirely.
        if not pieces:
            pieces = [ChunkCandidate(content="", token_count=0, char_count=0, page=header.page)]
        for piece in pieces:
            piece.content = f"{prefix}\n{piece.content}" if piece.content else prefix
            piece.token_count += prefix_tokens
            piece.char_count = len(piece.content)
            # The marker, not the sub-heading the piece happens to start under
            # ("1. 시스템 소프트웨어(예시)"), which is what a citation would
            # otherwise show and which names no class.
            piece.section = prefix
            if piece.page is None:
                piece.page = header.page
            piece.metadata["strategy"] = "classification_table"
        return pieces
