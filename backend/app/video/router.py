"""영상 내용 추출 - 유튜브 영상·재생목록·채널을 RAG용 문서(Word/PDF/Markdown)로 정리하는 화면의 백엔드.

일은 soonkun/youtube_transcipt 서비스(VIDEO_EXTRACT_URL)가 한다. yt-dlp·Whisper·화면 읽기를 제 venv와
제 작업 줄로 돌리는 별도 프로세스이고, 여기는 로그인한 사용자의 요청을 그 서비스로 넘기는 얇은 문이다.
넘길 때 사용자 id를 owner로 붙인다 - 작업은 넣은 사람에게만 보이고, 남의 작업은 그 서비스가 404로 답한다.
"""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user
from app.core.config import Settings, get_app_settings
from app.models.user import User

router = APIRouter(prefix="/api/video", tags=["video"])

# 테스트가 가짜 서비스를 끼우는 자리(httpx.MockTransport). 운영에서는 None = 실제 연결.
transport: httpx.AsyncBaseTransport | None = None

UNAVAILABLE = "영상 내용 추출 서비스에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요."


class NewVideoJob(BaseModel):
    # 주소 하나(영상·재생목록·채널) 또는 여러 개(쉼표·줄바꿈 구분). 유튜브 주소인지는 그 서비스가 검사한다.
    url: str = Field(min_length=1, max_length=60_000)
    force: bool = False
    # 요약을 몇 자 내외로 쓸지. 범위는 그 서비스의 것과 같다(yt2doc.CHARS_MIN/MAX).
    summary_chars: int = Field(default=500, ge=100, le=1500)
    # 요약 뒤에 영상 전체 내용(시간대별로 교정한 자막)도 넣을지.
    full: bool = False
    # 동영상 링크 앞에 썸네일(Word에는 그림, Markdown에는 그림 주소)을 넣을지.
    thumbnail: bool = True


async def _call(settings: Settings, method: str, path: str, **kwargs) -> httpx.Response:
    if not settings.video_extract_url:
        raise HTTPException(status_code=503, detail="영상 내용 추출이 이 서버에 설정되어 있지 않습니다.")
    try:
        # owner를 붙여 부를 자격 증명. 그 서비스는 이 값 없이 온 owner 요청을 403으로 거절한다.
        headers = {"X-Owner-Token": settings.video_extract_token}
        async with httpx.AsyncClient(
            base_url=settings.video_extract_url, timeout=30.0, transport=transport, headers=headers
        ) as client:
            response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=UNAVAILABLE) from exc
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if response.status_code == 404:
            detail = "작업을 찾을 수 없습니다."
        raise HTTPException(status_code=response.status_code, detail=detail or UNAVAILABLE)
    return response


@router.get("/jobs")
async def list_jobs(
    user: User = Depends(get_current_user), settings: Settings = Depends(get_app_settings)
) -> dict:
    return (await _call(settings, "GET", "/api/jobs", params={"owner": str(user.id)})).json()


@router.post("/jobs")
async def add_job(
    body: NewVideoJob, user: User = Depends(get_current_user), settings: Settings = Depends(get_app_settings)
) -> dict:
    payload = {"url": body.url, "force": body.force, "summary_chars": body.summary_chars, "full": body.full,
               "thumbnail": body.thumbnail,
               "owner": str(user.id)}
    return (await _call(settings, "POST", "/api/jobs", json=payload)).json()


@router.get("/jobs/{job_id}/{name}")
async def download(
    job_id: str = Path(pattern=r"^[0-9-]{1,40}$"),
    name: str = Path(pattern=r"^report\.(docx|md|pdf)$"),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    upstream = await _call(settings, "GET", f"/api/jobs/{job_id}/{name}", params={"owner": str(user.id)})
    headers = {k: upstream.headers[k] for k in ("content-disposition",) if k in upstream.headers}
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "application/octet-stream"),
        headers=headers,
    )
