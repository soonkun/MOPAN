import math
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

    Ties are broken by first appearance, which the stable sort gives for free:
    the earlier ranking wins, and the order never depends on hash order.

    THAT PROPERTY USED TO HAVE A CEILING OF TWO RANKINGS, and multi-query
    expansion (app/retrieval/expansion.py) passes more. The old note here was
    right: with `+=` the same addends arrive in a different order per id - id A
    scores 1/61 + 1/70, id B scores 1/70 + 1/61 - and float addition is not
    associative, so two nominally equal scores land 1 ulp apart and stop
    comparing equal. Still deterministic, but no longer a tie, and the
    first-appearance rule silently stops applying to exactly the ids it was
    written for.

    The fix is `math.fsum`, which is exactly rounded and therefore
    ORDER-INDEPENDENT: the same multiset of addends gives bit-identical results
    whatever order they arrive in, so nominally equal scores compare equal again
    and the stable sort resumes breaking the tie by first appearance. Hence the
    per-id list of contributions below rather than a running total - it is the
    cheapest thing that restores the documented invariant at any number of
    rankings, and it is what makes it safe to pass more than two.
    """
    if k < 0:
        raise ValueError(f"rrf k must be >= 0, got {k}")
    if weights is None:
        weights = [1.0] * len(rankings)
    elif len(weights) != len(rankings):
        raise ValueError(f"got {len(weights)} weights for {len(rankings)} rankings")
    elif any(weight < 0 for weight in weights):
        raise ValueError(f"rrf weights must be >= 0, got {weights}")

    # Insertion order is first-appearance order across every ranking, which is
    # what the stable sort at the bottom turns into the tie-break.
    contributions: dict[str, list[float]] = defaultdict(list)
    for ranking, weight in zip(rankings, weights, strict=True):
        # dict.fromkeys de-duplicates while keeping order. An id repeated within
        # one ranking is malformed input - a list of ranks has each id once - and
        # counting both positions would let one retriever's bug inflate its own
        # candidate above every honest one. Per ranking, so an id in two lists
        # still scores twice.
        for position, item_id in enumerate(dict.fromkeys(ranking), start=1):
            contributions[item_id].append(weight / (k + position))

    scores = [(item_id, math.fsum(terms)) for item_id, terms in contributions.items()]
    return sorted(scores, key=lambda pair: pair[1], reverse=True)
