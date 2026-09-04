import pytest

from app.retrieval.collapse import GroupInfo, FusedGroup, fuse_groups, group_key
from app.retrieval.rrf import reciprocal_rank_fusion


# ---------------------------------------------------------------- group_key


def test_hierarchical_key_is_the_full_path():
    # 항까지. 조 단위로 접으면 항을 묻는 질문에 다른 항이 대표로 나간다
    # (측정: statute 0.786 -> 0.714). collapse.py 도크스트링의 표 참조.
    info = GroupInfo(document_id="d1", strategy="hierarchical", path="조36/항1", section=None)
    assert group_key("c1", info) == "d1#p:조36/항1"
    assert group_key("c1", info) != group_key(
        "c2", GroupInfo(document_id="d1", strategy="hierarchical", path="조36/항2", section=None)
    )


def test_table_key_is_the_section_marker():
    info = GroupInfo(
        document_id="d1",
        strategy="classification_table",
        path=None,
        section="[제43류/S120602] 한식점업",
    )
    assert group_key("c1", info) == "d1#s:[제43류/S120602] 한식점업"


def test_same_path_in_two_documents_never_merges():
    # 특허법 제105조와 실용신안법 제27조는 문면이 같아도 다른 법이다. 키가
    # 문서 범위라는 것이 그 안전장치이고, 이 테스트가 그것을 고정한다.
    a = GroupInfo(document_id="특허법", strategy="hierarchical", path="조105/항2", section=None)
    b = GroupInfo(document_id="실용신안법", strategy="hierarchical", path="조105/항2", section=None)
    assert group_key("c1", a) != group_key("c2", b)


def test_prose_chunk_is_its_own_group():
    info = GroupInfo(document_id="d1", strategy=None, path=None, section="어떤 절")
    assert group_key("c1", info) == "c1"
    assert group_key("c1", None) == "c1"


# ---------------------------------------------------------------- 항등 성질


def test_identity_keys_reproduce_plain_rrf_exactly():
    # 붕괴를 끄는 것(모든 id가 자기 그룹)이 곧 기존 RRF라는 성질. 순서와
    # 점수가 전부 같아야 한다 - member_cap이 무엇이든.
    rankings = [["a", "b", "c"], ["c", "a", "d"], ["b", "d"]]
    plain = reciprocal_rank_fusion(rankings, k=60)
    for cap in (1, 2, 5):
        fused = fuse_groups(rankings, k=60, member_cap=cap)
        assert [(g.chunk_id, g.score) for g in fused] == plain


def test_identity_keys_respect_weights():
    rankings = [["a", "b"], ["b", "a"]]
    weights = [1.0, 0.5]
    plain = reciprocal_rank_fusion(rankings, k=60, weights=weights)
    fused = fuse_groups(rankings, k=60, weights=weights)
    assert [(g.chunk_id, g.score) for g in fused] == plain


# ---------------------------------------------------------------- 붕괴 동작


def test_split_votes_pool_into_one_group():
    # 측정된 실패의 최소 재현: 같은 섹션의 조각 둘이 4·5위로 표를 나눠 갖고,
    # 무관한 청크 셋이 1·2·3위다. 청크 단위 RRF에서는 섹션이 4위를 넘지
    # 못하지만, 그룹 융합에서는 두 조각의 표가 합쳐져 그룹이 이긴다.
    keys = {"s1": "G", "s2": "G"}
    rankings = [["x", "y", "z", "s1", "s2"]]
    fused = fuse_groups(rankings, k=0, keys=keys, member_cap=2)
    # 1/4 + 1/5 = 0.45 > 1/3 : 청크 단위라면 4위였을 섹션이 z(1/3)를 제친다.
    assert [g.chunk_id for g in fused[:2]] == ["x", "y"]
    assert fused[2].members == ("s1", "s2")
    assert fused[2].score == pytest.approx(1 / 4 + 1 / 5)


def test_member_cap_limits_what_a_big_group_can_sum():
    # 약한 매칭 셋을 긁어모은 그룹이 cap 때문에 상위 둘만 합산한다.
    keys = {"s1": "G", "s2": "G", "s3": "G"}
    fused = fuse_groups([["s1", "s2", "s3"]], k=0, keys=keys, member_cap=2)
    assert fused[0].score == pytest.approx(1 / 1 + 1 / 2)


def test_representative_is_the_best_single_member_across_arms():
    # dense가 3위로 본 멤버보다 sparse가 1위로 본 멤버가 대표가 된다 -
    # 대표는 "그 그룹을 가장 강하게 찾은 청크"다.
    keys = {"a": "G", "b": "G"}
    fused = fuse_groups([["x", "y", "a"], ["b"]], k=0, keys=keys)
    group = next(g for g in fused if g.members == ("a", "b"))
    assert group.chunk_id == "b"


def test_copies_across_arms_collapse_to_one_slot():
    # 한 조문을 세 문서가 사본으로 갖고 있어도 그룹 키가 같으면(같은 문서의
    # 항들) 슬롯 하나만 쓴다. 문서가 다르면 키가 달라 합쳐지지 않는다는 건
    # group_key 테스트가 잡는다.
    keys = {"c1": "G", "c2": "G"}
    fused = fuse_groups([["c1", "other"], ["c2", "other"]], k=60, keys=keys)
    ids = [g.chunk_id for g in fused]
    assert len([i for i in ids if i in ("c1", "c2")]) == 1
    # 두 팔이 서로 다른 멤버를 찾았어도 그룹의 members에는 둘 다 남는다 -
    # 근거 합의(corroboration) 판정이 이것을 읽는다.
    group = next(g for g in fused if g.chunk_id in ("c1", "c2"))
    assert set(group.members) == {"c1", "c2"}


def test_ties_break_by_first_appearance():
    fused = fuse_groups([["a"], ["b"]], k=60)
    assert [g.chunk_id for g in fused] == ["a", "b"]


def test_rejects_bad_arguments():
    with pytest.raises(ValueError):
        fuse_groups([["a"]], k=-1)
    with pytest.raises(ValueError):
        fuse_groups([["a"]], k=60, member_cap=0)
    with pytest.raises(ValueError):
        fuse_groups([["a"]], k=60, weights=[1.0, 2.0])
    with pytest.raises(ValueError):
        fuse_groups([["a"]], k=60, weights=[-1.0])


def test_returns_named_tuples_with_members():
    fused = fuse_groups([["a"]], k=60)
    assert isinstance(fused[0], FusedGroup)
    assert fused[0].members == ("a",)
