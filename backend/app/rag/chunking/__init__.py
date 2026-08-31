from app.core.config import Settings
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.fixed import FixedChunking
from app.rag.chunking.semantic import StructureSemanticChunking
from app.rag.chunking.structure import build_size_bounded_candidates
from app.rag.chunking.table import ClassificationTableChunking


def _configured_strategy(settings: Settings) -> ChunkingStrategy:
    """CHUNKING_STRATEGY is admin-selectable per the requirements; the worker must
    not hardcode one."""
    name = settings.chunking_strategy.lower()
    if name == "semantic":
        return StructureSemanticChunking(
            similarity_threshold=settings.semantic_similarity_threshold,
            max_chunk_tokens=settings.max_chunk_tokens,
            # CHUNK_SIZE/CHUNK_OVERLAP are the character knobs for BOTH strategies:
            # fixed slides a CHUNK_SIZE window with CHUNK_OVERLAP of carry-over, and
            # semantic aims each chunk at CHUNK_SIZE characters with CHUNK_OVERLAP
            # carried across a size-forced split. Same units, same meaning, so a
            # second pair of settings would only let the two drift apart.
            target_chars=settings.chunk_size,
            overlap_chars=settings.chunk_overlap,
        )
    if name == "fixed":
        return FixedChunking(
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
            max_chunk_tokens=settings.max_chunk_tokens,
        )
    raise ValueError(f"unknown chunking strategy: {settings.chunking_strategy}")


def get_chunking_strategy(settings: Settings) -> ChunkingStrategy:
    """The admin's strategy, wrapped so that a CLASSIFICATION TABLE gets cut on
    its own section markers instead.

    The wrapper is not a second setting and it is not keyed to a filename: it
    inspects the parsed blocks and steps aside for every document that is not one
    (see app/rag/chunking/table.py). Prose is unaffected, and the admin's choice
    still decides how prose is chunked.
    """
    return ClassificationTableChunking(
        _configured_strategy(settings),
        max_chunk_tokens=settings.max_chunk_tokens,
        target_chars=settings.chunk_size,
        overlap_chars=settings.chunk_overlap,
    )


__all__ = [
    "ChunkCandidate",
    "ClassificationTableChunking",
    "ChunkingStrategy",
    "EmbedFn",
    "FixedChunking",
    "StructureSemanticChunking",
    "build_size_bounded_candidates",
    "get_chunking_strategy",
]
