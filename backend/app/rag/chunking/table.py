"""Chunking for a CLASSIFICATION TABLE - a document built out of code-headed
sections rather than of prose. A goods-classification manual, a parts catalogue,
an ICD-10 table, a chart of accounts: anything whose retrievable unit is "the
marker line plus everything under it until the next marker line".

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

THE HEAD LINE, and why a repeated marker was not enough. The marker gives the
SPARSE arm everything it needs - "제9류" and "G390802" are literal strings a query
can share bigrams with - and the sparse arm duly returns the software section at
rank 1 for a rewritten 류 question. The DENSE arm could not place any of these
chunks inside depth 300 no matter how the question was phrased, because a bracket
of codes followed by 400 bare product names is not a sentence and there is nothing
in it for a sentence-shaped question to be near. Measured, before this line
existed: best dense rank 1204 of 16,841 for "어플 이름을 상표출원하려는데 몇 류로
출원해야 하나요?", and 0 of the 10 queries a real retry issues put one inside the
dense arm's top 10. So each chunk opens with ONE SENTENCE INTERPOLATED FROM THE
MARKER'S OWN CAPTURED GROUPS - deterministic, no model, no per-section prose -
and that sentence is what the dense arm matches. Measured after: 5 of those same
10. The wording of the shipped preset's sentence is not decoration; see the
measurements beside it.

WHAT THE SENTENCE DOES NOT FIX. A question phrased entirely in words the corpus
never prints - 어플 - still cannot reach these chunks by the dense arm on its own
(1204 -> 875, not 10). The bridge from 어플 to 애플리케이션 소프트웨어 is the query
expander's substitution rule, and this line is what lets the rewrite it produces
LAND. The two are one mechanism; neither works without the other.

WHAT SELECTS IT, and what does not. The COLLECTION's `chunking` configuration,
which is the user's explicit choice, and nothing else. It used to select itself by
sniffing the blocks for >= 20 lines matching a hardcoded Korean-IP regex, which is
domain knowledge wearing a heuristic's clothes: it did nothing for anybody else's
documents and nobody could turn it on for theirs. The pattern that recognises a
marker, the sentence composed from it and the line that closes a section are all
CONFIGURATION now, with `PRESETS["korean_ip_classification"]` reproducing exactly
what used to be hardcoded.

NO SEMANTIC MERGE PASS, deliberately. A merge pass over a goods table glues
section 390801's robots back onto section 390802's software header and undoes the
boundary this module exists to draw - in a table every adjacent pair is similar.
Runs of blocks that no marker covers (front matter, a class preamble) are
size-bounded rather than handed to a prose strategy for the same reason: there is
no prose here for one to protect.
"""

import re
import string
from dataclasses import dataclass

from anyio import to_thread

from app.core.tokens import count_tokens
from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.structure import NEWLINE_TOKENS, build_size_bounded_candidates

# The name a collection's `chunking.strategy` has to carry to get this. A named
# choice rather than "whatever the marker key implies", so an unknown value is a
# configuration error the user is told about instead of a silent no-op.
STRATEGY = "classification_table"

# A user-supplied regex is compiled and then run against every line of a
# thousand-page document, so its cost is the user's to pay and its length is
# bounded here rather than left open.
#
# ponytail: length is the only bound. Python's `re` has no match timeout, so a
# catastrophically backtracking pattern - (a+)+$ and friends - stalls one worker
# job until PIPELINE_TIMEOUT kills it. The document fails, nothing else is
# affected, and the fix if that ever happens in practice is the `regex` module's
# `timeout=` rather than a validator that tries to prove termination.
MAX_PATTERN_CHARS = 500

# Shipped patterns, so that the common case is a name and not a regex. A preset
# supplies the defaults and any key given alongside it wins, which is what lets
# somebody take the Korean-IP marker and change only the sentence.
#
# Group names are the contract between the two halves: whatever `marker` captures
# by name is what `head_line` may interpolate. That is the whole generalisation -
# "compose a sentence from the marker's own captured groups" is domain-free, while
# 류 and 유사군코드 live in this dict rather than in the code.
PRESETS: dict[str, dict[str, str]] = {
    # 유사상품 심사기준: "[제9류/G390802] 소프트웨어", "[제35류/S120602] 광고업",
    # "[제9류/G3902, G3903]". 931 such lines in that document, 0 in every other
    # document of this corpus.
    "korean_ip_classification": {
        "marker": (
            r"^\[제\s*(?P<class_no>\d+)\s*류\s*/\s*"
            r"(?P<code>[A-Z]\d{3,}(?:\s*,\s*[A-Z]\d{3,})*)\s*\]\s*(?P<name>.*)$"
        ),
        # MEASURED, against the rewrites the shipped query expander really
        # produces for the owner's question, as the estimated dense rank of the
        # best [제9류/G390802] chunk over the whole 16,841-chunk corpus. What has
        # to be beaten is CANDIDATE_LIMIT: a chunk outside the dense arm's top 10
        # cannot be corroborated by it, and an uncorroborated best candidate is
        # what diverts the answer to the clarification prompt.
        #
        #   sentence                                   in dense top-10
        #   (no head line, the marker alone)                0 of 10
        #   "제N류에 속하는 지정상품 또는 지정서비스업        1 of 10
        #    목록입니다. 유사군코드는 C이고, ..."
        #   "{name}. {name}은(는) 제N류에 속하며 ..."         (worse than none)
        #   "제N류 {name} (C)"                              (worse than none)
        #   this one                                        5 of 10
        #
        # The two that lost are the two that describe the LIST. A question is
        # about filing a trademark, so the sentence has to be about filing a
        # trademark; a sentence about what a table contains moves the chunk
        # toward every other table in the corpus instead. Naming 상표 출원 is not
        # a fact added to the marker, it is what this whole document is for -
        # and it is configuration, so a collection of ICD-10 codes writes its own.
        #
        # 상품류 covers all 45 니스 classes, services included (제35류~제45류), which
        # is why one sentence serves both and a goods/services branch is not
        # needed. A variant that spelled out "상품 및 서비스업" measured WORSE
        # (3 of 10) for the length it cost.
        "head_line": "상표 출원 시 {name}의 상품류는 제{class_no}류, 유사군코드는 {code}입니다.",
        "break_before": "본류에는",
    },
}


@dataclass(frozen=True)
class SectionMarkers:
    """How to recognise a section header, how to describe one in a sentence, and
    what closes a section without opening one."""

    marker: re.Pattern[str]
    head_line: str = ""
    break_before: str = ""


class _Blank(dict):
    """A template naming a group the pattern does not capture renders blank
    rather than raising in the middle of a worker job."""

    def __missing__(self, key: str) -> str:
        return ""


def resolve(config: dict | None) -> SectionMarkers | None:
    """A collection's `chunking` JSON -> the compiled markers, or None for "this
    collection is not chunked on section markers".

    Raises ValueError on a configuration that cannot work, so that the failure
    lands on whoever is saving the setting rather than on the next upload.
    """
    if not config or config.get("strategy") != STRATEGY:
        if config and config.get("strategy") not in (None, STRATEGY):
            raise ValueError(f"unknown chunking strategy: {config['strategy']!r}")
        return None
    preset = config.get("preset")
    if preset is not None and preset not in PRESETS:
        raise ValueError(f"unknown chunking preset: {preset!r}")
    merged = {**PRESETS.get(preset or "", {}), **config}
    pattern = merged.get("marker")
    if not pattern:
        raise ValueError(f"chunking strategy {STRATEGY!r} needs a `marker` pattern or a `preset`")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"marker pattern is longer than {MAX_PATTERN_CHARS} characters")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"marker is not a valid regular expression: {exc}") from exc
    return SectionMarkers(
        marker=compiled,
        head_line=merged.get("head_line", "") or "",
        break_before=merged.get("break_before", "") or "",
    )


class ClassificationTableChunking(ChunkingStrategy):
    def __init__(
        self,
        markers: SectionMarkers,
        max_chunk_tokens: int = 1300,
        target_chars: int = 1000,
        overlap_chars: int = 150,
    ):
        self.markers = markers
        # Which capture groups the sentence names, worked out once rather than
        # per section.
        self._head_fields = {
            field for _, field, _, _ in string.Formatter().parse(markers.head_line) if field
        }
        self.max_chunk_tokens = max_chunk_tokens
        self.target_chars = target_chars
        self.overlap_chars = overlap_chars

    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]:
        # Same reason the other strategies thread their passes: tiktoken is
        # CPU-bound and arq runs every queued job on one event loop.
        return await to_thread.run_sync(self._chunk, blocks)

    def _match(self, block: Block) -> re.Match[str] | None:
        """The section header this block opens, or None if it opens none."""
        return self.markers.marker.match(block.text.strip())

    def _head(self, match: re.Match[str]) -> str:
        """One sentence about this section, composed from the marker's own
        captured groups.

        Skipped entirely when a group the template names captured nothing: a
        header with no goods name would otherwise ship "상품군 명칭은 입니다."
        stamped on every chunk of its section, and half a sentence is worse than
        none - the point of the line is that a question can land on it.
        """
        if not self.markers.head_line:
            return ""
        groups = {key: (value or "").strip() for key, value in match.groupdict().items()}
        if any(not groups.get(field, "") for field in self._head_fields):
            return ""
        return self.markers.head_line.format_map(_Blank(groups)).strip()

    def _sections(self, blocks: list[Block]) -> list[tuple[Block | None, list[Block]]]:
        """(header, body) per section, in document order.

        A None header means "no marker applies here": the front matter before the
        first one, and - where the configuration names a `break_before` - each run
        that closes a section without opening one. Those are size-bounded with no
        prefix. An empty section is dropped rather than emitted.

        WHY `break_before` EXISTS, in the shipped preset's terms: each 니스 class
        opens with a preamble - its scope, its 특히 포함되는 상품 and its 특히
        포함되지 않는 상품 - carrying no code of its own, so without this it is
        swallowed by the LAST section of the PREVIOUS class and ships stamped
        "[제1류/G5301] 특수세라믹제조용 합성물" over a list of paints. A wrong code
        on a chunk is worse here than no code at all: the whole point of the code
        is that an answer can be grounded in it. "본류에는" opens 50 blocks in that
        document and nothing else does.

        ponytail: the preamble's TITLE lines run ahead of "본류에는" and still land
        in the previous section - ~4 blocks x 45 classes. Fixing that needs the
        class number, which appears on those pages only in the running header the
        furniture rule strips.
        """
        sections: list[tuple[Block | None, list[Block]]] = [(None, [])]
        for block in blocks:
            if self._match(block):
                sections.append((block, []))
            elif self.markers.break_before and block.text.startswith(self.markers.break_before):
                sections.append((None, [block]))
            else:
                sections[-1][1].append(block)
        return [s for s in sections if s[0] is not None or s[1]]

    def _chunk(self, blocks: list[Block]) -> list[ChunkCandidate]:
        candidates: list[ChunkCandidate] = []
        for header, body in self._sections(blocks):
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
        marker = header.text.strip()
        match = self.markers.marker.match(marker)
        assert match is not None  # _sections only routes matching blocks here
        head = self._head(match)
        prefix = f"{head}\n{marker}" if head else marker
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
            # The marker, not the head line and not the sub-heading the piece
            # happens to start under ("1. 시스템 소프트웨어(예시)"), which is what a
            # citation would otherwise show and which names no class.
            piece.section = marker
            if piece.page is None:
                piece.page = header.page
            piece.metadata["strategy"] = STRATEGY
        return pieces
