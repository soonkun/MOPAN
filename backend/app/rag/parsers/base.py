import re
from abc import ABC, abstractmethod

from app.rag.blocks import ParsedDocument


class Parser(ABC):
    """Synchronous by design: parsing is CPU-bound. Callers must run it through
    anyio.to_thread so it never blocks the API or worker event loop."""

    @abstractmethod
    def parse(self, path: str, section_marker: re.Pattern[str] | None = None) -> ParsedDocument:
        """`section_marker` is the collection's configured section-header pattern,
        the same one the chunking layer cuts on. A line matching it is a heading
        BY CONSTRUCTION rather than by heuristic, so a parser that can tell lines
        apart should say so; one that cannot is free to ignore it.

        It is threaded rather than hardcoded because the pattern is per-collection
        configuration - see app/rag/chunking/table.py. It used to be a Korean
        goods-classification regex living in app/rag/blocks.py, which no other
        user of this product could have benefited from.
        """
