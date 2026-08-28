from app.core.config import Settings
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.fixed import FixedChunking
from app.rag.chunking.semantic import StructureSemanticChunking
from app.rag.chunking.structure import build_size_bounded_candidates


def get_chunking_strategy(settings: Settings) -> ChunkingStrategy:
    """CHUNKING_STRATEGY is admin-selectable per the requirements; the worker must
    not hardcode one."""
    name = settings.chunking_strategy.lower()
    if name == "semantic":
        return StructureSemanticChunking(
            similarity_threshold=settings.semantic_similarity_threshold,
            max_chunk_tokens=settings.max_chunk_tokens,
        )
    if name == "fixed":
        return FixedChunking(chunk_size=settings.chunk_size, overlap=settings.chunk_overlap)
    raise ValueError(f"unknown chunking strategy: {settings.chunking_strategy}")


__all__ = [
    "ChunkCandidate",
    "ChunkingStrategy",
    "EmbedFn",
    "FixedChunking",
    "StructureSemanticChunking",
    "build_size_bounded_candidates",
    "get_chunking_strategy",
]
