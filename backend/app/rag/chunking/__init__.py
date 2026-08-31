from app.core.config import Settings
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy, EmbedFn
from app.rag.chunking.fixed import FixedChunking
from app.rag.chunking.hierarchy import (
    STRATEGY as HIERARCHICAL_STRATEGY,
)
from app.rag.chunking.hierarchy import (
    Detection,
    HierarchicalChunking,
    Scheme,
    detect,
    resolve_scheme,
    section_marker,
)
from app.rag.chunking.semantic import StructureSemanticChunking
from app.rag.chunking.structure import build_size_bounded_candidates
from app.rag.chunking.table import (
    PRESETS,
    STRATEGY,
    ClassificationTableChunking,
    SectionMarkers,
)
from app.rag.chunking.table import (
    resolve as resolve_table,
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


def resolve(chunking: dict | None) -> SectionMarkers | None:
    """A collection's `chunking` JSON -> the pattern the PARSER needs, or None.

    Two jobs in one call, because both have exactly one right moment. It VALIDATES
    - an unknown strategy or a preset that does not exist raises here, so a typo
    lands on whoever is saving the setting rather than on the next upload - and it
    hands back the section-header pattern, which has to reach the parser and not
    only the chunker: a header line the layout pass glued onto the previous
    paragraph is a boundary the chunker never sees. Measured on 유사상품 심사기준,
    868 of its 931 markers were swallowed that way.

    Which names exist is decided HERE rather than in either strategy module, so
    adding a third one is one branch in one place.
    """
    strategy = (chunking or {}).get("strategy")
    if strategy not in (None, "", STRATEGY, HIERARCHICAL_STRATEGY):
        raise ValueError(f"unknown chunking strategy: {strategy!r}")
    scheme = resolve_scheme(chunking)
    if scheme is not None:
        # A hierarchy has no head line and no section terminator; only the union
        # of its level patterns, so that every 편/장/조/항 opener survives as a
        # block of its own.
        return SectionMarkers(marker=section_marker(scheme))
    return resolve_table(chunking)


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
    # VALIDATE FIRST, and not because it is tidy. `resolve_scheme` and
    # `resolve_table` both answer None for a strategy that is not theirs, so
    # `{"strategy": "clasification_table"}` used to fall straight through both and
    # get the deployment's prose strategy - a typo silently ingesting a thousand
    # pages the wrong way, which is the exact failure mode 0013 was written to end.
    # `resolve` is the one place that knows which names exist, and it raises.
    resolve(chunking)
    scheme = resolve_scheme(chunking)
    if scheme is not None:
        # WHAT THIS IS NOT: the last word. The collection supplies the VOCABULARY
        # (what a 조 looks like, how a citation is written); whether THIS document
        # uses it is answered per document by `detect()` in the pipeline, which
        # falls back to `_configured_strategy` for a document that turns out to be
        # ordinary prose. One collection here already holds both kinds.
        return HierarchicalChunking(
            scheme,
            # The prose BETWEEN the numbered provisions is still prose, and it is
            # most of a reference-dependent document (99 조 openers in 5,878 blocks
            # of 특허·실용신안 심사기준, measured). It keeps the deployment's own
            # strategy so that adding structure does not silently re-cut the 95%
            # of the corpus the structure has nothing to say about.
            prose=_configured_strategy(settings),
            max_chunk_tokens=settings.max_chunk_tokens,
            target_chars=settings.chunk_size,
            overlap_chars=settings.chunk_overlap,
        )
    markers = resolve_table(chunking)
    if markers is None:
        return _configured_strategy(settings)
    return ClassificationTableChunking(
        markers,
        max_chunk_tokens=settings.max_chunk_tokens,
        target_chars=settings.chunk_size,
        overlap_chars=settings.chunk_overlap,
    )


__all__ = [
    "HIERARCHICAL_STRATEGY",
    "PRESETS",
    "STRATEGY",
    "ChunkCandidate",
    "ChunkingStrategy",
    "ClassificationTableChunking",
    "Detection",
    "EmbedFn",
    "FixedChunking",
    "HierarchicalChunking",
    "Scheme",
    "SectionMarkers",
    "StructureSemanticChunking",
    "build_size_bounded_candidates",
    "detect",
    "get_chunking_strategy",
    "resolve",
    "resolve_scheme",
    "resolve_table",
    "section_marker",
]
