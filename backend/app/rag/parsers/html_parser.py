from pathlib import Path

from bs4 import BeautifulSoup

from app.rag.blocks import Block, ParsedDocument
from app.rag.parsers.base import Parser

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCK_TAGS = [*HEADING_TAGS, "p", "li", "td", "th"]


class HtmlParser(Parser):
    def parse(self, path: str) -> ParsedDocument:
        soup = BeautifulSoup(Path(path).read_text(encoding="utf-8", errors="replace"), "html.parser")
        for tag in soup.find_all(["script", "style"]):
            tag.decompose()

        blocks: list[Block] = []
        current_section: str | None = None

        for tag in soup.find_all(BLOCK_TAGS):
            # A <p> inside a <td> is already covered by the <td> block above it;
            # emitting both indexes and retrieves the same text twice.
            if tag.find_parent(BLOCK_TAGS):
                continue
            # Separator matters: get_text(strip=True) strips each string first
            # and then concatenates, so "Hello <b>world</b>" becomes
            # "Helloworld" - every document with inline markup comes out mangled.
            text = tag.get_text(" ", strip=True)
            if not text:
                continue
            if tag.name in HEADING_TAGS:
                current_section = text
                blocks.append(Block(text=text, block_type="heading", section=current_section))
            elif tag.name == "li":
                blocks.append(Block(text=text, block_type="list_item", section=current_section))
            elif tag.name in {"td", "th"}:
                blocks.append(Block(text=text, block_type="table_cell", section=current_section))
            else:
                blocks.append(Block(text=text, block_type="paragraph", section=current_section))

        return ParsedDocument(blocks=blocks)
