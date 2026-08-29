from collections import defaultdict


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(id) = sum over rankings of 1 / (k + rank),
    with rank starting at 1. A pure function - no model, no LLM, no I/O.

    `rankings` is one ordered list of ids per retriever, best first; the same id
    in several lists is the point, and its contributions stack. `k` is required
    rather than defaulted, so the value can only come from `Settings.rrf_k` and
    cannot silently drift from it. k=0 is legal (pure reciprocal rank); k<0 is
    not a ranking parameter at all and is rejected rather than dividing by zero
    at rank -k.

    Ties are broken by first appearance, which the stable sort gives for free:
    the earlier ranking wins, and the order never depends on hash order. That
    holds exactly while a fused score is a sum of at most two terms, which is
    what Slice 1 passes; with three or more rankings the same addends can arrive
    in a different order per id and land 1 ulp apart, so nominally equal scores
    stop comparing equal. Still deterministic, just no longer a tie.
    """
    if k < 0:
        raise ValueError(f"rrf k must be >= 0, got {k}")

    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        # dict.fromkeys de-duplicates while keeping order. An id repeated within
        # one ranking is malformed input - a list of ranks has each id once - and
        # counting both positions would let one retriever's bug inflate its own
        # candidate above every honest one. Per ranking, so an id in two lists
        # still scores twice.
        for position, item_id in enumerate(dict.fromkeys(ranking), start=1):
            scores[item_id] += 1 / (k + position)

    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
