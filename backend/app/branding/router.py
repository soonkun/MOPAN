"""브랜딩 - 이 배포가 화면에서 자기를 뭐라고 부르는가.

읽기는 로그인한 모두(사이드바와 새 대화 첫 화면이 그리는 값), 쓰기는
관리자다. 값의 의미는 models/branding.py가 적고 있다: NULL = 코드의 기본값.

마스코트는 행이 아니라 파일이다. UPLOAD_DIR/branding/ 아래 한 장이 전부이고,
업로드는 교체, 삭제는 기본 그림(프런트의 /mascot.png)으로 복귀다. 문서
업로드와 같은 저장 공간을 쓰므로 Docker에서는 볼륨에 남는다.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.models.branding import Branding
from app.models.user import User

logger = logging.getLogger("mopan.branding")

router = APIRouter(prefix="/api/branding", tags=["branding"])

# 이미지 한 장의 상한. 마스코트는 아이콘이지 포스터가 아니고, 2MB PNG면
# 720px 원본(기본 마스코트)의 몇 배다.
MASCOT_MAX_BYTES = 2 * 1024 * 1024
MASCOT_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
SUGGESTED_QUESTIONS_MAX = 6


class BrandingResponse(BaseModel):
    app_title: str | None = None
    tagline_primary: str | None = None
    tagline_secondary: str | None = None
    suggested_questions: list[str] = []
    # 프런트가 기본 /mascot.png 대신 업로드본을 그릴지 판단하는 한 비트.
    has_custom_mascot: bool = False

    model_config = {"from_attributes": True}


class BrandingUpdateRequest(BaseModel):
    # None = 기본값으로 되돌리기. 빈 문자열도 같은 뜻으로 접는다 - 제목을
    # 지운 관리자가 원한 것은 빈 제목이 아니라 원래 제목이다.
    app_title: str | None = Field(default=None, max_length=60)
    tagline_primary: str | None = Field(default=None, max_length=200)
    tagline_secondary: str | None = Field(default=None, max_length=300)
    suggested_questions: list[str] = Field(default_factory=list)


def _mascot_path(settings: Settings) -> Path | None:
    directory = settings.upload_dir / "branding"
    for extension in MASCOT_TYPES.values():
        candidate = directory / f"mascot{extension}"
        if candidate.exists():
            return candidate
    return None


async def _row(db: AsyncSession) -> Branding | None:
    return await db.get(Branding, True)


def _to_response(row: Branding | None, settings: Settings) -> BrandingResponse:
    return BrandingResponse(
        app_title=row.app_title if row else None,
        tagline_primary=row.tagline_primary if row else None,
        tagline_secondary=row.tagline_secondary if row else None,
        suggested_questions=list(row.suggested_questions) if row else [],
        has_custom_mascot=_mascot_path(settings) is not None,
    )


@router.get("", response_model=BrandingResponse)
async def read_branding(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    return _to_response(await _row(db), settings)


@router.put("", response_model=BrandingResponse)
async def update_branding(
    payload: BrandingUpdateRequest,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    questions = [q.strip() for q in payload.suggested_questions if q.strip()]
    if len(questions) > SUGGESTED_QUESTIONS_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"추천 질문은 {SUGGESTED_QUESTIONS_MAX}개까지입니다. 많으면 아무것도 눈에 안 띕니다.",
        )
    if any(len(q) > 200 for q in questions):
        raise HTTPException(status_code=400, detail="추천 질문은 한 줄에 200자 이하여야 합니다.")

    row = await _row(db)
    if row is None:
        row = Branding(id=True)
        db.add(row)
    row.app_title = (payload.app_title or "").strip() or None
    row.tagline_primary = (payload.tagline_primary or "").strip() or None
    row.tagline_secondary = (payload.tagline_secondary or "").strip() or None
    row.suggested_questions = questions
    await db.commit()
    await db.refresh(row)
    return _to_response(row, settings)


@router.get("/mascot")
async def read_mascot(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
):
    """업로드된 마스코트. 없으면 404 - 프런트는 기본 /mascot.png로 그린다.
    <img>는 같은 출처라 세션 쿠키가 따라오므로 인증이 그대로 선다."""
    path = _mascot_path(settings)
    if path is None:
        raise HTTPException(status_code=404, detail="업로드된 마스코트가 없습니다.")
    # 교체 직후 옛 그림이 보이지 않게. 마스코트 한 장에 캐시 무효화 체계를
    # 들일 일은 아니다.
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@router.post("/mascot", status_code=204)
async def upload_mascot(
    file: UploadFile,
    user: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
):
    extension = MASCOT_TYPES.get(file.content_type or "")
    if extension is None:
        raise HTTPException(status_code=400, detail="PNG·JPEG·WebP 이미지만 올릴 수 있습니다.")
    data = await file.read(MASCOT_MAX_BYTES + 1)
    if len(data) > MASCOT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="마스코트 이미지는 2MB 이하여야 합니다.")

    directory = settings.upload_dir / "branding"
    directory.mkdir(parents=True, exist_ok=True)
    # 확장자가 바뀌는 교체(png -> jpg)에서 옛 파일이 남아 _mascot_path가 그것을
    # 먼저 찾으면 교체가 조용히 무시된다. 전부 지우고 하나만 남긴다.
    for old_extension in MASCOT_TYPES.values():
        (directory / f"mascot{old_extension}").unlink(missing_ok=True)
    (directory / f"mascot{extension}").write_bytes(data)
    return Response(status_code=204)


@router.delete("/mascot", status_code=204)
async def delete_mascot(
    user: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
):
    for extension in MASCOT_TYPES.values():
        (settings.upload_dir / "branding" / f"mascot{extension}").unlink(missing_ok=True)
    return Response(status_code=204)
