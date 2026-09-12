"""딥 리서치 API - 방(projects) · 지침 버전 · 실행(runs). 계약: docs/superpowers/plans/2026-09-08-deep-research.md"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from arq import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.documents.service import get_arq_pool
from app.documents.validation import ALLOWED_EXTENSIONS, extension_of
from app.llm.catalog import load_catalog
from app.models.research import (
    RESEARCH_TERMINAL,
    ResearchInstruction,
    ResearchProject,
    ResearchRun,
    clamp_budget,
)
from app.models.user import User
from app.rag.parsers import get_parser
from app.rag.parsers.base import ParseFailure
from app.research.templates import TEMPLATES, get_template

logger = logging.getLogger("mopan.research")
router = APIRouter(prefix="/api/research", tags=["research"])

ATTACHMENT_MAX_BYTES = 30 * 1024 * 1024
ATTACHMENT_TEXT_CHARS = 30_000


# --- schemas -------------------------------------------------------------------

class ProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=20000)
    # 템플릿(app/research/templates.py)에서 만들 때. 지침·관점·설명을 비운 항목은 템플릿 값으로 채운다.
    template_id: str | None = Field(default=None, max_length=40)
    planner_hint: str | None = Field(default=None, max_length=1000)
    budget: dict | None = None
    model: str | None = Field(default=None, max_length=100)
    reasoning_effort: str | None = Field(default=None, pattern="^(minimal|low|medium|high)$")
    gap_model: str | None = Field(default=None, max_length=100)
    collection_ids: list[uuid.UUID] | None = None


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    planner_hint: str | None = Field(default=None, max_length=1000)
    budget: dict | None = None
    model: str | None = Field(default=None, max_length=100)
    reasoning_effort: str | None = Field(default=None, pattern="^(minimal|low|medium|high|)$")
    gap_model: str | None = Field(default=None, max_length=100)
    collection_ids: list[uuid.UUID] | None = None


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    budget: dict
    model: str | None
    reasoning_effort: str | None
    gap_model: str | None
    collection_ids: list[uuid.UUID]
    instructions: str
    instruction_version: int
    planner_hint: str | None
    template_id: str | None
    run_count: int
    last_run_at: datetime | None
    created_at: datetime


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    planner_hint: str
    instructions: str


class InstructionBody(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class InstructionResponse(BaseModel):
    version: int
    text: str
    is_active: bool
    created_by_email: str | None
    created_at: datetime


class RunSummary(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    prompt: str
    status: str
    model: str | None
    created_by_email: str | None
    created_at: datetime
    finished_at: datetime | None
    source_count: int


class RunResponse(RunSummary):
    steps: list[dict]
    report: str | None
    sources: list[dict]
    sub_queries: list[str]
    error: str | None
    attachment_name: str | None
    reasoning_effort: str | None
    usage: dict
    scope: dict | None


class RunPage(BaseModel):
    total: int
    items: list[RunSummary]


# --- helpers -------------------------------------------------------------------

async def _project_or_404(db: AsyncSession, pid: uuid.UUID) -> ResearchProject:
    project = await db.get(ResearchProject, pid)
    if project is None:
        raise HTTPException(status_code=404, detail="리서치 방을 찾을 수 없습니다.")
    return project


async def _active_instruction(db: AsyncSession, pid: uuid.UUID) -> tuple[str, int]:
    row = (
        await db.execute(
            select(ResearchInstruction.text, ResearchInstruction.version).where(
                ResearchInstruction.project_id == pid, ResearchInstruction.is_active.is_(True)
            )
        )
    ).first()
    return (row.text, row.version) if row else ("", 0)


async def _project_response(db: AsyncSession, project: ResearchProject) -> ProjectResponse:
    text, version = await _active_instruction(db, project.id)
    count, last = (
        await db.execute(
            select(func.count(ResearchRun.id), func.max(ResearchRun.created_at)).where(ResearchRun.project_id == project.id)
        )
    ).one()
    return ProjectResponse(
        id=project.id, name=project.name, description=project.description, budget=clamp_budget(project.budget),
        model=project.model, reasoning_effort=project.reasoning_effort, gap_model=project.gap_model,
        collection_ids=[uuid.UUID(c) for c in project.collection_ids], instructions=text, instruction_version=version,
        planner_hint=project.planner_hint, template_id=project.template_id,
        run_count=count or 0, last_run_at=last, created_at=project.created_at,
    )


async def _add_instruction(db: AsyncSession, project_id: uuid.UUID, text: str, user_id: uuid.UUID) -> ResearchInstruction:
    existing = (
        await db.scalars(select(ResearchInstruction).where(ResearchInstruction.project_id == project_id).with_for_update())
    ).all()
    await db.execute(update(ResearchInstruction).where(ResearchInstruction.project_id == project_id).values(is_active=False))
    row = ResearchInstruction(
        project_id=project_id, version=max((r.version for r in existing), default=0) + 1, text=text, is_active=True, created_by=user_id
    )
    db.add(row)
    await db.flush()
    return row


def _run_summary(run: ResearchRun, email: str | None) -> RunSummary:
    return RunSummary(
        id=run.id, project_id=run.project_id, prompt=run.prompt, status=run.status, model=run.model,
        created_by_email=email, created_at=run.created_at, finished_at=run.finished_at, source_count=len(run.sources or []),
    )


def _run_response(run: ResearchRun, email: str | None) -> RunResponse:
    return RunResponse(
        **_run_summary(run, email).model_dump(), steps=run.steps or [], report=run.report, sources=run.sources or [],
        sub_queries=run.sub_queries or [], error=run.error, attachment_name=run.attachment_name,
        reasoning_effort=run.reasoning_effort, usage=run.usage or {}, scope=run.scope,
    )


async def _validate_models(db: AsyncSession, settings: Settings, *names: str | None) -> None:
    catalog = await load_catalog(db, settings)
    for name in names:
        if name and not catalog.is_allowed(name):
            raise HTTPException(status_code=400, detail=f"사용할 수 없는 모델입니다: {name}")


# --- projects ------------------------------------------------------------------

@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(user: User = Depends(get_current_user)):
    """지침 템플릿. 방을 만들 때 고르거나 기존 방의 지침을 갈아 끼울 때 쓴다(app/research/templates.py)."""
    return [
        TemplateResponse(id=t.id, name=t.name, description=t.description, planner_hint=t.planner_hint, instructions=t.instructions)
        for t in TEMPLATES
    ]


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.scalars(select(ResearchProject).order_by(ResearchProject.created_at))).all()
    return [await _project_response(db, p) for p in rows]


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    payload: ProjectBody,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    await _validate_models(db, settings, payload.model, payload.gap_model)
    template = get_template(payload.template_id) if payload.template_id else None
    if payload.template_id and template is None:
        raise HTTPException(status_code=400, detail="없는 템플릿입니다.")
    instructions = (payload.instructions or "").strip() or (template.instructions if template else "")
    project = ResearchProject(
        name=payload.name.strip(),
        description=payload.description or (template.description if template else None),
        budget=clamp_budget(payload.budget),
        model=payload.model or None, reasoning_effort=payload.reasoning_effort, gap_model=payload.gap_model or None,
        collection_ids=[str(c) for c in (payload.collection_ids or [])], created_by=admin.id,
        planner_hint=(payload.planner_hint or "").strip() or (template.planner_hint if template else None),
        template_id=template.id if template else None,
    )
    db.add(project)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="같은 이름의 방이 이미 있습니다.")
    if instructions:
        await _add_instruction(db, project.id, instructions, admin.id)
    await db.commit()
    await db.refresh(project)
    log_event(logger, "research_project_created", project_id=str(project.id), admin_id=str(admin.id))
    return await _project_response(db, project)


@router.get("/projects/{pid}", response_model=ProjectResponse)
async def get_project(pid: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    return await _project_response(db, await _project_or_404(db, pid))


@router.patch("/projects/{pid}", response_model=ProjectResponse)
async def update_project(
    pid: uuid.UUID,
    payload: ProjectPatch,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    project = await _project_or_404(db, pid)
    fields = payload.model_dump(exclude_unset=True)
    await _validate_models(db, settings, fields.get("model"), fields.get("gap_model"))
    if "budget" in fields:
        fields["budget"] = clamp_budget(fields["budget"])
    if "collection_ids" in fields:
        fields["collection_ids"] = [str(c) for c in (fields["collection_ids"] or [])]
    if "reasoning_effort" in fields and not fields["reasoning_effort"]:
        fields["reasoning_effort"] = None
    if "planner_hint" in fields:
        fields["planner_hint"] = (fields["planner_hint"] or "").strip() or None
    for key, value in fields.items():
        setattr(project, key, value.strip() if key == "name" and value else value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="같은 이름의 방이 이미 있습니다.")
    await db.refresh(project)
    return await _project_response(db, project)


@router.delete("/projects/{pid}", status_code=204)
async def delete_project(pid: uuid.UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)):
    project = await _project_or_404(db, pid)
    await db.delete(project)
    await db.commit()


# --- instructions --------------------------------------------------------------

@router.get("/projects/{pid}/instructions", response_model=list[InstructionResponse])
async def list_instructions(pid: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    await _project_or_404(db, pid)
    rows = (
        await db.execute(
            select(ResearchInstruction, User.email)
            .outerjoin(User, User.id == ResearchInstruction.created_by)
            .where(ResearchInstruction.project_id == pid)
            .order_by(ResearchInstruction.version.desc())
        )
    ).all()
    return [
        InstructionResponse(version=r.version, text=r.text, is_active=r.is_active, created_by_email=email, created_at=r.created_at)
        for r, email in rows
    ]


@router.post("/projects/{pid}/instructions", response_model=InstructionResponse, status_code=201)
async def add_instruction(
    pid: uuid.UUID, payload: InstructionBody, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)
):
    await _project_or_404(db, pid)
    row = await _add_instruction(db, pid, payload.text.strip(), admin.id)
    await db.commit()
    return InstructionResponse(version=row.version, text=row.text, is_active=True, created_by_email=admin.email, created_at=row.created_at)


@router.post("/projects/{pid}/instructions/{version}/activate", response_model=InstructionResponse)
async def activate_instruction(
    pid: uuid.UUID, version: int, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db_session)
):
    row = await db.scalar(
        select(ResearchInstruction).where(ResearchInstruction.project_id == pid, ResearchInstruction.version == version)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="그 버전의 지침이 없습니다.")
    await db.execute(update(ResearchInstruction).where(ResearchInstruction.project_id == pid).values(is_active=False))
    row.is_active = True
    await db.commit()
    return InstructionResponse(version=row.version, text=row.text, is_active=True, created_by_email=None, created_at=row.created_at)


# --- runs ----------------------------------------------------------------------

async def _extract_attachment_text(file: UploadFile, upload_dir: Path) -> tuple[str, str]:
    filename = (file.filename or "").strip()
    extension = extension_of(filename)
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="텍스트를 뽑을 수 있는 문서(PDF·DOCX·XLSX·CSV·TXT 등)만 첨부할 수 있습니다.")
    raw = await file.read(ATTACHMENT_MAX_BYTES + 1)
    if len(raw) > ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="첨부는 30MB까지입니다.")
    tmp_dir = upload_dir / "research-tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / f"{uuid.uuid4()}.{extension}"
    path.write_bytes(raw)
    try:
        parsed = get_parser(extension).parse(str(path))
    except ParseFailure as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        path.unlink(missing_ok=True)
    text = "\n".join(b.text for b in parsed.blocks)[:ATTACHMENT_TEXT_CHARS]
    if not text.strip():
        raise HTTPException(status_code=400, detail="첨부에서 글자를 읽어내지 못했습니다.")
    return filename, text


@router.post("/projects/{pid}/runs", response_model=RunResponse, status_code=202)
async def start_run(
    pid: uuid.UUID,
    prompt: str = Form(min_length=1, max_length=8000),
    model: str | None = Form(default=None),
    reasoning_effort: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    arq_pool: ArqRedis = Depends(get_arq_pool),
):
    project = await _project_or_404(db, pid)
    await _validate_models(db, settings, model)
    if reasoning_effort and reasoning_effort not in ("minimal", "low", "medium", "high"):
        raise HTTPException(status_code=400, detail="추론 수준 값이 올바르지 않습니다.")
    active = select(func.count(ResearchRun.id)).where(ResearchRun.status.not_in(tuple(RESEARCH_TERMINAL)))
    if await db.scalar(active.where(ResearchRun.created_by == user.id)):
        raise HTTPException(status_code=409, detail="진행 중인 리서치가 있습니다. 끝나거나 취소한 뒤 다시 시작해 주세요.")
    if (await db.scalar(active) or 0) >= settings.research_max_concurrent:
        raise HTTPException(status_code=409, detail="지금 다른 리서치가 실행 중입니다. 잠시 후 다시 시작해 주세요.")
    attachment_name = attachment_text = None
    if file is not None and (file.filename or "").strip():
        attachment_name, attachment_text = await _extract_attachment_text(file, settings.upload_dir)
    run = ResearchRun(
        project_id=project.id, prompt=prompt.strip(), attachment_text=attachment_text, attachment_name=attachment_name,
        model=model or project.model or None, reasoning_effort=reasoning_effort or project.reasoning_effort, created_by=user.id,
        steps=[{"stage": "queued", "at": datetime.now(UTC).isoformat(), "detail": "대기열에 넣었습니다."}],
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    try:
        await arq_pool.enqueue_job("run_research", str(run.id))
    except Exception:
        logger.exception("failed to enqueue research run")
        run.status = "failed"
        run.error = "작업 대기열에 넣지 못했습니다. 워커(Redis)를 확인해 주세요."
        run.finished_at = datetime.now(UTC)
        await db.commit()
    log_event(logger, "research_run_enqueued", run_id=str(run.id), project_id=str(pid), user_id=str(user.id))
    return _run_response(run, user.email)


@router.get("/projects/{pid}/runs", response_model=RunPage)
async def list_runs(
    pid: uuid.UUID, offset: int = 0, limit: int = 20,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session),
):
    await _project_or_404(db, pid)
    limit = max(1, min(limit, 100))
    total = await db.scalar(select(func.count(ResearchRun.id)).where(ResearchRun.project_id == pid)) or 0
    rows = (
        await db.execute(
            select(ResearchRun, User.email).outerjoin(User, User.id == ResearchRun.created_by)
            .where(ResearchRun.project_id == pid).order_by(ResearchRun.created_at.desc()).offset(offset).limit(limit)
        )
    ).all()
    return RunPage(total=total, items=[_run_summary(r, e) for r, e in rows])


@router.get("/runs/{rid}", response_model=RunResponse)
async def get_run(rid: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    row = (
        await db.execute(select(ResearchRun, User.email).outerjoin(User, User.id == ResearchRun.created_by).where(ResearchRun.id == rid))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="실행을 찾을 수 없습니다.")
    return _run_response(row[0], row[1])


@router.get("/runs/{rid}/pdf")
async def run_pdf(rid: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    """보고서를 PDF 파일로. 서버에서 만든다(원본 CR-67: 브라우저 인쇄는 iOS에서 앱 화면이 찍혔다).
    출처 목록을 부록으로 붙이고, 본문의 [n]은 그대로 남겨 부록과 대응시킨다."""
    from app.research.pdf import PdfUnavailable, report_to_pdf

    run = await db.get(ResearchRun, rid)
    if run is None or not run.report:
        raise HTTPException(status_code=404, detail="보고서가 없습니다.")
    project = await db.get(ResearchProject, run.project_id)
    title = f"{project.name if project else '리서치'} 보고서"
    when = (run.finished_at or run.created_at).astimezone().strftime("%Y-%m-%d")
    meta = " · ".join(x for x in (run.prompt[:80].replace("\n", " "), when, run.model or "") if x)
    body = run.report
    if run.sources:
        lines = []
        for c in run.sources:
            where = " ".join(x for x in (c.get("filename") or "", f"p.{c['page']}" if c.get("page") else "", c.get("section") or "") if x)
            lines.append(f"- [{c.get('index')}] {where}")
        body = f"{body.rstrip()}\n\n## 출처\n\n" + "\n".join(lines)
    try:
        data = await run_in_threadpool(report_to_pdf, title, body, meta, "MOPAN 딥 리서치")
    except PdfUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # 파일명은 ASCII 대체 + RFC 5987 한글 - 브라우저가 한글 이름으로 저장한다.
    from urllib.parse import quote

    filename = f"{title}-{when}.pdf"
    headers = {"Content-Disposition": f"attachment; filename=\"report-{when}.pdf\"; filename*=UTF-8''{quote(filename)}"}
    return Response(content=data, media_type="application/pdf", headers=headers)


@router.post("/runs/{rid}/cancel", response_model=RunResponse, status_code=202)
async def cancel_run(rid: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db_session)):
    run = await db.get(ResearchRun, rid, with_for_update=True)
    if run is None:
        raise HTTPException(status_code=404, detail="실행을 찾을 수 없습니다.")
    if run.created_by != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="본인이 시작한 리서치만 취소할 수 있습니다.")
    if run.status in RESEARCH_TERMINAL:
        return _run_response(run, user.email)
    run.cancel_requested = True
    if run.status == "queued":
        # 아직 워커가 잡지 않았다 - 여기서 바로 끝낸다(워커가 잡으면 첫 단계에서 취소를 본다).
        run.status = "cancelled"
        run.finished_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(run)
    return _run_response(run, user.email)
