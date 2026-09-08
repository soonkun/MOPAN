"""답변 모델 카탈로그 - .env 목록(ANSWER_MODEL/ANSWER_MODELS) 위에 llm_models 행을 덧씌운 것.

규칙 (마이그레이션 0018의 WHY):
- .env 모델은 행이 없어도 허가된 채 나온다. 행이 있으면 enabled/is_default/플래그가 우선.
- 행만 있는 모델(로컬 GPU 모델, 관리자가 추가한 OpenAI 모델)은 행이 곧 전부다.
- 기본 모델은 is_default 행이 있고 허가돼 있으면 그것, 없으면 ANSWER_MODEL, 그것도
  허가가 꺼졌으면 허가된 첫 모델.
- 빈 표 = 지금까지의 동작. 테스트가 settings.answer_models를 바꿔 쓰는 계약이 그대로 산다.

로컬 모델은 OpenAI 호환 엔드포인트(LOCAL_LLM_BASE_URL, 예: Ollama http://127.0.0.1:11434/v1)의
/models를 읽어 행으로 넣는다(enabled=false - 관리자가 켠다). 실측(2026-09-08, Ollama):
content와 별도 `reasoning` 필드, reasoning_effort·tools 수용, 임베딩은 여기서 쓰지 않는다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import MODEL_LABELS, Settings, model_supports_reasoning
from app.models.llm_model import LlmModel

logger = logging.getLogger("mopan.models")

# 로컬 모델 기능 유추. 이름에 근거한 단언이고, 관리자가 행의 플래그로 뒤집을 수 있다.
LOCAL_VISION_HINTS = ("gemma", "llava", "vl", "vision", "pixtral", "qwen2.5vl", "qwen3-vl")
LOCAL_REASONING_HINTS = ("gpt-oss", "deepseek-r1", "qwq", "o1", "o3", "magistral")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    provider: str  # openai | local
    enabled: bool
    is_default: bool
    supports_vision: bool
    supports_reasoning: bool
    from_env: bool
    has_row: bool


@dataclass
class Catalog:
    models: list[ModelInfo]

    @property
    def enabled(self) -> list[ModelInfo]:
        return [m for m in self.models if m.enabled]

    @property
    def default(self) -> str | None:
        return self.default_id

    default_id: str | None = None

    def get(self, model_id: str) -> ModelInfo | None:
        return next((m for m in self.models if m.id == model_id), None)

    def is_allowed(self, model_id: str) -> bool:
        m = self.get(model_id)
        return m is not None and m.enabled

    def supports_vision(self, model_id: str) -> bool:
        m = self.get(model_id)
        return bool(m and m.supports_vision)

    def supports_reasoning(self, model_id: str) -> bool:
        m = self.get(model_id)
        return bool(m and m.supports_reasoning)

    @property
    def any_enabled_supports_vision(self) -> bool:
        return any(m.supports_vision for m in self.enabled)

    def apply_to_provider(self, provider) -> None:
        """프로바이더에 라우팅·기능 정보를 심는다(값 대입뿐, 요청마다 싸다)."""
        provider.local_model_names = {m.id for m in self.models if m.provider == "local"}
        provider.reasoning_model_names = {m.id for m in self.models if m.supports_reasoning}


def _guess_vision(model_id: str, provider: str, settings: Settings) -> bool:
    if provider == "local":
        return any(h in model_id.lower() for h in LOCAL_VISION_HINTS)
    return settings.model_supports_vision(model_id)


def _guess_reasoning(model_id: str, provider: str) -> bool:
    if provider == "local":
        return any(h in model_id.lower() for h in LOCAL_REASONING_HINTS)
    return model_supports_reasoning(model_id)


def _info(model_id: str, row: LlmModel | None, *, from_env: bool, settings: Settings) -> ModelInfo:
    provider = row.provider if row else "openai"
    return ModelInfo(
        id=model_id,
        label=(row.label if row and row.label else MODEL_LABELS.get(model_id, model_id)),
        provider=provider,
        enabled=row.enabled if row else True,
        is_default=bool(row and row.is_default),
        supports_vision=(
            row.supports_vision if row and row.supports_vision is not None
            else _guess_vision(model_id, provider, settings)
        ),
        supports_reasoning=(
            row.supports_reasoning if row and row.supports_reasoning is not None
            else _guess_reasoning(model_id, provider)
        ),
        from_env=from_env,
        has_row=row is not None,
    )


async def load_catalog(db: AsyncSession, settings: Settings) -> Catalog:
    rows = {r.id: r for r in (await db.scalars(select(LlmModel).order_by(LlmModel.sort_order, LlmModel.id))).all()}
    models: list[ModelInfo] = []
    for model_id in settings.selectable_models:
        models.append(_info(model_id, rows.pop(model_id, None), from_env=True, settings=settings))
    for row in rows.values():
        models.append(_info(row.id, row, from_env=False, settings=settings))
    catalog = Catalog(models=models)
    default = next((m.id for m in models if m.is_default and m.enabled), None)
    if default is None and catalog.is_allowed(settings.answer_model):
        default = settings.answer_model
    if default is None and catalog.enabled:
        default = catalog.enabled[0].id
    catalog.default_id = default
    return catalog


async def upsert_row(db: AsyncSession, model_id: str, **fields) -> LlmModel:
    """관리자 토글의 쓰기 경로. 기본 지정은 다른 행의 is_default를 먼저 내린다(부분 유니크)."""
    row = await db.get(LlmModel, model_id)
    if row is None:
        row = LlmModel(id=model_id, provider=fields.pop("provider", "openai"))
        db.add(row)
    if fields.get("is_default"):
        for other in (await db.scalars(select(LlmModel).where(LlmModel.is_default.is_(True)))).all():
            other.is_default = False
        await db.flush()
    for key, value in fields.items():
        setattr(row, key, value)
    await db.commit()
    await db.refresh(row)
    return row


async def discover_local_models(db: AsyncSession, settings: Settings, *, timeout: float = 5.0) -> list[str]:
    """LOCAL_LLM_BASE_URL의 /models를 읽어 없는 이름을 행으로 넣는다(enabled=false).
    절대 raise하지 않는다 - 로컬 서버가 죽어 있어도 부팅과 화면은 살아야 한다."""
    base = settings.local_llm_base_url.rstrip("/")
    if not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{base}/models", headers={"Authorization": f"Bearer {settings.local_llm_api_key or 'none'}"}
            )
            response.raise_for_status()
            names = [item["id"] for item in response.json().get("data", []) if item.get("id")]
    except Exception:
        logger.warning("local model discovery failed; keeping existing rows", exc_info=True)
        return []
    existing = {r.id for r in (await db.scalars(select(LlmModel))).all()}
    added = []
    for name in names:
        if name in existing or name in settings.selectable_models:
            continue
        db.add(LlmModel(id=name, provider="local", enabled=False, label=name))
        added.append(name)
    if added:
        await db.commit()
        logger.info("discovered %d local models: %s", len(added), ", ".join(added))
    return added
