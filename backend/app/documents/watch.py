"""감시 폴더 색인 - 서버 디렉터리의 문서를 훑어 등록하고 처리 큐에 넣는다.

계약:
- 파일은 복사하지 않는다. Document.storage_path가 원본 경로를 가리킨다(파이프라인·다운로드가 그대로 읽는다).
  문서를 지워도 원본 파일은 남는다(delete_document_files는 uploads/<id>만 지운다).
- 하위 디렉터리는 대상 분류의 폴더 트리로 비춘다(같은 이름은 재사용).
- (크기, mtime)이 장부와 같으면 건너뛴다. 바뀐 파일만 해시하고, 같은 sha256의 현행 문서가 있으면 duplicate.
- 한 번의 스캔이 등록하는 파일 수에 상한을 둔다(다음 스캔이 이어간다) - 2만 권을 한 잡에서 다 등록하면
  잡 하나가 시간을 독점하고 실패 시 되감기 단위가 너무 크다.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from anyio import to_thread
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import log_event
from app.documents.validation import ALLOWED_EXTENSIONS, extension_of
from app.models.collection import Collection
from app.models.document import Document
from app.models.folder import MAX_FOLDER_DEPTH, Folder
from app.models.ingest_file import IngestFile
from app.models.user import User

logger = logging.getLogger("mopan.ingest")

SCAN_REGISTER_LIMIT = 500
SKIP_PREFIXES = (".", "~$")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(1 << 20), b""):
            h.update(piece)
    return h.hexdigest()


async def _system_admin(db: AsyncSession) -> uuid.UUID | None:
    return await db.scalar(
        select(User.id).where(User.role == "admin", User.is_active.is_(True)).order_by(User.created_at).limit(1)
    )


async def ensure_collection(db: AsyncSession, name: str, admin_id: uuid.UUID) -> Collection:
    coll = await db.scalar(select(Collection).where(Collection.name == name))
    if coll is None:
        coll = Collection(name=name, description="감시 폴더에서 자동 등록된 문서", chunking={}, created_by=admin_id)
        db.add(coll)
        await db.flush()
    return coll


async def ensure_folder_path(db: AsyncSession, collection_id: uuid.UUID, parts: list[str], admin_id: uuid.UUID, cache: dict) -> uuid.UUID | None:
    """상대 경로의 디렉터리 조각들을 폴더 트리로. 이미 있으면 재사용. 깊이 상한을 넘는 조각은 마지막 폴더에 합친다."""
    parent: uuid.UUID | None = None
    parent_path = "/"
    for depth, name in enumerate(parts[:MAX_FOLDER_DEPTH], start=1):
        key = (parent, name)
        if key in cache:
            folder = cache[key]
        else:
            q = select(Folder).where(Folder.collection_id == collection_id, Folder.name == name)
            q = q.where(Folder.parent_id.is_(None)) if parent is None else q.where(Folder.parent_id == parent)
            folder = await db.scalar(q)
            if folder is None:
                folder = Folder(id=uuid.uuid4(), collection_id=collection_id, parent_id=parent, name=name, depth=depth, created_by=admin_id, path="")
                folder.path = f"{parent_path}{folder.id}/"
                db.add(folder)
                await db.flush()
            cache[key] = folder
        parent, parent_path = folder.id, folder.path
    return parent


def _walk(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(SKIP_PREFIXES)]
        for name in filenames:
            if name.startswith(SKIP_PREFIXES):
                continue
            out.append(Path(dirpath) / name)
    return sorted(out)


async def scan_watch_dir(db: AsyncSession, settings: Settings, enqueue) -> dict:
    """한 번의 스캔. `enqueue(document_id)`는 처리 큐에 넣는 코루틴. 결과 요약 dict."""
    summary = {"scanned": 0, "registered": 0, "duplicate": 0, "unsupported": 0, "unchanged": 0, "missing": 0, "failed": 0, "limited": False}
    root_raw = settings.ingest_watch_dir.strip()
    if not root_raw:
        return summary
    root = Path(root_raw).expanduser().resolve()
    if not root.is_dir():
        summary["error"] = f"감시 폴더가 없습니다: {root}"
        return summary
    admin_id = await _system_admin(db)
    if admin_id is None:
        summary["error"] = "관리자 계정이 없어 등록할 수 없습니다."
        return summary
    collection = await ensure_collection(db, settings.ingest_watch_collection, admin_id)
    ledger = {row.path: row for row in (await db.scalars(select(IngestFile))).all()}
    files = await to_thread.run_sync(_walk, root)
    seen: set[str] = set()
    folder_cache: dict = {}
    registered = 0
    for path in files:
        key = str(path)
        seen.add(key)
        summary["scanned"] += 1
        try:
            st = path.stat()
        except OSError:
            continue
        row = ledger.get(key)
        if row is not None and row.size_bytes == st.st_size and abs(row.mtime - st.st_mtime) < 1e-6 and row.status != "failed":
            summary["unchanged"] += 1
            continue
        if registered >= SCAN_REGISTER_LIMIT:
            summary["limited"] = True
            break
        extension = extension_of(path.name)
        if row is None:
            row = IngestFile(path=key, size_bytes=st.st_size, mtime=st.st_mtime, status="unsupported")
            db.add(row)
            ledger[key] = row
        row.size_bytes, row.mtime, row.scanned_at, row.error = st.st_size, st.st_mtime, datetime.now(UTC), None
        if extension not in ALLOWED_EXTENSIONS:
            row.status = "unsupported"
            summary["unsupported"] += 1
            continue
        try:
            digest = await to_thread.run_sync(_sha256, key)
        except OSError as exc:
            row.status, row.error = "failed", str(exc)[:500]
            summary["failed"] += 1
            continue
        row.sha256 = digest
        duplicate = await db.scalar(
            select(Document.id).where(Document.sha256 == digest, Document.is_current.is_(True)).limit(1)
        )
        if duplicate is not None:
            row.status, row.document_id = "duplicate", duplicate
            summary["duplicate"] += 1
            continue
        rel_parts = list(path.relative_to(root).parts[:-1])
        folder_id = await ensure_folder_path(db, collection.id, rel_parts, admin_id, folder_cache) if rel_parts else None
        document = Document(
            collection_id=collection.id, folder_id=folder_id, filename=path.name[:500], file_type=extension,
            size_bytes=st.st_size, storage_path=key, status="uploaded", uploaded_by=admin_id, sha256=digest,
        )
        db.add(document)
        await db.flush()
        row.status, row.document_id = "registered", document.id
        registered += 1
        summary["registered"] += 1
        await db.commit()
        try:
            await enqueue(str(document.id))
        except Exception:
            logger.exception("watch ingest enqueue failed")
            document.status, document.error_message = "failed", "작업 대기열에 넣지 못했습니다."
            row.status, row.error = "failed", "enqueue"
            await db.commit()
    # 사라진 파일: 장부만 표시한다. 문서·청크는 지우지 않는다(사람이 탐색기에서 판단).
    for key, row in ledger.items():
        if key not in seen and row.status != "missing" and key.startswith(str(root)):
            row.status = "missing"
            summary["missing"] += 1
    await db.commit()
    log_event(logger, "watch_dir_scanned", **{k: v for k, v in summary.items() if k != "error"})
    return summary


async def ingest_status(db: AsyncSession, settings: Settings) -> dict:
    counts = dict((await db.execute(select(IngestFile.status, func.count()).group_by(IngestFile.status))).all())
    last = await db.scalar(select(func.max(IngestFile.scanned_at)))
    doc_states = {}
    if counts.get("registered"):
        doc_states = dict(
            (await db.execute(
                select(Document.status, func.count()).join(IngestFile, IngestFile.document_id == Document.id).group_by(Document.status)
            )).all()
        )
    return {
        "watch_dir": settings.ingest_watch_dir or None,
        "collection": settings.ingest_watch_collection,
        "interval_minutes": settings.ingest_scan_interval_minutes,
        "counts": counts,
        "document_states": doc_states,
        "last_scanned_at": last,
    }
