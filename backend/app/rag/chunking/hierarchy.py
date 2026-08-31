"""Chunking for a REFERENCE-DEPENDENT document - one whose parts are written to
be incomplete, because the author refuses to repeat what an outer clause already
said. A statute, an examination standard, a company rulebook.

WHY THIS EXISTS. 상표심사기준.pdf p.89 carries 법 제36조제1항, and the database
holds it like this:

    chunk 396  제3장 상표등록출원서류
               제36조(상표등록출원) ① 상표등록을 받으려는 자는 다음 각 호의
               사항을 적은 상표등록출원서를 지식재산처장에게 제출하여야 한다.
    chunk 397  1. 출원인의 성명 및 주소(...)
    chunk 398  2. 출원인의 대리인이 있는 경우에는 (...)
    chunk 399  3. 상표
               4. 지정상품 및 산업통상자원부령으로 정하는 상품류(이하 "상품류"라 한다)
               5. 제46조제3항에 따른 사항(우선권을 주장하는 경우만 해당한다)
               6. 그 밖에 산업통상자원부령으로 정하는 사항

399 IS the answer to "상표등록출원서에 등록대상은 뭘로 기재해? 류와 지정상품
알려줘" and it contains neither 상표등록출원서 nor 제36조 nor a verb. There is no
sentence in it for a sentence-shaped question to be near: measured, sparse rank
1-2 and dense rank 91-282. The governing clause is chunk 396, THREE positions
away, so the shipped +/-1 neighbour expansion cannot reach it either.

This is the same shape as the owner's own example - "[1조1항] ... 3. 1항의 내용 중
~~~의 경우는 예외로 한다" - and the fix is the one that already worked on the
classification table: put the missing sentence INSIDE the chunk, derived
deterministically from the document's own structure, so the dense arm has
something to match.

WHAT IS CONFIGURATION AND WHAT IS CONTENT, which is the whole design.

  * The preset says what a level LOOKS LIKE (편/장/절/관/조/항/호/목, and how a
    citation is written). That is cultural knowledge, it is data, and it lives in
    PRESETS - a company rulebook numbered "Article 1 / Section 1.2 / (a)" writes
    its own and changes no code.
  * The DOCUMENT says whether it actually uses that structure. `detect()` counts
    what it finds and returns a verdict with the counts attached, and those counts
    are stored on the document row and rendered on screen, because this project
    already shipped one silent auto-decision (a hardcoded Korean-IP regex that
    chose the chunking strategy by sniffing) and migration 0013 exists to undo it.
    An inference nobody can see or correct is the worst case, not the best one.
  * A CITATION states its own depth. `제12조` is 조-deep, `제1조제1항` is 항-deep.
    There is no "reference depth" setting because the string already answered the
    question; that is parsing, not guessing.

NO LLM ANYWHERE IN THIS MODULE, deliberately. The backbone has to be
deterministic, free and verifiable. A semantic layer (요건/예외/판단절차) can sit
on top later; it is not this.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from anyio import to_thread

from app.core.tokens import count_tokens
from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.structure import NEWLINE_TOKENS, build_size_bounded_candidates

# The name a collection's `chunking.strategy` has to carry to get this, exactly as
# `classification_table` does. Named rather than inferred from the keys, so a typo
# is a configuration error somebody is told about and not a silent no-op.
STRATEGY = "hierarchical"

# Same bound and the same reason as table.py: these patterns are user-supplied and
# then run against every line of a thousand-page document.
MAX_PATTERN_CHARS = 500

# ponytail: no regex timeout. Python's `re` has none; a catastrophically
# backtracking user pattern stalls one worker job until PIPELINE_TIMEOUT kills it.
# The `regex` module's timeout= is the upgrade if that ever happens in practice.

PRESETS: dict[str, dict] = {
    # Korean legal/administrative drafting. MEASURED against this corpus, as the
    # share of chunks matching each shape:
    #
    #   document                       blocks    spine     cite   verdict
    #   상표심사기준                     5,750   0.0657   0.1593   high      -> reference-dependent
    #   특허·실용신안 심사기준           5,878   0.0168   0.3833   high      -> reference-dependent
    #   기술분야별 심사실무가이드        8,576   0.0012   0.0899   ambiguous -> self-contained
    #   지식재산권 권리회복 가이드라인     205   0.0000   0.1902   none      -> self-contained
    #   유사상품 심사기준               29,438   0.0010   0.0003   none      -> self-contained
    #   연구보고서 A                         8   0.0000   0.0000   none      -> self-contained
    #   농약 안전사용 지침                  13   0.0000   0.0000   none      -> self-contained
    #
    # The two guidelines are the interesting rows. 권리회복 CITES statute constantly
    # (19% of its blocks) and contains none of it - spine 0.0000 - so there is no
    # hierarchy of its own to hang anything on, and "none" is the true answer
    # rather than a miss. 기술분야별 is the ambiguous band by construction: ten 조
    # openers in 8,576 blocks is a handful of quoted provisions inside a technical
    # manual, not a statute.
    "korean_legal": {
        # Outermost to innermost. Each is anchored at the start of a BLOCK, which
        # is why `section_marker` (below) also reaches the parser: a level line
        # that got glued into the previous paragraph is a boundary the chunker
        # never sees. Measured on 유사상품 심사기준, 868 of 931 markers were
        # swallowed that way before the parser was given the pattern.
        # A TITLE LEVEL DOES NOT END IN A FULL STOP, which is the trailing
        # lookahead. MEASURED, off a wrong ancestor line in a live answer: the tail of a
        # wrapped cross-reference - "제10장 컴퓨터 관련 발명 참조)." - opened a chapter
        # and reset the whole stack in the middle of a page about 의료행위. A real
        # chapter heading is a noun phrase; the sentence that merely mentions one
        # ends like a sentence. The guard is only on 편/장/절/관 because a 조 opener
        # DOES end in "...한다." - it carries its own first clause.
        "levels": [
            ["편", r"^제\s*(?P<n>\d+)\s*편(?![0-9])(?!.*\.\s*$)"],
            ["장", r"^제\s*(?P<n>\d+)\s*장(?![0-9])(?!.*\.\s*$)"],
            ["절", r"^제\s*(?P<n>\d+)\s*절(?![0-9])(?!.*\.\s*$)"],
            ["관", r"^제\s*(?P<n>\d+)\s*관(?![0-9])(?!.*\.\s*$)"],
            # The two negative lookaheads are not decoration; each was MEASURED
            # off a wrong ancestor line in a live answer.
            #   (?!의)          - "제2조의 정의규정 위반으로 거절이유를 통지한다"
            #                     is a SENTENCE about 제2조, not the opening of it,
            #                     and it reset the whole stack mid-chapter. The
            #                     guard has to sit after `n_sub` so that 제5조의2 -
            #                     where 의 IS part of the number - still opens.
            #   (?!...항)       - "제46조제3항에 따른 사항은 ..." is a citation at
            #                     the head of a body line, same failure.
            # `n_sub` is the SUB-ARTICLE number and it cannot be part of `n`:
            # 제5조의2 writes it AFTER the marker, so no single group can span it.
            # It is appended verbatim, which keeps 제5조 and 제5조의2 two different
            # articles instead of one - see `_number`.
            ["조", r"^제\s*(?P<n>\d+)\s*조(?P<n_sub>의\d+)?(?!의)(?!\s*제?\s*\d+\s*항)(?![0-9])"],
            ["항", r"^(?P<n>[①-⑳])"],
            ["호", r"^(?P<n>\d+)\.[ \t]"],
            ["목", r"^(?P<n>[가나다라마바사아자차카타파하])\.[ \t]"],
        ],
        # WHICH LEVELS A CITATION CAN NAME. 편/장/절/관 are absent on purpose:
        # nothing in this corpus cites a chapter, and including them would make
        # every citation path start with a component no citation ever supplies.
        "addressable": ["조", "항", "호", "목"],
        # WHERE A CHUNK IS CUT. Levels at or above this one open a new chunk;
        # deeper ones only extend the path. Cutting at 호 instead produced four
        # ~10-character chunks out of "3. 상표 / 4. 지정상품 / 5. / 6." each
        # carrying a 150-character ancestor prefix - the prefix would then be the
        # chunk. Cutting at 항 keeps 제36조① and its six 호 in ONE chunk, which is
        # the unit a question about 상표등록출원서 actually wants.
        "break_level": "항",
        # Citation patterns. The CAPTURE GROUP NAMES are the contract: a group
        # named for an addressable level contributes that component of the target
        # path, `<level>_sub` extends that component verbatim (제5조의2), and `law`
        # marks a citation that points OUT of this document. That is the whole
        # generalisation - the code reads groups, the preset names levels - and it
        # is what makes "the string states its own depth" work without a depth
        # setting.
        "citations": [
            # 제36조 / 제36조제1항 / 제36조제1항제4호 / 제5조의2제2항
            r"제\s*(?P<조>\d+)\s*조(?P<조_sub>의\d+)?"
            r"(?:\s*제\s*(?P<항>\d+)\s*항)?(?:\s*제\s*(?P<호>\d+)\s*호)?",
            # A RELATIVE reference: "제1항 각 호의 사항 외에". No 조 of its own, so
            # it resolves against the 조 of the chunk that wrote it. This is the
            # owner's "[1조1항] ... 3. 1항의 내용 중" case exactly.
            r"제\s*(?P<항>\d+)\s*항(?:\s*제\s*(?P<호>\d+)\s*호)?",
            # 특허·실용신안 심사기준's bracket form: [특법3], [특법3(2)], [특칙5의2(2)].
            # 565 chunks carry one. `law` is captured, which means these are
            # recorded and NOT resolved - see resolve_citation.
            # ponytail: only the FIRST item of a comma list is taken, so
            # "[특법46, 16]" yields 특법46 and drops 16. Carry-over needs a scanner
            # rather than a pattern; these do not resolve in this corpus anyway.
            # The closing bracket is OPTIONAL, not absent: "[특법54(3)]" has to
            # keep it - the label is what the unresolved list shows the user - and
            # "[특법46, 16]" has to still match, because only the first item of a
            # comma list is taken.
            r"\[(?P<law>[가-힣]{2,12})\s*(?P<조>\d+(?:의\d+)?)" r"(?:\s*\(\s*(?P<항>\d+)\s*\))?\]?",
        ],
        # How the ancestor line reads. `{path}` is the joined ancestors.
        "ancestor_template": "{path}",
        "separator": " > ",
    },
}

# THE VERDICT BANDS, and the numbers come from the table above.
#
# Two ratios, and BOTH have to clear the bar. A document with a hierarchy and no
# citations is a numbered handbook and is self-contained - repeating its chapter
# title on every chunk buys nothing and costs tokens. A document with citations
# and no hierarchy has nothing in itself to resolve them against.
#
# The band between the two thresholds is AMBIGUOUS and falls to self_contained,
# which is the safe direction: a document wrongly left self-contained retrieves
# exactly as well as it does today, while one wrongly promoted gets a WRONG
# governing clause stamped on every one of its chunks. That asymmetry is the same
# one that put `break_before` in the classification preset - a wrong 류 code was
# worse than no code.
SPINE_LEVEL = "조"
HIGH_SPINE_RATIO = 0.010
LOW_SPINE_RATIO = 0.003
HIGH_CITATION_RATIO = 0.030
LOW_CITATION_RATIO = 0.012
# Below this many spine markers there is no hierarchy to speak of regardless of
# ratio - a four-block research report with one "제1조" in it is not a statute, and
# at that size one marker is a ratio of 0.25.
#
# 5, not 10, and the number was MEASURED rather than chosen: 지식재산권 권리회복
# 심사 가이드라인 is 143 blocks with 9 조 openers and a citation in 21.7% of them -
# 6.3% and 21.7%, both comfortably over the "high" bands - and a floor of 10 was
# the only thing calling it self-contained. A floor is meant to stop a ratio
# computed from one or two hits, not to disqualify a short document for being
# short.
MIN_SPINE_BLOCKS = 5

CHARACTERS = ("self_contained", "reference_dependent")

# How much of the ancestor chain is carried onto each chunk. The INNERMOST
# ancestor is the governing clause and is what the question has to land on, so it
# gets the whole budget it needs; the outer ones are labels ("제3장 상표등록출원서류")
# and are trimmed from the outside in when the total will not fit.
ANCESTOR_CHARS = 300
ANCESTOR_ENTRY_CHARS = 200
# A level opener short enough to BE its own title is carried whole ("제3장
# 상표등록출원서류"); a longer one is cut back to its number and parenthesised name
# ("제36조(상표등록출원)"), because the rest of that line is the clause body and it
# is already in the chunk.
LABEL_WHOLE_LINE_CHARS = 60
_TITLE = re.compile(r"[ \t]*\([^)\n]{0,60}\)")
_ELLIPSIS = "…"
_UNNAMED = re.compile(r"\(\?P<[^>]+>")
# Hangul syllables and jamo, the same range the PDF parser uses to decide that a
# line break fell inside a word.
HANGUL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")
HANGUL_TAIL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]$")
_SENTENCE_TAIL = ".!?。！？:;)]"
# The shortest line this corpus wraps at. See join_wrapped_levels.
WRAP_MIN_CHARS = 30


@dataclass(frozen=True)
class Level:
    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Scheme:
    """A document numbering system, compiled. Everything here came out of the
    collection's `chunking` JSON; nothing is hardcoded."""

    levels: tuple[Level, ...]
    addressable: tuple[str, ...]
    break_index: int
    citations: tuple[re.Pattern[str], ...]
    separator: str = " > "
    ancestor_template: str = "{path}"
    preset: str = ""

    def index(self, name: str) -> int:
        return next(i for i, level in enumerate(self.levels) if level.name == name)

    def opens(self, text: str) -> tuple[int, re.Match[str]] | None:
        """The level this block opens, outermost first, or None.

        Outermost-first rather than longest-match: the patterns are disjoint at
        position 0 by construction (제N조 and ① and "3. " cannot all match the same
        character), so the first hit is the only hit, and the order only decides
        which of two overlapping user patterns wins - which is the order the user
        wrote them in.
        """
        head = text.lstrip()
        for index, level in enumerate(self.levels):
            match = level.pattern.match(head)
            if match:
                return index, match
        return None


def _compile(pattern: str, what: str) -> re.Pattern[str]:
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"{what} pattern is longer than {MAX_PATTERN_CHARS} characters")
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"{what} is not a valid regular expression: {exc}") from exc


def resolve_scheme(config: dict | None) -> Scheme | None:
    """A collection's `chunking` JSON -> the compiled scheme, or None for "this
    collection is not chunked on a hierarchy".

    Raises ValueError on a configuration that cannot work, so the failure lands on
    whoever is saving the setting rather than on the next upload - the same
    contract `table.resolve` has.
    """
    if not config or config.get("strategy") != STRATEGY:
        return None
    preset = config.get("preset")
    if preset is not None and preset not in PRESETS:
        raise ValueError(f"unknown chunking preset: {preset!r}")
    merged = {**PRESETS.get(preset or "", {}), **config}

    raw_levels = merged.get("levels") or []
    if not raw_levels:
        raise ValueError(f"chunking strategy {STRATEGY!r} needs `levels` or a `preset`")
    levels: list[Level] = []
    for entry in raw_levels:
        try:
            name, pattern = entry
        except (TypeError, ValueError) as exc:
            raise ValueError(f"each level must be a [name, pattern] pair, got {entry!r}") from exc
        levels.append(Level(str(name), _compile(str(pattern), f"level {name!r}")))
    names = [level.name for level in levels]
    if len(set(names)) != len(names):
        raise ValueError(f"level names must be unique, got {names}")

    addressable = tuple(merged.get("addressable") or names)
    unknown = [name for name in addressable if name not in names]
    if unknown:
        raise ValueError(f"`addressable` names levels that do not exist: {unknown}")

    break_level = merged.get("break_level") or names[-1]
    if break_level not in names:
        raise ValueError(f"`break_level` {break_level!r} is not one of {names}")

    citations = tuple(_compile(str(pattern), "citation") for pattern in (merged.get("citations") or []))
    return Scheme(
        levels=tuple(levels),
        addressable=addressable,
        break_index=names.index(break_level),
        citations=citations,
        separator=str(merged.get("separator", " > ")),
        ancestor_template=str(merged.get("ancestor_template", "{path}")),
        preset=str(preset or ""),
    )


def section_marker(scheme: Scheme) -> re.Pattern[str]:
    """One pattern matching ANY level opener, for the PARSER.

    It has to reach the parser and not only the chunker: a level line that the
    layout pass glued onto the end of the previous paragraph is a boundary the
    chunker never gets to see. Same road `table.resolve().marker` travels.
    """
    # Capture groups are stripped to non-capturing. Every level names its number
    # `(?P<n>...)`, and a union of them is a `redefinition of group name` error -
    # which is a crash at collection-save time, not a subtle one, but it is a
    # crash in the one place a user would first try the feature.
    return re.compile(
        "|".join(f"(?:{_UNNAMED.sub('(?:', level.pattern.pattern)})" for level in scheme.levels)
    )


# ---------------------------------------------------------------------------
# The walk


@dataclass
class Run:
    """One cut of the document: the block that opened a level, the blocks under
    it, and the levels enclosing it."""

    ancestors: tuple[tuple[str, str], ...]  # (level name, opener text), outermost first
    opener: Block | None
    path: tuple[tuple[str, str], ...]  # (level name, number) for THIS run, addressable only
    body: list[Block] = field(default_factory=list)


def walk(blocks: list[Block], scheme: Scheme) -> list[Run]:
    """Blocks -> runs, in document order.

    A block that opens a level at or above `break_index` closes the current run
    and starts a new one. A DEEPER level (호 under 항) only extends the path: it is
    an item of the clause it sits in, not a document of its own, and cutting there
    produced chunks that were mostly ancestor prefix.

    A run with `opener is None` is text no level covers - front matter, a preamble
    - and is size-bounded with no prefix, exactly as the table strategy does.
    """
    blocks = join_wrapped_levels(blocks, scheme)
    stack: list[tuple[int, Block]] = []
    runs: list[Run] = [Run(ancestors=(), opener=None, path=())]

    def path_of(entries: list[tuple[int, Block]]) -> tuple[tuple[str, str], ...]:
        return tuple(
            (scheme.levels[i].name, _number(block, scheme, i))
            for i, block in entries
            if scheme.levels[i].name in scheme.addressable
        )

    for block in blocks:
        opened = scheme.opens(block.text)
        if opened is None:
            runs[-1].body.append(block)
            continue
        index, _ = opened
        # Truncate to the levels that ENCLOSE this one. A 조 closes every 항 and 호
        # under the previous 조; a 항 closes the 호 under the previous 항.
        stack = [entry for entry in stack if entry[0] < index]
        if index <= scheme.break_index:
            runs.append(
                Run(
                    ancestors=tuple((scheme.levels[i].name, b.text) for i, b in stack),
                    opener=block,
                    path=path_of([*stack, (index, block)]),
                )
            )
        else:
            runs[-1].body.append(block)
        stack.append((index, block))

    return [run for run in runs if run.opener is not None or run.body]


def _number(block: Block, scheme: Scheme, index: int) -> str:
    """The level number as the citation would write it. Circled digits are
    normalised so that ② and 제2항 name the same component, and `n_sub` is
    appended for a marker whose number is written in two parts (제5조의2), so that
    제5조 and 제5조의2 do not collapse onto one path."""
    match = scheme.levels[index].pattern.match(block.text.lstrip())
    groups = match.groupdict() if match else {}
    raw = ((groups.get("n") or "") + (groups.get("n_sub") or "")).strip()
    if len(raw) == 1 and "①" <= raw <= "⑳":
        return str(ord(raw) - 0x245F)
    return unicodedata.normalize("NFKC", raw)


def parse_key(key: str, scheme: "Scheme") -> tuple[tuple[str, str], ...]:
    """`"조36/항1"` -> `(("조", "36"), ("항", "1"))`. The inverse of `path_key`, so
    a path that has been round-tripped through `chunk_metadata` can still be
    compared against a freshly parsed citation."""
    out: list[tuple[str, str]] = []
    for part in key.split("/") if key else []:
        for name in scheme.addressable:
            if part.startswith(name):
                out.append((name, part[len(name) :]))
                break
    return tuple(out)


def path_key(path: tuple[tuple[str, str], ...]) -> str:
    """`(("조", "36"), ("항", "1"))` -> `"조36/항1"`. The key both a chunk's own
    position and a parsed citation are reduced to, so resolution is a dict
    lookup and not a comparison of shapes."""
    return "/".join(f"{name}{number}" for name, number in path)


# ---------------------------------------------------------------------------
# Citations


@dataclass(frozen=True)
class Citation:
    label: str  # exactly as written: "제46조제3항", "[특법54(3)]"
    path: tuple[tuple[str, str], ...]
    law: str  # "" for a reference inside this document
    relative: bool  # named no 조 of its own; resolves against the citing chunk's


def find_citations(text: str, scheme: Scheme) -> list[Citation]:
    """Every citation in `text`, deduplicated, in the order written.

    A citation's DEPTH is whatever groups it filled - that is the whole reason
    there is no depth setting. `제12조` fills the 조 group and stops; `제1조제1항` fills 조 and 항.
    """
    seen: set[tuple] = set()
    out: list[Citation] = []
    spans: list[tuple[int, int]] = []
    for pattern in scheme.citations:
        for match in pattern.finditer(text):
            # A later, looser pattern must not re-read the tail of a citation an
            # earlier one already claimed: without this, "제36조제1항" is read a
            # second time by the relative-reference pattern as a bare "제1항".
            if any(start <= match.start() < end for start, end in spans):
                continue
            groups = {k: v for k, v in match.groupdict().items() if v}
            # BY LEVEL NAME, in the scheme's own outermost-first order. A fixed
            # table of Korean names here would mean a preset that numbers its
            # levels "article/clause" could describe its citations and never have
            # one parsed - the generalisation would stop at the ancestor line.
            path = tuple(
                (
                    name,
                    unicodedata.normalize("NFKC", groups[name] + groups.get(f"{name}_sub", "")).strip(),
                )
                for name in scheme.addressable
                if groups.get(name)
            )
            if not path:
                continue
            law = groups.get("law", "")
            citation = Citation(
                label=match.group(0).strip(),
                path=path,
                law=law,
                relative=not law and path[0][0] != scheme.addressable[0],
            )
            key = (citation.path, citation.law, citation.relative)
            if key in seen:
                continue
            seen.add(key)
            spans.append(match.span())
            out.append(citation)
    return out


def resolve_citation(
    citation: Citation,
    source_path: tuple[tuple[str, str], ...],
    index: dict[str, int],
    scheme: Scheme,
) -> int | None:
    """Which chunk this citation points at, or None.

    THREE rules and no model.

    1. A citation naming a `law` points OUT of this document. It is recorded and
       left unresolved on purpose: `[민법950]` and `[헌법6]` name statutes this
       corpus does not hold, and resolving 특법/특칙 against whichever 제5조 happens
       to be quoted in the same PDF would be a guess wearing a citation's clothes.
       The unresolved count is what tells the user on screen that the target
       document has not been uploaded.
    2. A RELATIVE reference ("제1항") is completed with the citing chunk's own
       outer components before it is looked up.
    3. The target is the LONGEST PREFIX of the citation path that some chunk
       actually opens. A citation to 제36조제1항제4호 lands on the chunk that opens
       제36조제1항 when 호 does not open chunks of its own, which is exactly where
       그 호 is written.
    """
    if citation.law:
        return None
    path = citation.path
    if citation.relative:
        # Completed by LEVEL, not by length. "제1항" written inside 제46조 means
        # 제46조제1항, so the citing chunk's components COARSER than 항 are the ones
        # prepended - and a length-based slice gets that wrong the moment the
        # citing chunk is itself only 조-deep. Measured on the probe: 제46조's own
        # "제1항에 따라" resolved to nothing until this counted levels.
        rank = scheme.addressable.index(path[0][0])
        prefix = tuple(part for part in source_path if scheme.addressable.index(part[0]) < rank)
        # NOTHING TO COMPLETE IT WITH means the citation is unresolvable, not that
        # it is absolute. MEASURED against the live edges: a chunk sitting in
        # prose - no 조 of its own - wrote "제1항제1호", the fallback looked up a bare
        # 항1, and it landed on "①12을 적용하되..." from a different chapter
        # entirely. A wrong provision attached to an answer is worse than a
        # missing one, and the unresolved count is a number the user can read.
        if not prefix:
            return None
        path = prefix + path
    for cut in range(len(path), 0, -1):
        hit = index.get(path_key(path[:cut]))
        if hit is not None:
            return hit
    return None


# ---------------------------------------------------------------------------
# Detection


@dataclass
class Detection:
    character: str
    confidence: str  # high | ambiguous | none
    levels: dict[str, int]
    blocks: int
    spine_ratio: float
    citation_ratio: float

    def as_json(self) -> dict:
        return {
            "character": self.character,
            "detected": self.character,
            "confidence": self.confidence,
            "levels": self.levels,
            "blocks": self.blocks,
            "spine_ratio": round(self.spine_ratio, 5),
            "citation_ratio": round(self.citation_ratio, 5),
        }


def detect(blocks: list[Block], scheme: Scheme) -> Detection:
    """Does THIS DOCUMENT use the scheme its collection describes?

    Per document, never per collection: the `일반` collection of this deployment
    already holds 특허·실용신안 심사기준 (reference-dependent) beside 연구보고서 A and
    농약 안전사용 지침 (self-contained), so a collection-wide verdict is guaranteed
    wrong here.

    Returns counts as well as a verdict, because the verdict has to be RENDERED
    and CORRECTABLE. See the module docstring: the failure mode this project has
    already lived through is an automatic decision nobody could see.
    """
    # The SAME pre-pass the chunker runs, so the counts on screen describe the
    # document the chunker will actually cut.
    blocks = join_wrapped_levels(blocks, scheme)
    levels: dict[str, int] = {}
    spine = 0
    cited = 0
    spine_index = scheme.index(SPINE_LEVEL) if SPINE_LEVEL in [level.name for level in scheme.levels] else 0
    for block in blocks:
        opened = scheme.opens(block.text)
        body = block.text
        if opened is not None:
            index, match = opened
            name = scheme.levels[index].name
            levels[name] = levels.get(name, 0) + 1
            if index <= spine_index:
                spine += 1
            # A heading is not a citation OF ITSELF. "제36조(상표등록출원)" matches
            # the citation pattern perfectly, and counting it would make every
            # hierarchical document look maximally cross-referenced whether it
            # cites anything or not - the ratio would then measure the level
            # patterns rather than the document.
            body = block.text.lstrip()[match.end() :]
        if find_citations(body, scheme):
            cited += 1

    total = max(len(blocks), 1)
    spine_ratio = spine / total
    citation_ratio = cited / total

    if spine < MIN_SPINE_BLOCKS or (spine_ratio < LOW_SPINE_RATIO and citation_ratio < LOW_CITATION_RATIO):
        confidence = "none"
    elif spine_ratio >= HIGH_SPINE_RATIO and citation_ratio >= HIGH_CITATION_RATIO:
        confidence = "high"
    else:
        confidence = "ambiguous"
    character = "reference_dependent" if confidence == "high" else "self_contained"
    return Detection(
        character=character,
        confidence=confidence,
        levels=levels,
        blocks=len(blocks),
        spine_ratio=spine_ratio,
        citation_ratio=citation_ratio,
    )


# ---------------------------------------------------------------------------
# The strategy


def _label(text: str, scheme: Scheme, index: int) -> str:
    """How an ancestor reads when it is only a label.

    A short opener IS its own title and is carried whole ("제3장 상표등록출원서류").
    A long one is cut back to its marker plus a parenthesised name
    ("제36조(상표등록출원)") because the rest of the line is the clause body, and the
    clause body of the INNERMOST ancestor is carried separately in full.
    """
    text = " ".join(text.split())
    if len(text) <= LABEL_WHOLE_LINE_CHARS:
        return text
    match = scheme.levels[index].pattern.match(text)
    if match is None:
        return _truncate(text, LABEL_WHOLE_LINE_CHARS)
    title = _TITLE.match(text, match.end())
    return text[: title.end() if title else match.end()]


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + _ELLIPSIS


def ancestor_line(ancestors: tuple[tuple[str, str], ...], opener: Block | None, scheme: Scheme) -> str:
    """The one line prepended to every chunk of a run - what makes an enumeration
    item findable by a question about its governing clause.

    THE INNERMOST ENTRY IS CARRIED IN FULL and the outer ones as labels. That is
    the whole point: "제36조(상표등록출원) ① 상표등록을 받으려는 자는 다음 각 호의
    사항을 적은 상표등록출원서를 지식재산처장에게 제출하여야 한다." is the sentence a
    question about 상표등록출원서 has to land on, while "제3장 상표등록출원서류" is
    only positional. When the total will not fit, the outermost entries are dropped
    first, for the same reason.
    """
    parts: list[str] = []
    for position, (_, text) in enumerate(ancestors):
        index = scheme.index(ancestors[position][0])
        parts.append(_label(text, scheme, index))
    if opener is not None:
        parts.append(_truncate(" ".join(opener.text.split()), ANCESTOR_ENTRY_CHARS))
    while len(parts) > 1 and len(scheme.separator.join(parts)) > ANCESTOR_CHARS:
        parts.pop(0)
    line = scheme.separator.join(parts)
    return scheme.ancestor_template.format(path=line) if line else ""


# The two rules below both exist because the PARSER now receives the level
# patterns (it must - a level line glued into the previous paragraph is a boundary
# the chunker never sees) and therefore emits every one of them as a block of its
# own. That is right for the boundary and wrong for two things it also does.


def _flattened(blocks: list[Block]) -> list[Block]:
    """The run's body with `heading` block types demoted to `paragraph`.

    MEASURED, and this was a real regression: `build_size_bounded_candidates`
    opens a new candidate at every heading block, so once "1. 출원인의 성명" and its
    five siblings each became a heading, 제36조 shipped as FOUR chunks of one item
    each - and the whole point of `break_level: "항"` is that they are one. The
    document's own boundaries inside a run have already been decided by `walk`;
    a heading below the break level is an item of this clause, not a section.
    """
    return [
        Block(text=b.text, block_type="paragraph", page=b.page, section=b.section)
        if b.block_type == "heading"
        else b
        for b in blocks
    ]


def join_wrapped_levels(blocks: list[Block], scheme: Scheme) -> list[Block]:
    """A level opener the PDF wrapped mid-word, put back together.

    MEASURED: 상표심사기준 p.89 sets 제36조① across two physical lines, breaking
    inside a word - "...다음 각 호의 사항을 적은 상" / "표등록출원서를 지식재산처장에게
    제출하여야 한다." The parser used to rejoin those into one paragraph; now that
    the line matches a level pattern it is emitted as a heading and the join never
    happens, so the ancestor line - the sentence this whole feature exists to make
    findable - ended mid-word and the string "상표등록출원서" was not in it.

    ponytail: joined on the Hangul-on-both-sides rule alone, which the parser
    measures at 64.5% on its own (8,176 of 12,675 breaks are mid-word). The parser
    beats that because it can see the producer's trailing space GLYPH, and a Block
    no longer carries one. Three guards keep the cost of a wrong join near zero:

      * the opener must not already end a sentence;
      * the next block must not open a level of its own;
      * the opener must be LONG ENOUGH TO HAVE WRAPPED. This one is not
        cosmetic. "제1장 특허요건" is a title, twelve characters, that did not
        reach the margin - and without the length test it swallowed the first
        line of the chapter body and shipped "제1장 특허요건특허법 제29조는..."
        as its own ancestor label. A line of this corpus runs 40-50 Korean
        characters before it wraps; 30 is under every real wrap and over every
        title in it.

    What that still costs, MEASURED on the re-cut 상표심사기준: a break that fell on
    a REAL word boundary loses its space - "영업소의 소재지" ships as
    "영업소의소재지". About a third of the joins, by the parser's own count. The
    dense arm does not care; the character-bigram sparse arm loses one boundary
    bigram. The upgrade is to move this join into the PDF parser, which can see the
    producer's trailing space glyph and gets it right - and which is a change to a
    file the classification-table corpus also depends on, so it needs its own
    measurement rather than a ride on this one.
    """
    out: list[Block] = []
    skip = False
    for block, following in zip(blocks, [*blocks[1:], None], strict=True):
        if skip:
            skip = False
            continue
        head = block.text.rstrip()
        tail = following.text.lstrip() if following is not None else ""
        if (
            scheme.opens(block.text) is not None
            and len(head) >= WRAP_MIN_CHARS
            and head[-1] not in _SENTENCE_TAIL
            and HANGUL_TAIL.search(head)
            and tail
            and HANGUL.match(tail)
            and scheme.opens(tail) is None
        ):
            out.append(
                Block(
                    text=f"{head}{tail}",
                    block_type=block.block_type,
                    page=block.page,
                    section=block.section,
                )
            )
            skip = True
            continue
        out.append(block)
    return out


def section_label(run: Run, scheme: Scheme) -> str:
    """What a CITATION shows the user for a chunk of this run.

    Today, for the p.89 chunk, `section` reads "6. 그 밖에 산업통상자원부령으로 정하는
    사항" - a numbered item that the parser's heading heuristic mistook for the
    title of the NEXT chunk, and which names nothing. The position does name
    something: "제3장 상표등록출원서류 > 제36조(상표등록출원) > ②".

    Labels only, never the clause body: this goes on one line of a citation, and
    the body is already in the chunk.
    """
    # EVERY ancestor, not only the addressable ones. `addressable` says which
    # levels a CITATION can name; 제3장 names nothing a citation can point at and
    # is still half of where this chunk is.
    parts = [_label(text, scheme, scheme.index(name)) for name, text in run.ancestors]
    if run.opener is not None:
        opened = scheme.opens(run.opener.text)
        if opened is not None:
            parts.append(_label(run.opener.text, scheme, opened[0]))
    # String(500) on the column, and a citation line has to stay readable.
    return _truncate(scheme.separator.join(parts), 200)


class HierarchicalChunking(ChunkingStrategy):
    """Cut on the document's own numbering, and put the governing clause inside
    every piece.

    NO SEMANTIC MERGE PASS, for the reason the table strategy gives: a merge over
    adjacent clauses of one statute glues 제36조 back onto 제37조 and undoes the
    boundary this exists to draw.
    """

    def __init__(
        self,
        scheme: Scheme,
        prose: ChunkingStrategy | None = None,
        max_chunk_tokens: int = 1300,
        target_chars: int = 1000,
        overlap_chars: int = 150,
    ):
        self.scheme = scheme
        self.prose = prose
        self.max_chunk_tokens = max_chunk_tokens
        self.target_chars = target_chars
        self.overlap_chars = overlap_chars

    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]:
        """The hierarchy where the document has one, `prose` where it does not.

        THIS IS NOT A REFINEMENT, it is what keeps the change small enough to
        measure. MEASURED on the live corpus: 특허·실용신안 심사기준 has 99 조 openers
        in 5,878 blocks. The overwhelming majority of a reference-dependent
        document is ordinary examination prose BETWEEN its quoted provisions, and
        cutting that with a size pass instead of the deployment's configured
        strategy would change every chunk boundary the 52-question fixture was
        measured on - a retrieval regression dressed up as a structure feature.

        So a run that no level opened is handed to `prose` (the collection's
        deployment-wide strategy, StructureSemanticChunking today) exactly as it
        is today, and only the runs a level DID open are prefixed and cut here.
        `prose=None` falls back to the size pass, which is what a test that wants
        the hierarchy in isolation asks for.
        """
        candidates: list[ChunkCandidate] = []
        for run in await to_thread.run_sync(walk, blocks, self.scheme):
            if run.opener is None:
                if not run.body:
                    continue
                if self.prose is not None:
                    candidates.extend(await self.prose.chunk(run.body, embed_fn))
                else:
                    candidates.extend(
                        await to_thread.run_sync(
                            build_size_bounded_candidates,
                            run.body,
                            self.max_chunk_tokens,
                            self.target_chars,
                            self.overlap_chars,
                        )
                    )
                continue
            # tiktoken is CPU-bound and arq runs every queued job on one event loop.
            candidates.extend(await to_thread.run_sync(self._run, run))
        return candidates

    def _run(self, run: Run) -> list[ChunkCandidate]:
        assert run.opener is not None
        prefix = ancestor_line(run.ancestors, run.opener, self.scheme)
        # What the prefix costs, taken out of BOTH budgets before the body is cut,
        # so prefix + piece still honours the limits rather than overshooting them
        # by the prefix on every chunk. max(1, ...) is for a prefix pathologically
        # longer than the whole limit.
        prefix_tokens = count_tokens(prefix) + NEWLINE_TOKENS
        pieces = build_size_bounded_candidates(
            _flattened(run.body),
            max(1, self.max_chunk_tokens - prefix_tokens),
            max(1, self.target_chars - len(prefix) - 1),
            # No overlap: the ancestor line IS this document's continuity carrier,
            # and a character tail would repeat the previous clause's body on top
            # of a prefix that already says where we are. Same trade the table
            # strategy makes.
            0,
        )
        if not pieces:
            pieces = [ChunkCandidate(content="", token_count=0, char_count=0, page=run.opener.page)]
        section = section_label(run, self.scheme)
        key = path_key(run.path)
        for piece in pieces:
            piece.content = f"{prefix}\n{piece.content}" if piece.content else prefix
            piece.token_count += prefix_tokens
            piece.char_count = len(piece.content)
            # The hierarchy path, not the sub-heading the piece happens to start
            # under. `section` is what a citation shows the user, and today it
            # shows "6. 그 밖에 산업통상자원부령으로 정하는 사항" - a numbered item
            # that the heading heuristic mistook for a title of the NEXT chunk.
            if section:
                piece.section = section
            if piece.page is None:
                piece.page = run.opener.page
            piece.metadata["strategy"] = STRATEGY
            if key:
                piece.metadata["path"] = key
        return pieces
