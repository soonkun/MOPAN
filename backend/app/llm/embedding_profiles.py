"""임베딩 프로필 - 배포 시 하나를 고르는 사전 설정(소유자 순서 2026-09-09).

임베딩은 사용자 선택지가 아니다: 문서와 질문이 같은 모델·같은 차원에 있어야 검색이 된다.
바꾸면 scripts/reembed.py --profile <key> --apply 로 전체 재임베딩(1024 프로필은 컬럼 차원 변경 동반).
로컬 모델은 미리 받아두지 않고 선택된 프로필의 모델만 기동 시 Ollama에서 내려받는다(ensure_local_embedding_model).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger("mopan.embedding")


@dataclass(frozen=True)
class EmbeddingProfile:
    key: str
    rank: int
    label: str
    provider: str  # openai | local
    model: str
    dim: int
    origin: str  # 모델 출처(제작사·국가)
    badges: tuple[str, ...]
    note: str


PROFILES: dict[str, EmbeddingProfile] = {
    p.key: p
    for p in (
        EmbeddingProfile(
            key="qwen3", rank=1, label="Qwen3-Embedding 8B", provider="local", model="qwen3-embedding:8b", dim=1536,
            origin="Alibaba(중국) · Apache-2.0", badges=("로컬 GPU", "비용 없음"),
            note="이 코퍼스 실측 1위(한국어 분별력·속도). 4096차원을 1536으로 축소해 스키마 변경 없음. 문서가 서버 밖으로 나가지 않는다.",
        ),
        EmbeddingProfile(
            key="openai", rank=2, label="OpenAI text-embedding-3-large", provider="openai", model="text-embedding-3-large", dim=1536,
            origin="OpenAI(미국)", badges=("API 비용 발생", "문서 외부 전송"),
            note="GPU가 없는 배포용. 1M 토큰당 $0.13 - 2만 권(약 20억 토큰)이면 약 $260. 문서 전문이 OpenAI로 전송된다.",
        ),
        EmbeddingProfile(
            key="bge-m3", rank=3, label="BGE-M3", provider="local", model="bge-m3", dim=1024,
            origin="BAAI(중국) · MIT", badges=("로컬 GPU", "비용 없음", "1024차원(스키마 변경)"),
            note="널리 검증된 다국어 모델. 이 코퍼스 실측에서는 qwen3보다 분별력·속도가 낮았다. 차원이 달라 전환 시 컬럼을 바꾼다.",
        ),
        EmbeddingProfile(
            key="arctic", rank=4, label="Snowflake Arctic Embed 2", provider="local", model="snowflake-arctic-embed2", dim=1024,
            origin="Snowflake(미국) · Apache-2.0", badges=("로컬 GPU", "비용 없음", "비중국", "1024차원(스키마 변경)"),
            note="기관 정책상 비중국 기원이 필요할 때. 한국어 품질은 이 코퍼스에서 아직 측정 전 - 전환 전 eval_retrieval.py로 잰다.",
        ),
    )
}


def ordered_profiles() -> list[EmbeddingProfile]:
    return sorted(PROFILES.values(), key=lambda p: p.rank)


def current_profile_key(provider: str, model: str) -> str | None:
    for p in PROFILES.values():
        if p.provider == provider and p.model == model:
            return p.key
    return None


async def local_model_present(base_url: str, model: str, timeout: float = 5.0) -> bool | None:
    """Ollama에 모델이 있는가. 서버가 안 뜨면 None(모름)."""
    root = base_url.rstrip("/")
    root = root[: -len("/v1")] if root.endswith("/v1") else root
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"{root}/api/tags")
            r.raise_for_status()
            names = {m["name"] for m in r.json().get("models", [])}
    except Exception:
        return None
    return model in names or f"{model}:latest" in names


async def ensure_local_embedding_model(base_url: str, model: str) -> str:
    """없으면 Ollama에게 내려받게 한다(POST /api/pull, 스트림 끝까지 대기). 결과 문자열은 로그용.
    절대 raise하지 않는다 - 임베딩 모델이 없으면 첫 문서 처리가 실패하며 그 사유가 문서 행에 남는다."""
    present = await local_model_present(base_url, model)
    if present is None:
        return "ollama unreachable"
    if present:
        return "present"
    root = base_url.rstrip("/")
    root = root[: -len("/v1")] if root.endswith("/v1") else root
    logger.warning("embedding model %s not found locally; pulling from Ollama registry", model)
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", f"{root}/api/pull", json={"name": model, "stream": True}) as r:
                async for _ in r.aiter_lines():
                    pass
    except Exception:
        logger.exception("embedding model pull failed")
        return "pull failed"
    return "pulled" if await local_model_present(base_url, model) else "pull incomplete"
