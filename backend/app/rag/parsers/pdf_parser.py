import re
from itertools import zip_longest

from pypdf import PdfReader

from app.rag.blocks import Block, ParsedDocument
from app.rag.parsers.base import Parser

MAX_HEADING_CHARS = 80
MAX_HEADING_WORDS = 12
NUMBERED_HEADING = re.compile(r"^\d+(\.\d+)*[.)]?\s+\S")
SENTENCE_ENDINGS = ".!?,;:"


def _is_heading(line: str, next_line: str) -> bool:
    """Deliberately conservative. A false heading only adds a chunk boundary, but
    the size pass in Task 9 bounds chunks anyway - so it is better to miss a
    heading than to shred every wrapped line into its own chunk."""
    stripped = line.strip()
    if not stripped or len(stripped) > MAX_HEADING_CHARS:
        return False
    if stripped[-1] in SENTENCE_ENDINGS:
        return False
    if NUMBERED_HEADING.match(stripped):
        return True
    words = stripped.split()
    if stripped.isupper() and len(words) <= MAX_HEADING_WORDS:
        return True
    # A short title-cased line that the following line does not continue in
    # lower case. The obvious "short line followed by a blank line" shape is
    # unusable here: pypdf collapses vertical whitespace, so extract_text never
    # emits a blank line between two lines of a page - that rule would be dead
    # except on the last line of a page, where it misfires on wrapped body text.
    return len(words) <= 8 and stripped.istitle() and not next_line[:1].islower()


def _flush(blocks: list[Block], paragraph: list[str], page: int, section: str | None) -> None:
    """Emit the buffered lines as one paragraph block and reset the buffer. A
    module-level function rather than a closure over the page loop, which is
    what ruff's B023 objects to."""
    if paragraph:
        blocks.append(
            Block(
                text=" ".join(paragraph).strip(),
                block_type="paragraph",
                page=page,
                section=section,
            )
        )
        paragraph.clear()


class PdfParser(Parser):
    def parse(self, path: str) -> ParsedDocument:
        reader = PdfReader(path)
        blocks: list[Block] = []
        current_section: str | None = None

        for page_number, page in enumerate(reader.pages, start=1):
            lines = (page.extract_text() or "").split("\n")
            paragraph: list[str] = []

            for line, next_line in zip_longest(lines, lines[1:], fillvalue=""):
                stripped = line.strip()
                if not stripped:
                    _flush(blocks, paragraph, page_number, current_section)
                    continue
                if _is_heading(stripped, next_line):
                    _flush(blocks, paragraph, page_number, current_section)
                    current_section = stripped
                    blocks.append(
                        Block(
                            text=stripped,
                            block_type="heading",
                            page=page_number,
                            section=current_section,
                        )
                    )
                    continue
                paragraph.append(stripped)

            _flush(blocks, paragraph, page_number, current_section)

        return ParsedDocument(blocks=blocks)
