import bisect
import re
from collections import Counter, defaultdict, deque
from itertools import zip_longest
from typing import NamedTuple

import pdfplumber

from app.rag.blocks import Block, ParsedDocument
from app.rag.parsers.base import Parser

MAX_HEADING_CHARS = 80
MAX_HEADING_WORDS = 12
# A bare leading number is not enough: "2025 was a strong year" and "15 growers
# reported blight" are ordinary prose. Require either a separator ("1.", "4)")
# or a multi-level number ("3.2"). One false-positive class survives that rule -
# a decimal quantity opening a sentence: "0.5 mg per litre was applied", "3.2
# million units were sold", "1.2 billion won in revenue", "99.9 percent uptime
# was achieved" and "2.5 times more than last year" all still match. It is an
# inherited class, not one this rule introduced: a brute force over 400k strings
# confirmed the pattern accepts a strict subset of the bare-number one it
# replaced. Killing it needs a lookahead for a unit word, which is a bigger
# heuristic than the one it would protect.
NUMBERED_HEADING = re.compile(
    r"^\d+(?:\.\d+)*[.)]\s+\S"  # 1. Introduction / 4) Methods / 3.2. Results
    r"|^\d+(?:\.\d+)+\s+\S"  # 3.2 Results - multi-level needs no separator
)
SENTENCE_ENDINGS = ".!?,;:"

# Visual reconstruction. MEASURED on the 854-page Korean government PDF this
# parser was rewritten for: pypdf's extract_text returns CONTENT-STREAM order,
# which draws the Hangul run first and the numerals afterwards at absolute
# positions, so page 486 came back as "청구기간은 누구나 회에 한하여 일
# 이내에서 연장할 수 있고 교통이 불 1 30 , 편한". Bucketing words by `top` and
# sorting each bucket by `x0` restores "청구기간은 누구나 1회에 한하여 30일
# 이내에서 연장할 수 있고, 교통이 불편한". extraction_mode="layout" does not
# fix it ("...교통이 불1 30 , 편한").
LINE_Y_TOLERANCE = 2.5
# pdfplumber's own default word gap. extra_attrs opens a word boundary wherever
# size or font changes even at zero gap, which put spaces inside mixed-font runs
# ("업(業)으로" -> "업( 業 )으로", 41 of 5308 sampled lines). Re-joining on the
# measured gap instead of unconditionally on a space undoes exactly that.
#
# The gap is only the SECOND test, because it is producer-dependent and this
# constant is not. 유사상품 심사기준 sets its table cells with a 2.5pt space
# glyph - under this tolerance - so joining on the gap alone glued every word in
# them together: "불을끄거나예방하기위한기기나장치", "가스누출감지기". MEASURED
# over five sampled pages of that document, 941 of 1277 word joins lost a real
# space; over five pages each of 특허·실용신안 심사기준 (893 joins) and
# 상표심사기준 (1111 joins) the same measurement found ZERO, which is why the
# tolerance stays 3 and a blank GLYPH between the two words is what overrides it.
# A blank glyph is evidence, not a heuristic: the mixed-font runs above have no
# space character between them, so they stay glued either way.
WORD_X_TOLERANCE = 3
# Half a point of slack on each side when asking whether a blank glyph falls
# between two words: pdfminer reports glyph edges in float space and a space
# advance can land a hair outside its neighbours' bounding boxes.
BLANK_SPAN_SLACK = 0.5

# Column detection. A gutter this wide that no glyph on the page crosses is a
# column rule; anything narrower is inter-word space, and anything closer than
# MIN_COLUMN_PT to either end of the text block is the margin.
MIN_GUTTER_PT = 12
MIN_COLUMN_PT = 40

# Running headers and footers. MEASURED on the same document: a line sits in the
# top band on 774 of 854 pages and in the bottom band on 666 - but an ordinary
# body band reaches 52%, so position alone cannot separate furniture from text.
# The same TEXT at the same band is the discriminator; body text never repeats.
FURNITURE_BAND_PT = 10
FURNITURE_MIN_PAGES = 5
# That header is the only extractable structure this document has - its
# sub-headings are rasterised images (7 per page, confirmed against page.images
# in the exact vertical gaps where they belong), so font size finds nothing
# below chapter level. The header alternates verso/recto - part title on even
# pages, chapter title on odd - so "changed since the previous page" would fire
# on every page. Remembering the last four texts per band absorbs any period-2
# alternation and still opens a heading at a real section change.
FURNITURE_MEMORY = 4

# Font-size headings, against the DOCUMENT's modal size rather than the page's.
# Per-page was measured worse: a page that is mostly a 10pt table drags the mode
# down and every ordinary 11pt body line on it reads as a heading (169 hits
# document-wide, mostly that). Document-wide gives ~25, all front matter titles.
# The 1.15 margin is what keeps 12pt inline citations ("[규정24(3), (4)]") out.
HEADING_SIZE_RATIO = 1.15

# Korean wraps mid-word with no hyphen, so joining wrapped lines with a space
# yields "보 정서의" and "교통이 불 편한". Hangul on both sides of the break
# means the word continues - but only 64.5% of the time. MEASURED over the
# 12,675 Hangul-to-Hangul line breaks in the 854-page document: 8,176 are
# mid-word and 4,499 fall on a real word boundary, and this producer emits a
# trailing space glyph on exactly the second kind. So the glyph decides where it
# exists and the Hangul rule only covers the rest.
HANGUL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")
CJK = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏一-鿿]")
PAGE_NUMBER = re.compile(r"^\d+$|^[ivxlcdm]{1,7}$|^[IVXLCDM]{1,7}$")
# A table-of-contents entry carries the same "3.2 Title" shape as the heading it
# points at. The leader run is what tells them apart, and the 26 contents pages
# of the Korean document contributed ~200 of the 563 numbered-heading hits.
LEADER_DOTS = re.compile(r"[.·․‥…]{4,}")


class _Line(NamedTuple):
    band: int
    text: str
    size: float
    bold: bool
    ends_blank: bool


def _is_heading(
    line: str, next_line: str, section_marker: re.Pattern[str] | None = None
) -> bool:
    """Deliberately conservative, because a false heading is not cheap: the
    detected text becomes current_section and is stamped on every block that
    follows, and section is what a citation shows the user. One misread line
    relabels the rest of the document. Missing a heading only costs a chunk
    boundary, which the size pass in Task 9 supplies anyway."""
    stripped = line.strip()
    if not stripped:
        return False
    # Before the length and punctuation guards, because this one is EVIDENCE and
    # not a heuristic: the caller's collection SAYS this is what a section header
    # looks like in its documents, so a line matching it is one by construction.
    # It has to be here at all because every test below rejects the shape the
    # shipped Korean-IP preset matches - the line is Hangul (the CJK guard returns
    # False for anything cased-ambiguous), it is set in 10.5pt against a 9.5pt body
    # so _is_font_heading misses it at the 1.15 ratio, and a long goods name pushes
    # it past MAX_HEADING_CHARS. Measured on 유사상품 심사기준: 931 such lines, of
    # which only 63 survived into a block of their own; the other 868 were joined
    # into the paragraph of the PREVIOUS section's goods list, which is why
    # "[제9류/G390802] 소프트웨어" and its 상품의 범위 were retrievable only as the
    # tail of a chunk about robots. None means the collection configured no such
    # pattern, and then only the heuristics below run.
    if section_marker is not None and section_marker.match(stripped):
        return True
    if len(stripped) > MAX_HEADING_CHARS:
        return False
    if stripped[-1] in SENTENCE_ENDINGS:
        return False
    if LEADER_DOTS.search(stripped):
        return False
    if NUMBERED_HEADING.match(stripped):
        return True
    if CJK.search(stripped):
        # Everything below is a Latin case test, and str.isupper()/str.istitle()
        # both answer True for a line whose only cased character is incidental -
        # Hangul and Han are uncased. MEASURED on the 854-page Korean document:
        # 691 of 703 isupper() hits and 23 of 36 istitle() hits were ordinary
        # body prose or table rows ("A (구성1) A (기재된 위치)"), and each one
        # became current_section and relabelled every citation after it. That is
        # where the garbage section string in the shipped chunks came from.
        return False
    words = stripped.split()
    if stripped.isupper() and len(words) <= MAX_HEADING_WORDS:
        return True
    # A short title-cased line that the following line does not continue in
    # lower case. The obvious "short line followed by a blank line" shape is
    # unusable here: a PDF page carries no blank lines between its lines, so
    # that rule would be dead except on the last line of a page, where it
    # misfires on wrapped body text. istitle() buys that safety by missing
    # headings with lowercase stop-words ("Results and Discussion"),
    # possessives ("The Company's Results"), or a trailing colon ("Results:",
    # rejected above as sentence punctuation). Its blast radius is wider than it
    # looks: bare-numbered headings now fall through to this rule and are caught
    # by the same ceiling, so "2 Materials and Methods" and "3 Results and
    # Discussion" - which the old bare-number regex accepted - are missed by the
    # regex AND by istitle(). Missing a heading is still the cheap direction
    # (Task 9's size pass supplies the boundary; a false heading mislabels every
    # citation after it), but the tightening costs more real headings than the
    # numbered-prose cases alone.
    return len(words) <= 8 and stripped.istitle() and not next_line[:1].islower()


def _is_font_heading(line: _Line, body_size: float) -> bool:
    if len(line.text) > MAX_HEADING_CHARS:
        return False
    return line.size > body_size * HEADING_SIZE_RATIO or line.bold


def _join_wrapped(lines: list[_Line]) -> str:
    joined = lines[0].text
    for previous, line in zip(lines, lines[1:], strict=False):
        glued = (
            not previous.ends_blank
            and HANGUL.match(joined[-1:])
            and HANGUL.match(line.text[:1])
        )
        joined = f"{joined}{'' if glued else ' '}{line.text}"
    return joined


def _flush(blocks: list[Block], paragraph: list[_Line], page: int, section: str | None) -> None:
    """Emit the buffered lines as one paragraph block and reset the buffer. A
    module-level function rather than a closure over the page loop, which is
    what ruff's B023 objects to."""
    if paragraph:
        blocks.append(
            Block(
                text=_join_wrapped(paragraph).strip(),
                block_type="paragraph",
                page=page,
                section=section,
            )
        )
        paragraph.clear()


def _columns(words: list[dict], width: float) -> list[list[dict]]:
    """The page's words split at every full-height vertical whitespace gutter.

    A gutter is an x band that NO glyph on the page crosses - not a wide gap on
    some lines, which is what an ordinary indent or a right-aligned page number
    looks like. That is what makes it safe to apply to every document rather than
    to a document this parser knows about: MEASURED over 40 sampled pages each,
    유사상품 심사기준 has one on 34 of them (at x 303-321, the goods table's own
    column rule, on every one of its classification pages) while 특허·실용신안
    심사기준 has one on 1 and 상표심사기준 on 2 - a cover and an appendix, both of
    which genuinely are two columns.

    Returns [words] unchanged when there is no gutter, which is the single-column
    case and every page of a prose document.
    """
    if not words:
        return []
    occupied = bytearray(int(width) + 2)
    for word in words:
        for x in range(max(0, int(word["x0"])), min(len(occupied) - 1, int(word["x1"]) + 1)):
            occupied[x] = 1
    left, right = int(min(w["x0"] for w in words)), int(max(w["x1"] for w in words))

    edges: list[float] = []
    run: int | None = None
    for x in range(left, right + 2):
        if x < len(occupied) and not occupied[x]:
            run = x if run is None else run
            continue
        if (
            run is not None
            and x - run >= MIN_GUTTER_PT
            # Inside the text block, not the margin either side of it: a page
            # whose first column is 20pt wide is a misread, not a column.
            and run > left + MIN_COLUMN_PT
            and x < right - MIN_COLUMN_PT
        ):
            edges.append((run + x) / 2)
        run = None
    if not edges:
        return [words]

    columns: list[list[dict]] = [[] for _ in range(len(edges) + 1)]
    for word in words:
        columns[bisect.bisect_right(edges, word["x0"])].append(word)
    return [column for column in columns if column]


def _page_lines(page) -> list[_Line]:
    words = page.extract_words(extra_attrs=["size", "fontname"])
    # Every blank glyph's span per text row. extract_words drops blank chars, so
    # these spans are the only evidence of two things: where a wrap fell on a real
    # word boundary (the rightmost one), and where a space narrower than
    # WORD_X_TOLERANCE separates two words (any of them).
    blanks: dict[float, list[tuple[float, float]]] = {}
    for char in page.chars:
        if char["text"].isspace():
            blanks.setdefault(round(char["top"], 1), []).append((char["x0"], char["x1"]))

    buckets: list[tuple[int, float, list[dict]]] = []
    # ONE bucket list across every column, and the column index is part of the
    # sort key, so a bucket only ever holds words from one column. Bucketing on
    # `top` alone is what glued the two columns of the goods table together:
    # "- 방화피복(제9류/G4507) 가스누출 경보기 gas leak alarms" is a cross-reference
    # from the left column and an unrelated goods name from the right, read as
    # one line. Columns are emitted whole, in reading order, because that is the
    # order the document is written in.
    columns = _columns(words, float(page.width))
    for index, column in enumerate(columns):
        for word in sorted(column, key=lambda w: (round(w["top"], 1), w["x0"])):
            for column_index, top, bucket in buckets:
                if column_index == index and abs(top - word["top"]) <= LINE_Y_TOLERANCE:
                    bucket.append(word)
                    break
            else:
                buckets.append((index, word["top"], [word]))

    lines: list[_Line] = []
    for _, top, bucket in buckets:
        ordered = sorted(bucket, key=lambda w: w["x0"])
        spans = [
            span
            for row, row_spans in blanks.items()
            if abs(row - top) <= LINE_Y_TOLERANCE
            for span in row_spans
        ]
        text = ordered[0]["text"]
        for previous, word in zip(ordered, ordered[1:], strict=False):
            spaced = word["x0"] - previous["x1"] > WORD_X_TOLERANCE or any(
                x0 >= previous["x1"] - BLANK_SPAN_SLACK
                and x1 <= word["x0"] + BLANK_SPAN_SLACK
                for x0, x1 in spans
            )
            text = f"{text}{' ' if spaced else ''}{word['text']}"
        text = text.strip()
        if not text:
            continue
        last_x1 = max(word["x1"] for word in bucket)
        lines.append(
            _Line(
                band=round(top / FURNITURE_BAND_PT),
                text=text,
                size=max(word["size"] for word in bucket),
                bold=any("bold" in word["fontname"].lower() for word in bucket),
                ends_blank=any(x1 > last_x1 for _, x1 in spans),
            )
        )
    return lines


class PdfParser(Parser):
    def parse(self, path: str, section_marker: re.Pattern[str] | None = None) -> ParsedDocument:
        with pdfplumber.open(path) as pdf:
            pages: list[list[_Line]] = []
            for page in pdf.pages:
                pages.append(_page_lines(page))
                # 854 pages of cached pdfminer objects do not fit in a worker.
                page.flush_cache()
                page.get_textmap.cache_clear()

        # Weighted by characters, not by lines: a document's body size is the
        # one most of its TEXT is set in, and a title page of one-word lines
        # outvotes a page of prose under a per-line count.
        weighted = Counter()
        for page_lines in pages:
            for line in page_lines:
                weighted[round(line.size, 1)] += len(line.text)
        body_size = weighted.most_common(1)[0][0] if weighted else 0.0
        repeats = Counter((line.band, line.text) for page_lines in pages for line in page_lines)
        furniture = {key for key, count in repeats.items() if count >= FURNITURE_MIN_PAGES}
        recent: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=FURNITURE_MEMORY))

        blocks: list[Block] = []
        current_section: str | None = None

        for page_number, page_lines in enumerate(pages, start=1):
            paragraph: list[_Line] = []
            texts = [line.text for line in page_lines]

            for index, (line, next_text) in enumerate(
                zip_longest(page_lines, texts[1:], fillvalue="")
            ):
                if (line.band, line.text) in furniture:
                    # The publisher printed this section name on this page, so
                    # it is authoritative for it - re-asserting it here is what
                    # takes the section back from an in-page heuristic hit on
                    # the page before. But it only carries NEW information where
                    # it changes, so only then does it emit a block.
                    seen = line.text in recent[line.band]
                    recent[line.band].append(line.text)
                    current_section = line.text
                    if seen:
                        continue
                # A folio never repeats verbatim, so the furniture rule cannot
                # catch it; its position can. First or last line of the page.
                elif PAGE_NUMBER.match(line.text) and index in (0, len(page_lines) - 1):
                    continue
                elif not (
                    _is_font_heading(line, body_size)
                    or _is_heading(line.text, next_text, section_marker)
                ):
                    paragraph.append(line)
                    continue

                _flush(blocks, paragraph, page_number, current_section)
                current_section = line.text
                blocks.append(
                    Block(
                        text=line.text,
                        block_type="heading",
                        page=page_number,
                        section=current_section,
                    )
                )

            _flush(blocks, paragraph, page_number, current_section)

        return ParsedDocument(blocks=blocks)
