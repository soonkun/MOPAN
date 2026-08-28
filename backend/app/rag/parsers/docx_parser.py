from pathlib import Path

from docx import Document as DocxDocument

from app.rag.blocks import Block, ParsedDocument
from app.rag.parsers.base import Parser


class DocxParser(Parser):
    def parse(self, path: str) -> ParsedDocument:
        # python-docx raises PackageNotFoundError for a missing file, which is
        # not a FileNotFoundError - the structure endpoint's "source file is no
        # longer available" 404 would degrade into a 500 without this.
        if not Path(path).is_file():
            raise FileNotFoundError(path)

        doc = DocxDocument(path)
        blocks: list[Block] = []
        current_section: str | None = None

        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name if para.style is not None else ""
            if style.startswith("Heading") or style == "Title":
                current_section = text
                blocks.append(Block(text=text, block_type="heading", section=current_section))
            elif style.startswith("List"):
                blocks.append(Block(text=text, block_type="list_item", section=current_section))
            else:
                blocks.append(Block(text=text, block_type="paragraph", section=current_section))

        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text:
                        blocks.append(Block(text=text, block_type="table_cell", section=current_section))

        return ParsedDocument(blocks=blocks)
