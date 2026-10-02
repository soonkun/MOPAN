"""음성 전사·회의록 - 회의 녹음을 받아 적고(Whisper), 회의 결과보고서 초안을 써서 한글(hwpx)로 낸다. 원본: 새싹이.

받아 적기는 soonkun/youtube_transcipt 서비스(영상 내용 추출과 같은 곳, 같은 작업 줄)가 한다 - 여기는 app/video처럼 넘기는 문.
초안은 이 백엔드의 답변 모델이 쓴다(draft.py). 수십 초 걸려 요청과 분리된 태스크로 돌리고, 화면은 짧은 GET으로 읽는다.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.chat.memory import spawn
from app.chat.prompt import get_prompt
from app.chat.router import get_llm_provider
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.redis import get_redis
from app.llm.base import LLMProvider
from app.llm.catalog import load_catalog
from app.minutes import draft, hwpx
from app.models.user import User
from app.video.router import _call

logger = logging.getLogger("mopan.minutes")
router = APIRouter(prefix="/api/minutes", tags=["minutes"])

GUIDE_PROMPT = "meeting_minutes"
MAX_TRANSCRIPT = 200_000  # 자. 3시간 회의가 6~8만 자 - 답변 모델 창(131k 토큰) 안에 통째로 들어가는 크기
DRAFT_TTL = 24 * 3600
ID = r"^[0-9a-f-]{1,40}$"


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y.%m.%d.")


@router.post("/transcriptions")
async def add_transcription(
    request: Request,
    filename: str = Query(min_length=1, max_length=200),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    """음성 파일 = 요청 본문 그대로. 받는 대로 그 서비스로 흘려 보낸다(여기 메모리에 쌓지 않는다)."""
    return (await _call(settings, "POST", "/api/transcriptions", params={"filename": filename, "owner": str(user.id)},
                        content=request.stream(), timeout=600.0)).json()


@router.get("/transcriptions/{transcription_id}")
async def get_transcription(
    transcription_id: str = Path(pattern=r"^[0-9-]{1,40}$"),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    return (await _call(settings, "GET", f"/api/transcriptions/{transcription_id}", params={"owner": str(user.id)})).json()


class NewDraft(BaseModel):
    transcript: str = Field(min_length=20, max_length=MAX_TRANSCRIPT)
    pages: Literal[1, 2] = 1


@router.post("/drafts")
async def add_draft(
    body: NewDraft,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session, scope="function"),
    redis: Redis = Depends(get_redis),
    llm: LLMProvider = Depends(get_llm_provider),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    model = (await load_catalog(db, settings)).default_id
    if not model:
        raise HTTPException(status_code=503, detail="쓸 수 있는 답변 모델이 없습니다. 관리자에게 문의해 주세요.")
    guide = (await get_prompt(GUIDE_PROMPT)).text  # 요청 안에서 읽는다 - 태스크에는 요청의 세션이 없다
    job = {"id": uuid.uuid4().hex, "status": "running", "note": "초안을 쓰는 중"}
    key = f"minutes:{user.id}:{job['id']}"

    async def save(**changes) -> None:
        job.update(changes)
        await redis.set(key, json.dumps(job, ensure_ascii=False), ex=DRAFT_TTL)

    async def run() -> None:
        try:
            result = await draft.write(llm, model, guide, body.transcript, body.pages, _today(), note=lambda text: save(note=text))
            await save(status="done", note="", **result)
        except Exception:
            logger.exception("minutes draft failed")
            await save(status="failed", note="", error="초안을 쓰지 못했습니다. 잠시 뒤 다시 시도해 주세요.")

    await save()
    spawn(run())
    return job


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: str = Path(pattern=ID), user: User = Depends(get_current_user), redis: Redis = Depends(get_redis)
) -> dict:
    raw = await redis.get(f"minutes:{user.id}:{draft_id}")  # 키에 사용자 id가 들어 있어 남의 초안은 없는 것으로 보인다
    if raw is None:
        raise HTTPException(status_code=404, detail="초안을 찾을 수 없습니다(하루가 지나면 지워집니다).")
    return json.loads(raw)


class Text(BaseModel):
    text: str = Field(min_length=1, max_length=40_000)
    transcript: str = Field(default="", max_length=MAX_TRANSCRIPT)
    pages: Literal[1, 2] = 1
    date: str = Field(default="", pattern=r"^(\d{4}\.\d{1,2}\.\d{1,2}\.)?$")
    department: str = Field(default="", max_length=40)


@router.post("/check")
async def check(body: Text, user: User = Depends(get_current_user)) -> dict:
    """고친 글을 지침으로 다시 잰다(모델을 부르지 않는다)."""
    return {"issues": draft.lint(body.text, body.transcript, body.pages, _today())}


@router.post("/hwpx")
async def download_hwpx(body: Text, user: User = Depends(get_current_user)) -> Response:
    minutes = draft.parse(body.text)
    if not minutes.title or not any(minutes.sections.values()):
        raise HTTPException(status_code=400, detail="보고서 형식을 읽지 못했습니다. 첫 줄에 제목, 그 아래 [개요]·[주요내용]·[향후계획]과 ○ 꼭지가 있어야 합니다.")
    name = quote(f"{minutes.title[:60]}.hwpx")
    return Response(
        content=hwpx.build(minutes, body.date or _today(), body.department.strip()),
        media_type="application/hwp+zip",
        headers={"Content-Disposition": f"attachment; filename=\"minutes.hwpx\"; filename*=UTF-8''{name}"},
    )
