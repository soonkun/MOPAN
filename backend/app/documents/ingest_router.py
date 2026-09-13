"""감시 폴더 관리자 API - 상태와 '지금 스캔'."""
from __future__ import annotations

from datetime import datetime

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.documents.service import get_arq_pool
from app.documents.watch import ingest_status
from app.models.user import User

router = APIRouter(prefix="/api/admin/ingest", tags=["ingest"])


class IngestStatus(BaseModel):
    watch_dir: str | None
    collection: str
    interval_minutes: int
    counts: dict[str, int]
    document_states: dict[str, int]
    last_scanned_at: datetime | None


@router.get("/status", response_model=IngestStatus)
async def status(admin: User = Depends(require_admin), settings: Settings = Depends(get_app_settings), db: AsyncSession = Depends(get_db_session)):
    return IngestStatus(**(await ingest_status(db, settings)))


@router.post("/scan", status_code=202)
async def scan_now(admin: User = Depends(require_admin), settings: Settings = Depends(get_app_settings), arq_pool: ArqRedis = Depends(get_arq_pool)):
    """스캔은 워커 잡이다 - 2만 파일 해시를 요청 안에서 하지 않는다."""
    if not settings.ingest_watch_dir:
        raise HTTPException(status_code=400, detail="INGEST_WATCH_DIR이 설정되지 않았습니다(.env).")
    from app.worker import WATCH_QUEUE

    # 스캔 전용 큐로(문서 처리 큐 뒤에 줄 서지 않게). 같은 id면 이미 큐에 있는 스캔과 합쳐진다.
    await arq_pool.enqueue_job("scan_watch_dir", _job_id="scan_watch_dir", _queue_name=WATCH_QUEUE)
    return {"queued": True}
