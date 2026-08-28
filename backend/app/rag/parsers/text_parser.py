from pathlib import Path

from app.rag.blocks import Block, ParsedDocument
from app.rag.parsers.base import Parser


class TextParser(Parser):
    """Handles .txt and .md. Markdown '#' headings become heading blocks."""

    def parse(self, path: str) -> ParsedDocument:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        blocks: list[Block] = []
        current_section: str | None = None

        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                heading_text = line.lstrip("#").strip()
                current_section = heading_text
                blocks.append(Block(text=heading_text, block_type="heading", section=current_section))
            elif line.startswith(("-", "*")) and len(line) > 1 and line[1] == " ":
                blocks.append(Block(text=line[2:].strip(), block_type="list_item", section=current_section))
            else:
                blocks.append(Block(text=line, block_type="paragraph", section=current_section))

        return ParsedDocument(blocks=blocks)
