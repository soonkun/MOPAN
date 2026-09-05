"""Runtime-editable settings, read through an indirection with `.env` as the
fallback - the same shape `app/chat/prompt.py:get_prompt` gives the answer
template.

Two rules hold this together and both are tested:

1.  **An empty `app_settings` table behaves exactly like today.** Every value
    falls back to the `Settings` the process booted with, which is the `.env`
    value. Nothing here seeds a row, and every failure path returns the base
    settings unchanged.
2.  **Only the keys in `RUNTIME_SAFE_SETTINGS` exist.** The admin API reads and
    writes nothing else, so a secret is not "hidden" from the screen - it has no
    entry, which is why `OPENAI_API_KEY` can be neither read nor written through
    it however the request is spelled.
"""

import logging
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import MAX_EXTRA_QUERIES, Settings

logger = logging.getLogger("mopan.settings")

RETRIEVAL = "retrieval"
CHUNKING = "chunking"

# Applies to the WHOLE chunking group and is repeated on screen, because an admin
# who raises CHUNK_SIZE and expects the corpus to re-chunk will be wrong for a
# long time before anything tells them so.
CHUNKING_SCOPE_NOTE = (
    "이 값은 앞으로 등록되는 문서에만 적용됩니다. 이미 색인된 문서는 다시 등록해야 바뀝니다."
)


@dataclass(frozen=True)
class SettingSpec:
    """One runtime-safe `.env` value.

    `minimum`/`maximum` duplicate part of `Settings._finalise` on purpose. The
    write path runs the real pydantic validators as well (see
    `validated_settings`), but the read path must not: it runs on every request,
    and constructing a `Settings` re-reads the `.env` FILE each time. So the
    bounds are stated here in a form both paths can afford, and the write path
    adds the cross-field checks on top.
    """

    key: str
    field: str
    kind: type[int] | type[float]
    minimum: float
    maximum: float
    group: str
    label: str
    help: str

    def parse(self, raw: str) -> int | float:
        try:
            value = self.kind(raw)
        except (TypeError, ValueError) as exc:
            expected = "정수" if self.kind is int else "숫자"
            raise ValueError(f"{self.key} 값은 {expected}여야 합니다.") from exc
        if not self.minimum <= value <= self.maximum:
            raise ValueError(
                f"{self.key} 값은 {_number(self.minimum)}에서 {_number(self.maximum)} 사이여야 합니다."
            )
        return value


def _number(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


RUNTIME_SAFE_SETTINGS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        SettingSpec(
            key="RETRIEVAL_TOP_N",
            field="retrieval_top_n",
            kind=int,
            minimum=1,
            maximum=50,
            group=RETRIEVAL,
            label="답변에 사용할 근거 수",
            help=(
                "검색 결과 중 상위 몇 개를 모델에게 넘길지 정합니다. "
                "늘리면 근거가 많아지지만 토큰 예산을 더 빨리 소진합니다."
            ),
        ),
        SettingSpec(
            key="RETRIEVAL_CANDIDATE_LIMIT",
            field="retrieval_candidate_limit",
            kind=int,
            minimum=1,
            maximum=200,
            group=RETRIEVAL,
            label="후보 검색 개수",
            help=(
                "벡터 검색과 키워드 검색이 각각 가져오는 후보 수입니다. 두 결과를 RRF로 합친 뒤 "
                "그중 상위 '답변에 사용할 근거 수'만 답변에 쓰이므로, 이 값은 그 순위 경쟁에 "
                "참여하는 후보의 범위입니다. 환경변수 RERANK_MODEL을 설정한 경우에만 재순위 "
                "모델이 이 후보 전체를 다시 정렬하며, 기본값은 재순위 없음입니다."
            ),
        ),
        SettingSpec(
            key="QUERY_EXPANSION_COUNT",
            field="query_expansion_count",
            kind=int,
            minimum=0,
            maximum=MAX_EXTRA_QUERIES,
            group=RETRIEVAL,
            label="질문 다시 쓰기 개수 (재시도용)",
            help=(
                "첫 검색이 근거를 제대로 못 찾았을 때만, 질문 하나를 검색용 질문 N개로 더 바꿔서 "
                "다시 검색합니다. 모든 질문이 아니라 실패한 질문만 이 비용을 냅니다. 0이면 이 "
                "단계는 실행되지 않고 비용도 0입니다. 문서가 쓰는 단어와 사용자가 쓰는 단어가 "
                "다를 때(어플/애플리케이션 소프트웨어) 그리고 한 질문이 여러 가지를 물을 때 "
                "효과가 있습니다. 재시도가 걸린 질문 하나당 값싼 completion 한 번이 붙어 "
                "약 4~10초와 $0.0002 정도가 더 듭니다."
            ),
        ),
        SettingSpec(
            key="RRF_K",
            field="rrf_k",
            kind=int,
            minimum=0,
            maximum=1000,
            group=RETRIEVAL,
            label="RRF 상수 (k)",
            help=(
                "두 검색 결과를 합칠 때 쓰는 상수입니다. 값이 작을수록 각 목록의 1위가 "
                "더 강하게 반영됩니다. 기본값은 60입니다."
            ),
        ),
        SettingSpec(
            key="SPARSE_WEIGHT",
            field="sparse_weight",
            kind=float,
            minimum=0.0,
            maximum=10.0,
            group=RETRIEVAL,
            label="키워드 검색 가중치",
            help="키워드(FTS) 검색 결과의 비중입니다. 0으로 두면 벡터 검색만 사용합니다.",
        ),
        SettingSpec(
            key="ANSWER_CONTEXT_TOKEN_BUDGET",
            field="answer_context_token_budget",
            kind=int,
            minimum=1,
            maximum=200_000,
            group=RETRIEVAL,
            label="답변 컨텍스트 토큰 예산",
            help=(
                "근거와 대화 이력에 쓸 수 있는 전체 토큰 상한입니다. 이 예산을 넘긴 근거는 "
                "모델에게 전달되지 않으며, 어떤 근거가 잘렸는지는 각 답변의 추적 화면에서 "
                "볼 수 있습니다."
            ),
        ),
        SettingSpec(
            key="CHUNK_SIZE",
            field="chunk_size",
            kind=int,
            minimum=100,
            maximum=20_000,
            group=CHUNKING,
            label="청크 크기 (문자)",
            help=f"문서를 나눌 때의 목표 길이입니다. {CHUNKING_SCOPE_NOTE}",
        ),
        SettingSpec(
            key="CHUNK_OVERLAP",
            field="chunk_overlap",
            kind=int,
            minimum=0,
            maximum=19_999,
            group=CHUNKING,
            label="청크 겹침 (문자)",
            help=f"인접한 청크가 겹치는 길이입니다. 청크 크기보다 작아야 합니다. {CHUNKING_SCOPE_NOTE}",
        ),
        SettingSpec(
            key="MAX_CHUNK_TOKENS",
            field="max_chunk_tokens",
            kind=int,
            minimum=1,
            maximum=4095,
            group=CHUNKING,
            label="청크 최대 토큰",
            help=(
                "한 청크가 넘을 수 없는 토큰 수입니다. 임베딩 모델의 입력 한도 때문에 "
                f"4095가 상한입니다. {CHUNKING_SCOPE_NOTE}"
            ),
        ),
        SettingSpec(
            key="SEMANTIC_SIMILARITY_THRESHOLD",
            field="semantic_similarity_threshold",
            kind=float,
            minimum=-1.0,
            maximum=1.0,
            group=CHUNKING,
            label="의미 병합 임계값",
            help=(
                "인접한 청크를 합칠지 판단하는 코사인 유사도 기준입니다. 낮출수록 청크가 "
                f"커집니다. {CHUNKING_SCOPE_NOTE}"
            ),
        ),
    )
}


@dataclass(frozen=True)
class EnvOnlySetting:
    """A value that looks like it belongs on this screen and does not.

    It lives beside the editable specs rather than in the frontend so that the
    reason travels with the decision: a later contributor who wants to make one
    of these editable reads why here, at the point they would have to delete it.
    """

    key: str
    label: str
    reason: str


ENV_ONLY_SETTINGS: list[EnvOnlySetting] = [
    EnvOnlySetting(
        key="EMBEDDING_MODEL",
        label="임베딩 모델",
        reason=(
            "이 값을 바꾸면 이미 저장된 모든 임베딩과 새 질문의 임베딩이 서로 다른 공간에 놓여 검색이 "
            "조용히 무의미해집니다. 전체 문서를 다시 색인해야 하므로 환경변수로만 바꿉니다."
        ),
    ),
    EnvOnlySetting(
        key="NEIGHBOR_EXPANSION",
        label="인접 청크 확장",
        reason=(
            "off / targeted / blanket 중 하나를 고르는 값이라 이 화면의 숫자 입력 칸으로는 다룰 수 "
            "없습니다. 답변에 쓰이는 근거의 분량을 바꾸므로 토큰 예산과 함께 판단해야 하며, "
            "scripts/eval_retrieval.py 로 측정한 뒤 환경변수로 바꿉니다."
        ),
    ),
    EnvOnlySetting(
        key="EMBEDDING_DIM",
        label="임베딩 차원",
        reason=(
            "chunks.embedding 컬럼의 실제 차원과 같아야 합니다. 바꾸려면 마이그레이션과 전체 재색인이 "
            "필요하고, 불일치하면 서버가 준비 상태 점검에서 기동을 거부합니다. 환경변수로만 바꿉니다."
        ),
    ),
    EnvOnlySetting(
        key="SPARSE_TOKENIZER",
        label="키워드 검색 토크나이저",
        reason=(
            "simple / bigram 중 하나를 고르는 값이라 이 화면의 숫자 입력 칸으로는 다룰 수 없습니다. "
            "저장된 색인은 청크를 쓸 때 설정되어 있던 토크나이저로 만들어져 있어서, 이 값을 바꾸면 "
            "scripts/backfill_tsv.py 로 전체를 다시 색인해야 합니다. 다시 색인하지 않으면 질문과 "
            "색인이 서로 다른 방식으로 쪼개져 키워드 검색이 아무것도 찾지 못합니다."
        ),
    ),
    EnvOnlySetting(
        key="INTENT_GATE",
        label="의도 게이트",
        reason=(
            "켜짐/꺼짐 값이라 이 화면의 숫자 입력 칸으로는 다룰 수 없습니다. 인사말 같은 대화형 "
            "발화를 검색 전에 골라내 문서 검색 없이 답하게 하는 판정 한 번(값싼 completion)이며, "
            "판정이 실패하면 항상 검색으로 강등되므로 꺼진 것과 같은 동작이 됩니다."
        ),
    ),
    EnvOnlySetting(
        key="SPARSE_DF_TRIM",
        label="흔한 토큰 잘라내기",
        reason=(
            "키워드 검색이 후보를 고르기 전에, 전체 청크의 이 비율보다 흔한 질의 토큰을 "
            "버립니다. 켜려면 먼저 scripts/build_lexeme_df.py 로 어휘 빈도표를 만들어야 하고, "
            "코퍼스가 크게 바뀌면 그 표를 다시 만들어야 하므로 "
            "scripts/eval_retrieval.py 로 측정한 뒤 환경변수로 바꿉니다. 0이면 꺼져 있습니다."
        ),
    ),
    EnvOnlySetting(
        key="RETRIEVAL_COLLAPSE",
        label="그룹 붕괴 융합",
        reason=(
            "켜짐/꺼짐 값이라 이 화면의 숫자 입력 칸으로는 다룰 수 없습니다. 같은 조문이나 같은 "
            "분류표 섹션의 청크들이 검색 순위에서 표를 나눠 갖지 않도록 그룹 단위로 융합하는 "
            "단계이며, scripts/eval_retrieval.py 로 측정한 뒤 환경변수로 바꿉니다."
        ),
    ),
    EnvOnlySetting(
        key="RETRIEVAL_RECAST",
        label="사례 질의 재작성",
        reason=(
            "켜짐/꺼짐 값이라 이 화면의 숫자 입력 칸으로는 다룰 수 없습니다. 자기 상황을 이야기로 "
            "서술한 질문을 첫 검색 전에 코퍼스의 용어로 다시 쓰는 단계이며, 검색 질문 하나마다 "
            "값싼 completion 한 번이 붙습니다. scripts/eval_retrieval.py 로 측정한 뒤 환경변수로 "
            "바꿉니다."
        ),
    ),
    EnvOnlySetting(
        key="RERANK_MODEL",
        label="재순위 모델",
        reason=(
            "모델 이름이라 이 화면의 숫자 입력 칸으로는 다룰 수 없습니다. 비워 두면 재순위 단계 자체가 "
            "실행 경로에 없으며, 그것이 현재 기본값입니다. 켜면 질문 하나마다 completion 호출이 "
            "추가되므로 scripts/eval_retrieval.py 로 측정한 뒤 환경변수로 바꿉니다."
        ),
    ),
]

_OVERRIDES_SQL = text("SELECT key, value FROM app_settings")

NOT_RUNTIME_SAFE_MESSAGE = "런타임에서 변경할 수 없는 설정입니다: {key}"
INVALID_COMBINATION_MESSAGE = (
    "설정값 조합이 올바르지 않습니다. 다른 설정과 함께 성립할 수 있는 값으로 다시 입력해 주세요."
)


async def load_overrides(session: AsyncSession) -> dict[str, str]:
    """Raw rows, unparsed. Unknown keys are dropped here rather than at the point
    of use: a key that was runtime-safe in an older build and is not any more
    must not keep applying just because its row survived."""
    rows = (await session.execute(_OVERRIDES_SQL)).all()
    return {row.key: row.value for row in rows if row.key in RUNTIME_SAFE_SETTINGS}


def apply_overrides(base: Settings, overrides: dict[str, str]) -> Settings:
    """The READ path. Per-key parse and range check, then one `model_copy`.

    A value that does not parse is DROPPED with a log rather than raising: this
    runs on the request path, and one bad row must not take answering down - the
    same rule `get_prompt` follows. The write path is what makes bad rows
    unreachable in the first place.
    """
    update: dict[str, int | float] = {}
    for key, raw in overrides.items():
        spec = RUNTIME_SAFE_SETTINGS[key]
        try:
            update[spec.field] = spec.parse(raw)
        except ValueError:
            logger.warning("ignoring unusable app_settings row", extra={"extra_fields": {"key": key}})
    # The one cross-field constraint among the runtime-safe keys, restated
    # because `model_copy` does NOT re-run validators - and `FixedChunking`
    # raises on overlap >= size, so a hand-edited pair of rows would break
    # ingestion rather than degrade it. Both are dropped together: keeping one
    # half of an invalid pair is not a repair.
    size = update.get("chunk_size", base.chunk_size)
    overlap = update.get("chunk_overlap", base.chunk_overlap)
    if not 0 <= overlap < size:
        logger.warning("ignoring CHUNK_SIZE/CHUNK_OVERLAP override pair: overlap must be < size")
        update.pop("chunk_size", None)
        update.pop("chunk_overlap", None)
    return base.model_copy(update=update) if update else base


def validated_settings(base: Settings, overrides: dict[str, str]) -> Settings:
    """The WRITE path. Runs the real pydantic validators, cross-field checks and
    all, so that nothing an admin saves can be a row `apply_overrides` has to
    drop later. Raises `ValueError` with a Korean message.

    Constructing a `Settings` re-reads the `.env` file, which is why this is not
    on the read path. Every field of `base` is passed explicitly, so the file
    cannot win over the values being validated.
    """
    update: dict[str, int | float] = {}
    for key, raw in overrides.items():
        spec = RUNTIME_SAFE_SETTINGS.get(key)
        if spec is None:
            raise ValueError(NOT_RUNTIME_SAFE_MESSAGE.format(key=key))
        update[spec.field] = spec.parse(raw)
    try:
        return Settings(**{**base.model_dump(), **update})
    except ValidationError as exc:
        # The pydantic message is English and would render verbatim in a Korean
        # UI, so it goes to the log and the user gets the sentence above.
        logger.warning("rejected settings combination", extra={"extra_fields": {"error": str(exc)}})
        raise ValueError(INVALID_COMBINATION_MESSAGE) from exc


async def effective_settings(session: AsyncSession, base: Settings) -> Settings:
    """`base` with whatever the database overrides. Every failure returns `base`.

    Used from the request path via `get_app_settings` and from the arq worker at
    the top of each job, which is what makes a chunking change apply to the next
    ingestion without a worker restart.
    """
    try:
        overrides = await load_overrides(session)
    except Exception:
        logger.exception("settings override lookup failed; using the environment values")
        return base
    return apply_overrides(base, overrides)
