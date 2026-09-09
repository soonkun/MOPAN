"""관리자용 모델 레지스트리 API - 고급 설정 > 모델 화면의 뒷면."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.llm.catalog import discover_local_models, load_catalog, upsert_row
from app.models.user import User

import logging

logger = logging.getLogger("mopan.models")
router = APIRouter(prefix="/api/admin/models", tags=["models"])


class AdminModelResponse(BaseModel):
    id: str
    label: str
    provider: str
    enabled: bool
    is_default: bool
    supports_vision: bool
    supports_reasoning: bool
    from_env: bool


class AdminModelUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    is_default: bool | None = None
    supports_vision: bool | None = None
    supports_reasoning: bool | None = None


class AdminModelCreate(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    provider: str = Field(default="openai", pattern="^(openai|local)$")
    label: str | None = Field(default=None, max_length=200)


class RefreshResponse(BaseModel):
    added: list[str]
    local_base_url: str


def _to_response(m) -> AdminModelResponse:
    return AdminModelResponse(
        id=m.id, label=m.label, provider=m.provider, enabled=m.enabled, is_default=m.is_default,
        supports_vision=m.supports_vision, supports_reasoning=m.supports_reasoning, from_env=m.from_env,
    )


@router.get("", response_model=list[AdminModelResponse])
async def list_all_models(
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session),
):
    catalog = await load_catalog(db, settings)
    return [_to_response(m) for m in catalog.models]


@router.post("", response_model=AdminModelResponse, status_code=201)
async def add_model(
    payload: AdminModelCreate,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session),
):
    """이름을 직접 적어 추가한다(OpenAI의 새 모델, 발견되지 않은 로컬 모델). 허가는 꺼진 채 시작."""
    if (await load_catalog(db, settings)).get(payload.id) is not None:
        raise HTTPException(status_code=409, detail="이미 있는 모델입니다.")
    await upsert_row(db, payload.id, provider=payload.provider, label=payload.label, enabled=False)
    catalog = await load_catalog(db, settings)
    return _to_response(catalog.get(payload.id))


@router.patch("/{model_id}", response_model=AdminModelResponse)
async def update_model(
    model_id: str,
    payload: AdminModelUpdate,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session),
):
    catalog = await load_catalog(db, settings)
    current = catalog.get(model_id)
    if current is None:
        raise HTTPException(status_code=404, detail="모델을 찾을 수 없습니다.")
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="바꿀 값이 없습니다.")
    # 기본 모델은 허가된 모델이어야 하고, 기본 모델의 허가를 끄면 기본이 다른 모델로 넘어간다.
    if fields.get("is_default") and not fields.get("enabled", current.enabled):
        raise HTTPException(status_code=400, detail="허가되지 않은 모델은 기본 모델이 될 수 없습니다.")
    if fields.get("is_default") is False and current.is_default:
        raise HTTPException(status_code=400, detail="기본 모델을 해제하려면 다른 모델을 기본으로 지정하세요.")
    if fields.get("enabled") is False and current.is_default:
        fields["is_default"] = False
    if fields.get("enabled") is False:
        # 마지막 허가 모델은 끌 수 없다 - 사용자가 고를 것이 없어진다.
        if len(catalog.enabled) <= 1 and current.enabled:
            raise HTTPException(status_code=400, detail="허가된 모델이 하나뿐이라 끌 수 없습니다.")
    await upsert_row(db, model_id, provider=current.provider, **fields)
    log_event(logger, "model_registry_changed", model=model_id, admin_id=str(admin.id), **fields)
    catalog = await load_catalog(db, settings)
    return _to_response(catalog.get(model_id))


class EmbeddingProfileResponse(BaseModel):
    key: str
    rank: int
    label: str
    provider: str
    model: str
    dim: int
    origin: str
    badges: list[str]
    note: str
    current: bool
    local_present: bool | None  # 로컬 프로필만. None = Ollama 응답 없음/해당 없음


class EmbeddingStatus(BaseModel):
    profile: str | None
    provider: str
    model: str
    dim: int
    chunk_count: int
    switch_command: str
    profiles: list[EmbeddingProfileResponse]


@router.get("/embedding", response_model=EmbeddingStatus)
async def embedding_status(
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session),
):
    """임베딩 프로필 카드. 바꾸는 버튼은 없다 - 전환은 재임베딩을 동반하는 배포 절차다."""
    from sqlalchemy import func, select

    from app.llm.embedding_profiles import current_profile_key, local_model_present, ordered_profiles
    from app.models.chunk import Chunk

    current = settings.embedding_profile or current_profile_key(settings.embedding_provider, settings.embedding_model)
    count = await db.scalar(select(func.count(Chunk.id)).where(Chunk.embedding.is_not(None))) or 0
    items = []
    for p in ordered_profiles():
        present = None
        if p.provider == "local" and settings.local_llm_base_url:
            present = await local_model_present(settings.local_llm_base_url, p.model, timeout=2.0)
        items.append(EmbeddingProfileResponse(
            key=p.key, rank=p.rank, label=p.label, provider=p.provider, model=p.model, dim=p.dim, origin=p.origin,
            badges=list(p.badges), note=p.note, current=(p.key == current), local_present=present,
        ))
    return EmbeddingStatus(
        profile=current, provider=settings.embedding_provider, model=settings.embedding_model, dim=settings.embedding_dim,
        chunk_count=count, switch_command="python scripts/reembed.py --profile <key> --apply  →  .env EMBEDDING_PROFILE=<key>  →  재시작",
        profiles=items,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_local_models(
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session),
):
    if not settings.local_llm_base_url:
        raise HTTPException(status_code=400, detail="LOCAL_LLM_BASE_URL이 설정되지 않았습니다(.env).")
    added = await discover_local_models(db, settings)
    return RefreshResponse(added=added, local_base_url=settings.local_llm_base_url)
