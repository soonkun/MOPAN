"""코드 탭 API - 작업 공간 목록/생성/파일, 내 컴퓨터 연결, 그리고 OpenCode 서버로의 프록시.

프록시 라우트 `/api/code/ws/{ws_id}/oc/{path}`가 이 기능의 대부분이다: 브라우저는 OpenCode의 REST/SSE
API(세션·메시지·이벤트·권한)를 이 경로로 그대로 부르고, 여기서는 (1) 세션 쿠키로 사용자를 확인하고
(2) 작업 공간 id로 상류(서버 샌드박스 프로세스 또는 내 컴퓨터 연결)와 `directory`를 정하고
(3) 바이트를 그대로 흘린다. OpenCode API를 다시 모델링하지 않는다.

작업 공간 id: `srv:<디렉터리명>`(서버) · `loc:<base64url(PC 경로)>`(내 컴퓨터).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import secrets
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.code.bridge import Bridge, BridgeConn, BridgeError
from app.code.llm_proxy import code_models, model_limit, token_hash
from app.code.runtime import ServerRuntime
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.redis import get_redis
from app.llm.catalog import Catalog, load_catalog
from app.models.code import CodeProfile
from app.models.user import User

router = APIRouter(prefix="/api/code", tags=["code"])

COMPANION_PATH = Path(__file__).with_name("companion") / "mopan-code.mjs"
# 디렉터리 이름: 글자·숫자·밑줄(한글 포함), 하이픈·점·공백. 경로 구분자와 `..`은 여기서 걸러진다.
NAME_RE = re.compile(r"[^\W-][\w\-. ]{0,59}")
# 내려받기에서 빼는 것 - 다시 만들 수 있는 것들.
ZIP_SKIP = {"node_modules", ".venv", "__pycache__", ".git"}
HOP_HEADERS = {
    "host",
    "connection",
    "content-length",
    "transfer-encoding",
    "cookie",
    "authorization",
    "accept-encoding",
}


# 화면이 쓰지 않는 OpenCode 제어 경로는 프록시하지 않는다: 서버 종료·업그레이드·프로바이더 자격증명·콘솔·
# TUI·설정 변경. 설정 PATCH를 막는 이유: provider baseURL을 바꾸면 그 사용자의 LLM 토큰이 바깥 주소로 나간다.
PROXY_DENY_PREFIXES = (
    "global/",
    "instance/",
    "auth",
    "provider",
    "experimental/console",
    "tui/",
    "log",
    "config",
)


# --- 의존 ------------------------------------------------------------------------


def _runtime(request: Request) -> ServerRuntime:
    return request.app.state.code_runtime


def _bridge(request: Request) -> Bridge:
    return request.app.state.code_bridge


async def get_profile(db: AsyncSession, user: User, settings: Settings) -> CodeProfile:
    """없으면 만든다. uid는 base 위로 하나씩 - 유니크 제약이 경합을 잡으면 한 번 더.

    경합은 상상이 아니다: 코워크 화면이 뜨며 /status와 /cowork/tasks가 동시에 오고, 첫 방문이면 둘 다
    같은 uid를 계산한다(실사고 2026-09-26: 새 계정의 첫 /cowork가 500). 진 쪽은 IntegrityError인데,
    rollback()이 세션의 모든 인스턴스를 만료시켜 그 뒤 `user.id`를 읽는 순간 lazy refresh(IO) →
    MissingGreenlet이 되었다. 그래서 user_id는 루프 전에 값으로 떼어 두고, 재시도 전에 이긴 쪽이
    만든 행을 먼저 찾는다(같은 사용자의 두 요청이면 그 행이 정답이다)."""
    user_id = user.id
    profile = await db.get(CodeProfile, user_id)
    if profile is not None:
        return profile
    for _ in range(3):
        top = await db.scalar(select(func.max(CodeProfile.sandbox_uid)))
        profile = CodeProfile(user_id=user_id, sandbox_uid=max(top or 0, settings.code_uid_base - 1) + 1)
        db.add(profile)
        try:
            await db.commit()
            # INSERT에 안 실린 열(bridge_token_hash, created_at)은 만료 상태라 다음 접근에서 lazy load(IO)가
            # 나고 async에서는 MissingGreenlet 500이 된다(실사고 2026-09-27, 새 계정 첫 /status). 다 읽는다.
            await db.refresh(profile)
            return profile
        except IntegrityError:
            await db.rollback()
            existing = await db.get(CodeProfile, user_id)
            if existing is not None:
                return existing
    raise HTTPException(status_code=503, detail="샌드박스 uid를 배정하지 못했습니다. 다시 시도해 주세요.")


async def _models_for_opencode(
    request: Request, catalog: Catalog, settings: Settings
) -> tuple[dict[str, dict], str | None]:
    catalog.apply_to_provider(request.app.state.llm_provider)
    models = {}
    for m in code_models(catalog, settings):
        limit = await model_limit(request, settings, m.id)
        models[m.id] = {"name": m.label, "limit": limit, "tool_call": True}
    return models, catalog.default


def parse_ws_id(ws_id: str) -> tuple[str, str]:
    """('server', 이름) · ('cowork', 작업 이름) · ('local', PC 경로).

    server와 cowork는 둘 다 이 서버의 폴더다(각각 /workspace, /cowork에 바인드)."""
    kind, _, rest = ws_id.partition(":")
    if kind == "srv" and NAME_RE.fullmatch(rest) and rest not in {".", ".."}:
        return "server", rest
    if kind == "cw" and NAME_RE.fullmatch(rest) and rest not in {".", ".."}:
        return "cowork", rest
    if kind == "loc" and rest:
        try:
            return "local", base64.urlsafe_b64decode(rest + "=" * (-len(rest) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
    raise HTTPException(status_code=404, detail="작업 공간을 찾을 수 없습니다.")


def local_ws_id(path: str) -> str:
    return "loc:" + base64.urlsafe_b64encode(path.encode("utf-8")).decode().rstrip("=")


def _server_ws_root(runtime: ServerRuntime, uid: int, name: str, kind: str = "server") -> Path:
    root = (runtime.cowork_dir(uid) if kind == "cowork" else runtime.ws_dir(uid)) / name
    if root.is_symlink() or not root.is_dir():
        raise HTTPException(status_code=404, detail="작업 공간을 찾을 수 없습니다.")
    return root


def _sandbox_dir(kind: str, name: str) -> str:
    return f"/cowork/{name}" if kind == "cowork" else f"/workspace/{name}"


def _mtime_tree(root: Path) -> float:
    """폴더 안 가장 최근 수정 시각 - 빈 폴더면 폴더 자체. 링크는 따라가지 않는다."""
    latest = root.lstat().st_mtime
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                latest = max(latest, os.lstat(os.path.join(dirpath, name)).st_mtime)
            except OSError:
                pass
    return latest


def _inside(root: Path, target: Path) -> bool:
    """target(링크를 다 풀었을 때)이 root 안인가.

    샌드박스의 에이전트는 자기 uid로 어디든 가리키는 링크를 만들 수 있고, 이 프로세스는 root라 링크를 따라가면
    남의 파일을 읽고·덮어쓰고·소유권을 넘긴다(보안 리뷰 2026-09-25, /etc/hostname 덮어쓰기로 실증). 그래서 이
    모듈의 파일 I/O는 전부 링크를 따라가지 않거나 풀린 경로가 root 안인지 본다."""
    try:
        return os.path.realpath(target).startswith(os.path.realpath(root) + os.sep)
    except OSError:
        return False


def _write_file(root: Path, dest: Path, data_iter) -> None:
    """dest에 쓴다 - 링크면 거절(O_NOFOLLOW), 부모가 링크로 밖을 가리켜도 거절."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or not _inside(root, dest.parent) or not _inside(root, dest):
        raise HTTPException(status_code=400, detail="파일 경로가 올바르지 않습니다.")
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as out:
        for chunk in data_iter:
            out.write(chunk)


# --- 상태·작업 공간 ------------------------------------------------------------------


class WorkspaceOut(BaseModel):
    id: str
    kind: str  # server | local
    name: str
    path: str
    online: bool
    host: str | None = None


class BridgeStatus(BaseModel):
    connected: bool
    has_token: bool
    host: str | None = None
    version: str | None = None
    dirs: list[str] = []


class StatusOut(BaseModel):
    server_available: bool
    server_reason: str
    bridge: BridgeStatus
    models: list[dict]
    default_model: str | None


def _bridge_status(conn: BridgeConn | None, has_token: bool) -> BridgeStatus:
    if conn is None:
        return BridgeStatus(connected=False, has_token=has_token)
    return BridgeStatus(
        connected=True, has_token=has_token, host=conn.host, version=conn.version, dirs=conn.dirs
    )


@router.get("/status", response_model=StatusOut)
async def status(
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    ok, why = _runtime(request).available()
    profile = await get_profile(db, user, settings)
    catalog = await load_catalog(db, settings)
    allowed = code_models(catalog, settings)
    return StatusOut(
        server_available=ok and settings.code_enabled,
        server_reason=""
        if settings.code_enabled
        else "관리자가 코드 기능을 꺼 두었습니다."
        if not ok
        else why,
        bridge=_bridge_status(_bridge(request).get(user.id), profile.bridge_token_hash is not None),
        models=[{"id": m.id, "label": m.label} for m in allowed],
        default_model=(
            settings.code_default_model
            if settings.code_default_model in {m.id for m in allowed}
            else (allowed[0].id if allowed else None)
        ),
    )


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    profile = await get_profile(db, user, settings)
    out: list[WorkspaceOut] = []
    ws_dir = _runtime(request).ws_dir(profile.sandbox_uid)
    if ws_dir.is_dir():
        for p in sorted(ws_dir.iterdir(), key=lambda p: p.name):
            if p.is_dir():
                out.append(
                    WorkspaceOut(
                        id=f"srv:{p.name}",
                        kind="server",
                        name=p.name,
                        path=f"/workspace/{p.name}",
                        online=True,
                    )
                )
    conn = _bridge(request).get(user.id)
    if conn is not None:
        for d in conn.dirs:
            out.append(
                WorkspaceOut(
                    id=local_ws_id(d),
                    kind="local",
                    name=PurePosixPath(d.replace("\\", "/")).name or d,
                    path=d,
                    online=True,
                    host=conn.host,
                )
            )
    return out


class WorkspaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    body: WorkspaceBody,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    name = body.name.strip()
    if not NAME_RE.fullmatch(name) or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="이름에는 글자·숫자·하이픈·점·공백만 쓸 수 있습니다.")
    if not settings.code_enabled:
        raise HTTPException(status_code=403, detail="관리자가 코드 기능을 꺼 두었습니다.")
    profile = await get_profile(db, user, settings)
    runtime = _runtime(request)
    ws_dir = runtime.ensure_dirs(profile.sandbox_uid)
    target = ws_dir / name
    if target.exists():
        raise HTTPException(status_code=409, detail="같은 이름의 작업 공간이 이미 있습니다.")
    target.mkdir()
    shutil.chown(target, profile.sandbox_uid, profile.sandbox_uid)
    return WorkspaceOut(id=f"srv:{name}", kind="server", name=name, path=f"/workspace/{name}", online=True)


@router.delete("/workspaces/{ws_id}", status_code=204)
async def delete_workspace(
    ws_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    kind, name = parse_ws_id(ws_id)
    if kind == "local":
        raise HTTPException(status_code=400, detail="내 컴퓨터의 폴더는 여기서 지우지 않습니다.")
    profile = await get_profile(db, user, settings)
    root = _server_ws_root(_runtime(request), profile.sandbox_uid, name, kind)
    await run_in_threadpool(shutil.rmtree, root)
    return Response(status_code=204)


def _safe_join(root: Path, rel: str) -> Path:
    rel = rel.replace("\\", "/").lstrip("/")
    parts = [p for p in PurePosixPath(rel).parts if p not in {"", ".", ".."}]
    if not parts:
        raise HTTPException(status_code=400, detail="파일 경로가 올바르지 않습니다.")
    return root.joinpath(*parts)


def _chown_tree(root: Path, uid: int) -> None:
    """링크는 따라가지 않는다(lchown) - 링크가 가리키는 바깥 파일의 소유권이 넘어가지 않게."""
    os.chown(root, uid, uid, follow_symlinks=False)
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            try:
                os.chown(os.path.join(dirpath, name), uid, uid, follow_symlinks=False)
            except OSError:
                pass


@router.post("/workspaces/{ws_id}/files", status_code=201)
async def upload_files(
    ws_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    paths: list[str] | None = Form(None),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    """파일 여러 개(폴더 선택 시 상대 경로는 `paths`로) 또는 zip 하나(풀어서 넣는다)."""
    kind, name = parse_ws_id(ws_id)
    if kind == "local":
        raise HTTPException(status_code=400, detail="내 컴퍼터의 폴더에는 파일 탐색기로 직접 넣으세요.")
    profile = await get_profile(db, user, settings)
    root = _server_ws_root(_runtime(request), profile.sandbox_uid, name, kind)
    limit = settings.max_upload_size_mb * 1024 * 1024
    written = 0
    if len(files) == 1 and (files[0].filename or "").lower().endswith(".zip") and not paths:
        data = await files[0].read()
        if len(data) > limit:
            raise HTTPException(
                status_code=413, detail=f"파일이 최대 크기 {settings.max_upload_size_mb}MB를 초과했습니다."
            )

        def extract() -> int:
            n = 0
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                # zip 폭탄·항목 폭주 방지: 압축 해제 총량은 업로드 상한의 20배, 항목 5,000개까지.
                total = sum(i.file_size for i in zf.infolist())
                if total > limit * 20 or len(zf.infolist()) > 5000:
                    raise HTTPException(status_code=413, detail="zip 안의 파일이 너무 크거나 많습니다.")
                for info in zf.infolist():
                    # 디렉터리·심볼릭 링크 항목은 건너뛴다(zip 안의 링크로 바깥을 가리키는 고전 수법).
                    if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                        continue
                    dest = _safe_join(root, info.filename)
                    with zf.open(info) as src:
                        _write_file(root, dest, iter(lambda: src.read(1 << 20), b""))
                    n += 1
            return n

        try:
            written = await run_in_threadpool(extract)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="zip 파일을 읽을 수 없습니다.") from None
    else:
        for i, f in enumerate(files):
            rel = (paths[i] if paths and i < len(paths) and paths[i] else f.filename) or ""
            dest = _safe_join(root, rel)
            data = await f.read()
            if len(data) > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"파일이 최대 크기 {settings.max_upload_size_mb}MB를 초과했습니다.",
                )
            await run_in_threadpool(_write_file, root, dest, [data])
            written += 1
    await run_in_threadpool(_chown_tree, root, profile.sandbox_uid)
    return {"written": written}


@router.get("/workspaces/{ws_id}/download")
async def download_workspace(
    ws_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    kind, name = parse_ws_id(ws_id)
    if kind == "local":
        raise HTTPException(status_code=400, detail="내 컴퍼터의 폴더는 이미 내 컴퍼터에 있습니다.")
    profile = await get_profile(db, user, settings)
    root = _server_ws_root(_runtime(request), profile.sandbox_uid, name, kind)

    def build() -> bytes:
        # 메모리에서 만든다(작업 공간은 작다). 상한을 넘으면 413 - 커진 폴더가 백엔드 메모리를 먹지 않게.
        # ponytail: 스트리밍 zip은 상한을 넘는 사용자가 나오면.
        cap = 500 * 1024 * 1024
        total = 0
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for dirpath, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [
                    d for d in dirs if d not in ZIP_SKIP and not os.path.islink(os.path.join(dirpath, d))
                ]
                for name in files:
                    p = Path(dirpath) / name
                    if p.is_symlink() or not p.is_file():
                        continue
                    total += p.stat().st_size
                    if total > cap:
                        raise HTTPException(
                            status_code=413, detail="작업 공간이 500MB를 넘어 zip으로 받을 수 없습니다."
                        )
                    zf.write(p, str(p.relative_to(root)))
        return buf.getvalue()

    data = await run_in_threadpool(build)
    from urllib.parse import quote

    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.zip"},
    )


@router.get("/workspaces/{ws_id}/file")
async def download_file(
    ws_id: str,
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    """결과물 파일 하나 받기(서버 작업 공간). 내 컴퍼터 폴더는 이미 내 컴퍼터에 있다."""
    kind, name = parse_ws_id(ws_id)
    if kind == "local":
        raise HTTPException(status_code=400, detail="내 컴퍼터의 폴더는 이미 내 컴퍼터에 있습니다.")
    profile = await get_profile(db, user, settings)
    root = _server_ws_root(_runtime(request), profile.sandbox_uid, name, kind)
    target = _safe_join(root, path)
    if target.is_symlink() or not target.is_file() or not _inside(root, target):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return FileResponse(target, filename=target.name)


# --- 코워크 작업 ------------------------------------------------------------------------


class CoworkTask(BaseModel):
    id: str
    name: str
    updated_at: float  # epoch seconds - 폴더 안 가장 최근 수정
    files: int


def _list_cowork(runtime: ServerRuntime, uid: int) -> list[CoworkTask]:
    root = runtime.cowork_dir(uid)
    if not root.is_dir():
        return []
    out = []
    for p in root.iterdir():
        if p.is_dir():
            out.append(
                CoworkTask(
                    id=f"cw:{p.name}",
                    name=p.name,
                    updated_at=_mtime_tree(p),
                    files=sum(1 for f in p.rglob("*") if f.is_file()),
                )
            )
    return sorted(out, key=lambda t: t.updated_at, reverse=True)


@router.get("/cowork/tasks", response_model=list[CoworkTask])
async def list_cowork_tasks(
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    """지난 작업 - 작업(대화)마다 폴더 하나. 넣은 파일과 만든 파일이 그 안에 함께 있다."""
    profile = await get_profile(db, user, settings)
    return await run_in_threadpool(_list_cowork, _runtime(request), profile.sandbox_uid)


@router.post("/cowork/tasks", response_model=CoworkTask, status_code=201)
async def create_cowork_task(
    body: WorkspaceBody,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    """새 작업 폴더. 이름은 첫 요청의 앞부분 - 같은 이름이 있으면 번호를 붙인다."""
    if not settings.code_enabled:
        raise HTTPException(status_code=403, detail="관리자가 코드 기능을 꺼 두었습니다.")
    base = re.sub(r"[^\w\-. ]+", " ", body.name).strip()[:40] or "새 작업"
    if not NAME_RE.fullmatch(base) or base in {".", ".."}:
        base = "새 작업"
    profile = await get_profile(db, user, settings)
    runtime = _runtime(request)
    runtime.ensure_dirs(profile.sandbox_uid)
    parent = runtime.cowork_dir(profile.sandbox_uid)
    name, n = base, 2
    while (parent / name).exists():
        name, n = f"{base} ({n})", n + 1
    (parent / name).mkdir()
    shutil.chown(parent / name, profile.sandbox_uid, profile.sandbox_uid)
    return CoworkTask(id=f"cw:{name}", name=name, updated_at=(parent / name).stat().st_mtime, files=0)


def purge_cowork_tasks(data_dir: Path, retention_days: int) -> list[str]:
    """마지막 사용 후 retention_days가 지난 코워크 작업 폴더를 통째로 지운다(워커 크론이 매일 부른다).
    파일 단위가 아니라 폴더 단위인 이유: 진행 중인 작업의 오래된 입력 파일만 골라 지우면 작업이 깨진다."""
    import time

    if retention_days <= 0:
        return []
    cutoff = time.time() - retention_days * 86400
    removed = []
    for task in data_dir.glob("users/*/cowork/*"):
        if task.is_dir() and _mtime_tree(task) < cutoff:
            shutil.rmtree(task, ignore_errors=True)
            removed.append(str(task))
    return removed


# --- 내 컴퓨터 연결 -------------------------------------------------------------------


@router.get("/companion")
async def companion_script(
    request: Request,
    db: AsyncSession = Depends(get_db_session, scope="function"),
    redis: Redis = Depends(get_redis),
):
    """컴패니언 파일. 브라우저(세션 쿠키)도, setup 스크립트(브리지 토큰 Bearer)도 받는다 - 한 줄 설치가
    로그인 없이 파일을 가져와야 하는데, 토큰은 그 계정에 이미 묶여 있으니 같은 신뢰 수준이다."""
    if request.headers.get("authorization", "").startswith("Bearer "):
        await _companion_user(request, db)
    else:
        await get_current_user(request, db, redis)
    return FileResponse(
        COMPANION_PATH, media_type="text/javascript; charset=utf-8", filename="mopan-code.mjs"
    )


@router.get("/setup.ps1", include_in_schema=False)
@router.get("/setup.sh", include_in_schema=False)
async def setup_script(request: Request):
    """한 줄 설치 스크립트(Windows PowerShell / macOS·Linux bash). 비밀이 없어 공개. 서버 주소·토큰·폴더는
    코드 탭이 만들어 준 명령의 환경 변수로 들어온다(components/code/ConnectDialog.tsx)."""
    name = request.url.path.rsplit("/", 1)[-1]
    return FileResponse(COMPANION_PATH.with_name(name), media_type="text/plain; charset=utf-8")


@router.post("/bridge/token")
async def issue_bridge_token(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    """새 토큰 발급(이전 것은 즉시 무효). 원문은 이 응답에서만 볼 수 있다."""
    profile = await get_profile(db, user, settings)
    token = secrets.token_urlsafe(32)
    profile.bridge_token_hash = token_hash(token)
    await db.commit()
    return {"token": token}


async def _companion_user(request: Request, db: AsyncSession) -> User:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="토큰이 없습니다.")
    user_id = await db.scalar(
        select(CodeProfile.user_id).where(CodeProfile.bridge_token_hash == token_hash(token))
    )
    user = await db.get(User, user_id) if user_id else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="토큰이 올바르지 않습니다.")
    return user


# GET과 POST 둘 다. Cloudflare quick tunnel이 GET 스트림 응답을 연결이 끝날 때까지 통째로 붙잡아 두는 것을
# 실측했다(2026-09-26: 터널→백엔드 직결에서도 같았고 POST /api/chat SSE는 즉시 흘렀다). 컴패니언은 POST로
# 연다. GET은 로컬 디버그용.
@router.api_route("/bridge/stream", methods=["GET", "POST"])
async def bridge_stream(
    request: Request,
    dirs: str = "",
    host: str = "",
    version: str = "",
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    """컴패니언이 열어 두는 SSE. 서버→PC 요청 프레임이 여기로 내려간다. `dirs`는 `|`로 이은 폴더 경로."""
    user = await _companion_user(request, db)
    folders = [d for d in dirs.split("|") if d.strip()]
    if not folders:
        raise HTTPException(status_code=400, detail="연결할 폴더를 하나 이상 지정해 주세요(--dir).")
    bridge = _bridge(request)
    conn = bridge.connect(user.id, folders, host[:80], version[:40])

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'hello', 'dirs': folders}, ensure_ascii=False)}\n\n"
            while conn.alive:
                try:
                    item = await asyncio.wait_for(conn.outgoing.get(), 20.0)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            bridge.disconnect(conn)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/bridge/reply/{request_id}")
async def bridge_reply(request_id: str, request: Request, db: AsyncSession = Depends(get_db_session, scope="function")):
    user = await _companion_user(request, db)
    conn = _bridge(request).get(user.id)
    frame = await request.json()
    if conn is None or not _bridge(request).reply(conn, request_id, frame):
        # 브라우저가 이미 떠난 요청 - 컴패니언은 이걸 보고 그 fetch를 끊는다.
        return {"accepted": False}
    return {"accepted": True}


# --- OpenCode 프록시 ---------------------------------------------------------------


@router.api_route("/ws/{ws_id}/oc/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(
    ws_id: str,
    path: str,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    kind, target = parse_ws_id(ws_id)
    if path.startswith(PROXY_DENY_PREFIXES) and path != "config/providers":
        raise HTTPException(status_code=404, detail="지원하지 않는 경로입니다.")
    # OpenCode의 /event는 GET 전용 SSE인데 Cloudflare 터널이 GET 스트림을 붙잡아 두므로(위 bridge_stream 주석)
    # 브라우저는 POST로 열고 여기서 GET으로 바꿔 올린다.
    method = "GET" if path == "event" else request.method
    profile = await get_profile(db, user, settings)
    query = {k: v for k, v in request.query_params.multi_items() if k not in {"directory", "workspace"}}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    body = await request.body()
    if method != request.method:  # POST → GET으로 바꿔 올릴 때 본문과 그 헤더는 버린다
        body = b""
        headers = {k: v for k, v in headers.items() if k.lower() not in {"content-type", "content-length"}}
    runtime = _runtime(request)

    if kind in {"server", "cowork"}:
        if not settings.code_enabled:
            raise HTTPException(status_code=403, detail="관리자가 코드 기능을 꺼 두었습니다.")
        _server_ws_root(runtime, profile.sandbox_uid, target, kind)
        models, default = await _models_for_opencode(request, await load_catalog(db, settings), settings)
        try:
            proc = await runtime.ensure(user.id, profile.sandbox_uid, models, default)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"코드 서버를 띄우지 못했습니다: {exc}") from exc
        query["directory"] = _sandbox_dir(kind, target)
        req = runtime.http.build_request(
            method, f"{proc.base_url}/{path}", params=query, headers=headers, content=body or None
        )
        req.headers["Authorization"] = proc.auth._auth_header
        # httpx가 기본으로 붙이는 accept-encoding: gzip 때문에 OpenCode가 큰 응답을 압축해 보내고, 우리는
        # aiter_raw로 그 바이트를 content-encoding 없이 그대로 흘려 브라우저의 JSON 파싱이 깨졌다(실사고
        # 2026-09-26: 1KB 넘는 파일 미리 보기만 "알 수 없는 오류"). 압축 없이 받는다.
        req.headers["Accept-Encoding"] = "identity"
        resp = await runtime.http.send(req, stream=True)
        status_code, resp_headers = resp.status_code, resp.headers

        async def iterate():
            try:
                async for chunk in resp.aiter_raw():
                    runtime.touch(user.id)
                    yield chunk
            finally:
                await resp.aclose()

    else:
        conn = _bridge(request).get(user.id)
        if conn is None or target not in conn.dirs:
            raise HTTPException(
                status_code=409, detail="내 컴퓨터가 연결되어 있지 않습니다. 컴패니언을 실행해 주세요."
            )
        query["directory"] = target
        try:
            status_code, resp_headers, iterate_ = await _bridge(request).request(
                conn, method, "/" + path, query, headers, body or None
            )
        except BridgeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        iterate = lambda: iterate_  # noqa: E731

    content_type = resp_headers.get("content-type", "application/json")
    out_headers = (
        {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
        if "event-stream" in content_type
        else {}
    )
    return StreamingResponse(iterate(), status_code=status_code, media_type=content_type, headers=out_headers)
