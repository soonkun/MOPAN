from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.rag.blocks import Block


@dataclass
class ChunkCandidate:
    content: str
    token_count: int
    char_count: int
    page: int | None = None
    section: str | None = None
    metadata: dict = field(default_factory=dict)
    # Set by StructureSemanticChunking for candidates that were NOT merged, so
    # the pipeline can reuse the embedding instead of paying for the whole
    # document a second time.
    embedding: list[float] | None = None


EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


class ChunkingStrategy(ABC):
    @abstractmethod
    async def chunk(self, blocks: list[Block], embed_fn: EmbedFn) -> list[ChunkCandidate]: ...
