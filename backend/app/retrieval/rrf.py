from collections import defaultdict


def reciprocal_rank_fusion(
    rankings: list[list[str]], *, k: int, weights: list[float] | None = None
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(id) = sum over rankings of w / (k + rank),
    with rank starting at 1. A pure function - no model, no LLM, no I/O.

    `rankings` is one ordered list of ids per retriever, best first; the same id
    in several lists is the point, and its contributions stack. `k` is required
    rather than defaulted, so the value can only come from `Settings.rrf_k` and
    cannot silently drift from it. k=0 is legal (pure reciprocal rank); k<0 is
    not a ranking parameter at all and is rejected rather than dividing by zero
    at rank -k.

    `weights` is one multiplier per ranking, defaulting to 1.0 each - textbook
    RRF, where every retriever is a peer. It exists because on the Korean corpus
    they measurably are not: see the note over `sparse_weight` in Settings, and
    scripts/eval_retrieval.py for the numbers. A weight of 0 silences a ranking
    without changing the call site's shape; negatives are rejected because a
    ranking that subtracts is not a ranking.

    Ties are broken by FIRST APPEARANCE, recorded explicitly rather than left to
    the stable sort. The stable sort used to be the whole mechanism, and its
    guarantee held only while a fused score was a sum of at most two terms:
    with three or more rankings the same addends arrive in a different order per
    id and land 1 ulp apart, so nominally equal scores stop comparing equal.
    Query expansion makes that the normal case - N rewrites feed both arms, so
    2N rankings arrive here - and a docstring promising a property the code no
    longer had is worse than no promise. `first_seen` restores it for any number
    of rankings: two ids whose float scores compare equal are ordered by which
    retriever saw one first, and the result never depends on dict or hash order.
    """
    if k < 0:
        raise ValueError(f"rrf k must be >= 0, got {k}")
    if weights is None:
        weights = [1.0] * len(rankings)
    elif len(weights) != len(rankings):
        raise ValueError(f"got {len(weights)} weights for {len(rankings)} rankings")
    elif any(weight < 0 for weight in weights):
        raise ValueError(f"rrf weights must be >= 0, got {weights}")

    scores: dict[str, float] = defaultdict(float)
    first_seen: dict[str, int] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        # dict.fromkeys de-duplicates while keeping order. An id repeated within
        # one ranking is malformed input - a list of ranks has each id once - and
        # counting both positions would let one retriever's bug inflate its own
        # candidate above every honest one. Per ranking, so an id in two lists
        # still scores twice.
        for position, item_id in enumerate(dict.fromkeys(ranking), start=1):
            scores[item_id] += weight / (k + position)
            # setdefault, not assignment: the FIRST ranking to offer an id owns
            # its tie-break position, exactly as the stable sort used to give.
            first_seen.setdefault(item_id, len(first_seen))

    # Descending score, ascending first-appearance. Two sort keys rather than
    # `reverse=True` because the two directions differ: reversing the whole
    # comparison would make a later-seen id win a tie.
    return sorted(scores.items(), key=lambda pair: (-pair[1], first_seen[pair[0]]))
