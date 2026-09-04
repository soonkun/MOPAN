"""그룹 접기 융합 — 같은 주소(조문 항·분류표 섹션)의 청크가 슬롯을 겹쳐 쓰지 않게 한다.

측정이 이 모듈의 존재 이유이고, 야심이 꺾인 자리도 측정이다 (2026-09-05,
scripts/eval_retrieval.py --collapse 스윕, 86문항 + 구어체 10문항):

    구성                          ALL anchor  crossref  statute  proviso(recall)
    끔 (배포 베이스라인)              0.872     0.880    0.786    0.800
    조 단위 + 합산2 + 5배 깊이        0.814     0.800    0.643    0.600
    항 단위 + 합산2 + 5배 깊이        0.814     0.760    0.714    0.600
    항 단위 + 합산1 + 5배 깊이        0.860     0.840    0.786    0.600
    **항 단위 + 합산1 + 깊이 1배**    **0.884**  **0.920** 0.786    0.800

세 가지가 기각됐다. ① 멤버 점수 **합산**(member_cap≥2)은 그룹 키가 있는
청크(법령·분류표)만 점수를 부풀려 prose 근거를 밀어낸다. ② **깊이 늘리기**
(overfetch)는 두 팔이 중위권에서 겹친 후보가 한 팔 1위의 진짜 답을 이기게
한다 — candidate_limit을 20으로 올리면 안 되는 이유(config.py)와 같은 현상.
③ **조 단위** 접기는 항을 묻는 질문에 다른 항을 대표로 내민다 (statute
0.786→0.714). 살아남은 것은 가장 소박한 형태다: 자연 깊이 안에서, 같은
주소의 사본·형제를 접어 겹친 슬롯만 해방한다. 그것만으로 crossref가
0.880→0.920 — 하루 종일 모든 단계에서 움직이지 않던 그룹이 움직였다.

그룹 키는 색인이 이미 저장한 것을 재사용한다 — 새 컬럼도 재색인도 없다:

    hierarchical           → 문서 + chunk_metadata.path 전체      ("doc#p:조36/항1")
    classification_table   → 문서 + section (= 분류표 마커)        ("doc#s:[제43류/S120602]")
    그 외 (prose/semantic) → 청크 자신 (접히지 않음)

키가 문서 범위인 것이 평행 조문 안전장치다: 특허법 제105조와 실용신안법
제27조는 문면이 동일해도 다른 문서라 절대 합쳐지지 않는다 (측정: 법령 간
정규화-완전일치 클러스터 4건 — 전부 합치면 안 되는 종류였다).

이 모듈에 I/O는 없다. 순수 함수이고 tests/test_collapse.py가 잡는다.
"""

from collections import defaultdict
from typing import NamedTuple

# 한 순위 목록 안에서 그룹이 합산할 수 있는 멤버 수. 1 = 접기만 하고 표를
# 합치지 않는다. 위 표가 그 이유다 — 2는 그룹 있는 청크만 부풀린다.
# 스윕은 scripts/eval_retrieval.py --collapse on:CAP:OVERFETCH:jo|hang 로 돈다.
MEMBER_CAP = 1


class GroupInfo(NamedTuple):
    """`group_key`가 읽는, 청크 한 개의 색인 시점 사실."""

    document_id: str
    strategy: str | None  # chunk_metadata->>'strategy'
    path: str | None  # chunk_metadata->>'path', 예: "조36/항1"
    section: str | None


def group_key(chunk_id: str, info: GroupInfo | None) -> str:
    """청크가 속한 융합 그룹의 키. 그룹이 없으면 청크 자신.

    경로 **전체**(항까지)로 접는다. 조 단위로 접으면 항을 묻는 질문에 같은
    조의 다른 항이 대표로 나가서 statute 그룹이 0.786→0.714로 떨어졌다
    (모듈 도크스트링의 표). 같은 키에 남는 것은 한 항이 크기 때문에 여러
    조각으로 잘린 경우와, 같은 항을 여는 사본뿐이다.
    """
    if info is None:
        return chunk_id
    if info.strategy == "hierarchical" and info.path:
        return f"{info.document_id}#p:{info.path}"
    if info.strategy == "classification_table" and info.section:
        return f"{info.document_id}#s:{info.section}"
    return chunk_id


class FusedGroup(NamedTuple):
    chunk_id: str  # 대표 청크 — 어느 순위 목록에서든 단일 기여가 가장 큰 멤버
    score: float
    members: tuple[str, ...]  # 순위 목록 어딘가에 나타난 이 그룹의 청크들


def fuse_groups(
    rankings: list[list[str]],
    *,
    k: int,
    weights: list[float] | None = None,
    keys: dict[str, str] | None = None,
    member_cap: int = MEMBER_CAP,
) -> list[FusedGroup]:
    """RRF를 그룹 위에서 계산한다.

    한 순위 목록 안에서 그룹의 기여는 상위 `member_cap`개 멤버의 w/(k+rank)
    합이다. 배포값은 1 — 접기만 하고 합산하지 않는다. 2 이상은 그룹 키가
    있는 청크만 점수를 부풀려 prose 근거를 밀어낸다는 것이 측정 결과다
    (모듈 도크스트링의 표). 파라미터로 남는 이유는 하네스가 스윕하기 위해서다.

    `keys`가 모든 id를 자기 자신으로 보내면 (또는 None이면) member_cap과
    무관하게 reciprocal_rank_fusion과 같은 순서·같은 점수를 낸다 — 붕괴를
    끄는 것이 곧 항등이라는 성질이고, 테스트가 그것을 고정한다.

    타이브레이크는 rrf.py와 같은 이유로 그룹의 첫 등장 순서를 명시적으로 쓴다.
    """
    if k < 0:
        raise ValueError(f"rrf k must be >= 0, got {k}")
    if member_cap < 1:
        raise ValueError(f"member_cap must be >= 1, got {member_cap}")
    if weights is None:
        weights = [1.0] * len(rankings)
    elif len(weights) != len(rankings):
        raise ValueError(f"got {len(weights)} weights for {len(rankings)} rankings")
    elif any(weight < 0 for weight in weights):
        raise ValueError(f"rrf weights must be >= 0, got {weights}")

    def key_of(chunk_id: str) -> str:
        return keys.get(chunk_id, chunk_id) if keys is not None else chunk_id

    scores: dict[str, float] = defaultdict(float)
    group_seen: dict[str, int] = {}
    members: dict[str, dict[str, None]] = defaultdict(dict)  # 삽입 순서 보존 집합
    # 그룹 → (-최대 단일 기여, 그 멤버의 등장 순번, 멤버 id). 대표 선정용.
    best_single: dict[str, tuple[float, int, str]] = {}
    order = 0

    for ranking, weight in zip(rankings, weights, strict=True):
        taken: dict[str, int] = defaultdict(int)  # 이 목록에서 그룹이 이미 합산한 멤버 수
        for position, item_id in enumerate(dict.fromkeys(ranking), start=1):
            group = key_of(item_id)
            contribution = weight / (k + position)
            group_seen.setdefault(group, len(group_seen))
            if item_id not in members[group]:
                members[group][item_id] = None
                order += 1
            # 목록은 위에서 아래로 훑으므로 같은 그룹의 멤버 기여는 내림차순 —
            # 앞의 member_cap개가 곧 상위 member_cap개다.
            if taken[group] < member_cap:
                scores[group] += contribution
                taken[group] += 1
            held = best_single.get(group)
            candidate = (-contribution, order, item_id)
            if held is None or candidate < held:
                best_single[group] = candidate

    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], group_seen[pair[0]]))
    return [
        FusedGroup(chunk_id=best_single[group][2], score=score, members=tuple(members[group]))
        for group, score in ordered
    ]
