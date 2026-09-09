"""감시 폴더 스캐너: 등록·폴더 트리 비추기·중복·미지원·변경 없음 건너뛰기·사라진 파일 표시."""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.config import Settings
from app.documents.watch import scan_watch_dir
from app.models.document import Document
from app.models.folder import Folder
from app.models.ingest_file import IngestFile
from app.models.user import User


@pytest_asyncio.fixture
async def admin(db):
    user = User(email="w@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.commit()
    return user


def _settings(root) -> Settings:
    return Settings(openai_api_key="x", ingest_watch_dir=str(root), ingest_watch_collection="감시")


async def test_scan_registers_mirrors_folders_and_skips_unchanged(db, admin, tmp_path):
    (tmp_path / "규정" / "인사").mkdir(parents=True)
    (tmp_path / "규정" / "인사" / "복무.txt").write_text("복무 규정 본문", encoding="utf-8")
    (tmp_path / "보고서.md").write_text("# 보고서", encoding="utf-8")
    (tmp_path / "사진.png").write_bytes(b"\x89PNG")
    (tmp_path / "사본.txt").write_text("복무 규정 본문", encoding="utf-8")  # 같은 내용 → duplicate
    enqueued = []

    async def enqueue(did):
        enqueued.append(did)

    summary = await scan_watch_dir(db, _settings(tmp_path), enqueue)
    assert summary["registered"] == 2 and summary["duplicate"] == 1 and summary["unsupported"] == 1
    assert len(enqueued) == 2
    docs = (await db.scalars(select(Document))).all()
    by_name = {d.filename: d for d in docs}
    assert by_name["복무.txt"].storage_path == str(tmp_path / "규정" / "인사" / "복무.txt")  # 복사 없음
    folders = {f.name: f for f in (await db.scalars(select(Folder))).all()}
    assert folders["인사"].parent_id == folders["규정"].id and by_name["복무.txt"].folder_id == folders["인사"].id
    assert by_name["보고서.md"].folder_id is None
    # 다시 스캔: 변경 없음
    summary = await scan_watch_dir(db, _settings(tmp_path), enqueue)
    assert summary["registered"] == 0 and summary["unchanged"] == 4 and len(enqueued) == 2
    # 파일이 사라지면 장부만 missing, 문서는 남는다
    (tmp_path / "보고서.md").unlink()
    summary = await scan_watch_dir(db, _settings(tmp_path), enqueue)
    assert summary["missing"] == 1
    ledger = {r.path: r.status for r in (await db.scalars(select(IngestFile))).all()}
    assert ledger[str(tmp_path / "보고서.md")] == "missing" and len((await db.scalars(select(Document))).all()) == 2


async def test_scan_without_dir_is_noop(db, admin):
    summary = await scan_watch_dir(db, Settings(openai_api_key="x", ingest_watch_dir=""), lambda d: None)
    assert summary["scanned"] == 0
