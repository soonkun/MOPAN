from app.rag.parsers.base import Parser
from app.rag.parsers.docx_parser import DocxParser
from app.rag.parsers.html_parser import HtmlParser
from app.rag.parsers.pdf_parser import PdfParser
from app.rag.parsers.text_parser import TextParser

_TEXT = TextParser()

# Adding a format is one dict entry here - no import-order-dependent
# registration, no linear supports() scan, no silently-empty registry - plus
# matching entries in app/documents/validation.py's ALLOWED_EXTENSIONS,
# ALLOWED_CONTENT_TYPES and EXPECTED_MAGIC_MIME, or uploads of it are rejected
# before any parser is reached.
PARSERS: dict[str, Parser] = {
    "txt": _TEXT,
    "md": _TEXT,
    "html": HtmlParser(),
    "pdf": PdfParser(),
    "docx": DocxParser(),
}


def get_parser(file_type: str) -> Parser:
    try:
        return PARSERS[file_type.lower()]
    except KeyError as exc:
        raise ValueError(f"no parser registered for file type: {file_type}") from exc


__all__ = ["PARSERS", "Parser", "get_parser", "TextParser", "HtmlParser", "PdfParser", "DocxParser"]
