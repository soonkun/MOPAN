import pytest

from app.retrieval.rrf import reciprocal_rank_fusion


def test_rrf_favors_id_ranked_high_in_both_lists():
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["a", "c", "b"]], k=60)
    assert fused[0][0] == "a"


def test_rrf_score_matches_formula():
    # Pins the arithmetic itself - 1 / (k + rank), rank starting at 1 - not just
    # that the favoured id sorts first. A wrong constant or a 0-based rank passes
    # every ordering assertion in this file and fails only here.
    fused = dict(reciprocal_rank_fusion([["a", "b"]], k=60))
    assert fused["a"] == 1 / (60 + 1)
    assert fused["b"] == 1 / (60 + 2)


def test_rrf_sums_contributions_across_rankings():
    fused = dict(reciprocal_rank_fusion([["a"], ["a"]], k=60))
    assert fused["a"] == 2 / 61


def test_rrf_includes_ids_present_in_only_one_ranking():
    fused = reciprocal_rank_fusion([["a"], ["b"]], k=60)
    assert {id_ for id_, _ in fused} == {"a", "b"}


def test_rrf_k_changes_the_score_but_not_the_order():
    small = reciprocal_rank_fusion([["a", "b"]], k=1)
    large = reciprocal_rank_fusion([["a", "b"]], k=1000)
    assert [i for i, _ in small] == [i for i, _ in large]
    assert small[0][1] > large[0][1]


def test_rrf_empty_rankings_returns_empty_list():
    assert reciprocal_rank_fusion([], k=60) == []
    assert reciprocal_rank_fusion([[], []], k=60) == []


def test_rrf_an_empty_ranking_beside_a_full_one_contributes_nothing():
    # An empty list is what a keyword search returns for a query with no lexical
    # match. It must not shift the other ranking's ranks or its scores.
    assert reciprocal_rank_fusion([[], ["a", "b"]], k=60) == reciprocal_rank_fusion([["a", "b"]], k=60)


def test_rrf_counts_a_duplicated_id_once_per_ranking():
    # A ranking is a ranking: an id repeated inside one list is malformed input,
    # and summing both positions would let a buggy retriever inflate its own
    # candidate past everything else. First occurrence wins, and the duplicate
    # does not consume a rank slot - "c" is third, not fourth.
    fused = dict(reciprocal_rank_fusion([["a", "b", "a", "c"]], k=60))
    assert fused["a"] == 1 / 61
    assert fused["b"] == 1 / 62
    assert fused["c"] == 1 / 63


def test_rrf_counts_an_id_once_per_ranking_but_once_in_each():
    # De-duplication is per ranking, not global: appearing in both lists is the
    # whole point of fusion and must still stack.
    fused = dict(reciprocal_rank_fusion([["a", "a"], ["a"]], k=60))
    assert fused["a"] == 2 / 61


def test_rrf_k_zero_is_pure_reciprocal_rank():
    # k=0 is legal, not a division by zero, because rank starts at 1. It is the
    # maximally top-heavy setting an admin can dial in.
    fused = dict(reciprocal_rank_fusion([["a", "b"]], k=0))
    assert fused["a"] == 1.0
    assert fused["b"] == 0.5


def test_rrf_rejects_a_negative_k():
    # rrf_k is admin-configurable, so a negative value reaches this function from
    # Settings. k=-1 would be ZeroDivisionError at rank 1 and any k<0 flips the
    # sign of the leading ranks - a nonsense ranking rather than a loud failure.
    with pytest.raises(ValueError, match="k"):
        reciprocal_rank_fusion([["a"]], k=-1)


def test_rrf_breaks_ties_by_first_appearance():
    # Symmetric rankings tie exactly. Ties resolve to the order the ids were
    # first seen - so the ranking passed first wins - and never to dict/hash
    # order, which would make retrieval irreproducible across runs.
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]], k=60)
    assert fused[0][1] == fused[1][1]
    assert [id_ for id_, _ in fused] == ["a", "b"]
    swapped = reciprocal_rank_fusion([["b", "a"], ["a", "b"]], k=60)
    assert [id_ for id_, _ in swapped] == ["b", "a"]


def test_rrf_is_deterministic_across_repeated_calls():
    rankings = [[f"id-{n}" for n in range(50)], [f"id-{n}" for n in range(49, -1, -1)]]
    first = reciprocal_rank_fusion(rankings, k=60)
    assert all(reciprocal_rank_fusion(rankings, k=60) == first for _ in range(5))


def test_rrf_accumulates_over_many_rankings():
    # Float addition, so exact equality is not promised past the trivial cases
    # above; what must hold is that every ranking still adds and none is lost.
    ten = dict(reciprocal_rank_fusion([["a"]] * 10, k=60))["a"]
    nine = dict(reciprocal_rank_fusion([["a"]] * 9, k=60))["a"]
    assert ten == pytest.approx(10 / 61)
    assert ten > nine


def test_rrf_handles_a_long_ranking_in_full_descending_order():
    ranking = [f"id-{n}" for n in range(1000)]
    fused = reciprocal_rank_fusion([ranking], k=60)
    assert len(fused) == 1000
    assert [id_ for id_, _ in fused] == ranking
    assert fused[-1][1] == 1 / (60 + 1000)


def test_rrf_does_not_mutate_its_input():
    rankings = [["a", "b"], ["b", "a"]]
    reciprocal_rank_fusion(rankings, k=60)
    assert rankings == [["a", "b"], ["b", "a"]]
