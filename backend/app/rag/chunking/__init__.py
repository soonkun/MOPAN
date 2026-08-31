from app.core.config import Settings
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.fixed import FixedChunking
from app.rag.chunking.semantic import StructureSemanticChunking
from app.rag.chunking.structure import build_size_bounded_candidates
from app.rag.chunking.table import (
    PRESETS,
    STRATEGY,
    ClassificationTableChunking,
    SectionMarkers,
    resolve,
)


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


def get_chunking_strategy(
    settings: Settings, chunking: dict | None = None
) -> ChunkingStrategy:
    """The strategy for ONE document, given its collection's `chunking` setting.

    Two levels, and they answer different questions. `settings.chunking_strategy`
    is the deployment-wide default for PROSE and stays admin-selectable as the
    requirements have it. `chunking` is the COLLECTION's own choice, and today the
    only thing it can choose is the section-marker cutter - a collection that says
    nothing gets exactly the prose strategy it always got.

    It is a per-collection setting rather than a global one because a collection is
    what holds one kind of document: a goods-classification table and a manual of
    prose want different cuts, and they live in different collections precisely so
    that they can differ.

    NOTHING IS SNIFFED. This used to wrap every document in the table cutter and
    let the cutter decide, by counting how many lines matched a hardcoded Korean
    goods-classification regex, whether it applied. That is domain knowledge
    disguised as a heuristic - it fired for exactly one corpus, it could not be
    turned on for anybody else's, and it could not be turned off. `resolve`
    raising on an unknown strategy or preset is the other half: a typo in the
    configuration is reported, not ignored.
    """
    markers = resolve(chunking)
    if markers is None:
        return _configured_strategy(settings)
    return ClassificationTableChunking(
        markers,
        max_chunk_tokens=settings.max_chunk_tokens,
        target_chars=settings.chunk_size,
        overlap_chars=settings.chunk_overlap,
    )


__all__ = [
    "PRESETS",
    "STRATEGY",
    "ChunkCandidate",
    "ClassificationTableChunking",
    "ChunkingStrategy",
    "EmbedFn",
    "FixedChunking",
    "SectionMarkers",
    "StructureSemanticChunking",
    "build_size_bounded_candidates",
    "get_chunking_strategy",
    "resolve",
]
