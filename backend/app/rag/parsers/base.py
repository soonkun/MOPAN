from abc import ABC, abstractmethod

from app.rag.blocks import ParsedDocument


class Parser(ABC):
    """Synchronous by design: parsing is CPU-bound. Callers must run it through
    anyio.to_thread so it never blocks the API or worker event loop."""

    @abstractmethod
    def parse(self, path: str) -> ParsedDocument: ...
