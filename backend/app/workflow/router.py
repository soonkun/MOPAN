"""/api/workflows and /api/tools.

Formerly /api/agents. The path moved with the table and the code: the UI says
워크플로우, so leaving `agents` in a URL would hand the next person the confusion
this slice exists to remove.

**A GRAPH IS VALIDATED AT SAVE, AGAINST THIS WORKFLOW'S OWN CATALOGUE.** That is
the fourth acceptance criterion of the design, and it is the reason
`POST /api/workflows/{id}/versions` calls `load_available(db, None, resolved)`
before `validate_graph`: a node naming a tool the workflow does not carry cannot
be resolved, so the graph is refused whole with a Korean 400 rather than saved
and refused later on somebody's question.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.chat.prompt import _FALLBACK_PROMPTS
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.models.collection import Collection
from app.models.mcp import McpServer, McpTool
from app.models.prompt import Prompt
from app.models.user import User
from app.models.workflow import Workflow, WorkflowVersion
from app.schemas.workflow import (
    CallableToolResponse,
    WorkflowCollectionRef,
    WorkflowCreate,
    WorkflowOption,
    WorkflowResponse,
    WorkflowToolRef,
    WorkflowUpdate,
    WorkflowVersionCreate,
    WorkflowVersionResponse,
)
from app.workflow.catalogue import graph_risk_level, load_available, resolve
from app.workflow.graph import GraphError, validate_graph

logger = logging.getLogger("mopan.workflow")
router = APIRouter(prefix="/api", tags=["workflows"])

WORKFLOW_NOT_FOUND_MESSAGE = "워크플로우를 찾을 수 없습니다."
VERSION_NOT_FOUND_MESSAGE = "해당 버전을 찾을 수 없습니다."
DUPLICATE_NAME_MESSAGE = "같은 이름의 워크플로우가 이미 있습니다."
UNKNOWN_PROMPT_MESSAGE = "등록되지 않은 프롬프트입니다: {name}"
UNKNOWN_MODEL_MESSAGE = "사용할 수 없는 답변 모델입니다: {name}"
UNKNOWN_COLLECTION_MESSAGE = "등록되지 않은 분류가 포함되어 있습니다."
UNKNOWN_TOOL_MESSAGE = "등록되지 않은 MCP 도구가 포함되어 있습니다."

# What a brand-new workflow starts as, and what migration 0010 wrote for every
# converted row: the graph that behaves exactly like the direct RAG path. A blank
# canvas would be a workflow that saves and cannot run, which is the state
# `input`/`answer` being undeletable exists to make unreachable.
STARTER_GRAPH = {
    "nodes": [
        {"id": "input", "kind": "input", "label": "질문", "x": 0, "y": 0},
        {
            "id": "search",
            "kind": "tool",
            "label": "문서 검색",
            "tool": "rag",
            "collections": [],
            "arguments": {"query": "{{input.text}}"},
            "x": 260,
            "y": 0,
        },
        {"id": "answer", "kind": "answer", "label": "답변", "x": 520, "y": 0},
    ],
    "edges": [{"from": "input", "to": "search"}, {"from": "search", "to": "answer"}],
}


async def _server_names(db: AsyncSession) -> dict[uuid.UUID, str]:
    return dict((await db.execute(select(McpServer.id, McpServer.name))).all())


async def _active(db: AsyncSession, workflow_id: uuid.UUID) -> WorkflowVersion | None:
    return await db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.is_active.is_(True)
        )
    )


def _response(
    workflow: Workflow,
    email: str | None,
    servers: dict[uuid.UUID, str],
    version: WorkflowVersion | None,
) -> WorkflowResponse:
    return WorkflowResponse(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        prompt_name=workflow.prompt_name,
        answer_model=workflow.answer_model,
        enabled=workflow.enabled,
        collections=[WorkflowCollectionRef(id=c.id, name=c.name) for c in workflow.collections],
        tools=[
            WorkflowToolRef(
                id=t.id,
                # The id, not a join: a tool whose server row vanished would be a
                # foreign key violation, so this only falls back for a session
                # that has not loaded the map.
                server_name=servers.get(t.server_id, ""),
                name=t.name,
                risk_level=t.risk_level,
            )
            for t in workflow.tools
        ],
        active_version=version.version if version else None,
        graph=version.graph if version else None,
        created_by_email=email,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


async def _validate_prompt(db: AsyncSession, name: str) -> None:
    """A prompt a workflow names has to exist, or the first question it answers
    dies inside the stream where nothing can explain it.

    `get_prompt` falls back to the module constant, so the built-in names are
    valid even before migration 0004/0007 has seeded them.
    """
    if name in _FALLBACK_PROMPTS:
        return
    exists = await db.scalar(select(Prompt.id).where(Prompt.name == name).limit(1))
    if exists is None:
        raise HTTPException(status_code=400, detail=UNKNOWN_PROMPT_MESSAGE.format(name=name[:100]))


def _validate_model(model: str | None, settings: Settings) -> None:
    """The SAME allowlist POST /api/chat enforces. Checked here as well as there
    because a Korean sentence on the form an admin is filling in is worth more
    than a refusal on somebody else's question three days later - and checked
    THERE as well as here because an operator can drop a model from ANSWER_MODELS
    long after this row was saved."""
    if model is not None and model not in settings.selectable_models:
        raise HTTPException(status_code=400, detail=UNKNOWN_MODEL_MESSAGE.format(name=model[:100]))


async def _load_collections(db: AsyncSession, ids: list[uuid.UUID]) -> list[Collection]:
    if not ids:
        return []
    rows = list((await db.scalars(select(Collection).where(Collection.id.in_(ids)))).all())
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=400, detail=UNKNOWN_COLLECTION_MESSAGE)
    return rows


async def _load_tools(db: AsyncSession, ids: list[uuid.UUID]) -> list[McpTool]:
    if not ids:
        return []
    rows = list((await db.scalars(select(McpTool).where(McpTool.id.in_(ids)))).all())
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=400, detail=UNKNOWN_TOOL_MESSAGE)
    return rows


async def _get(db: AsyncSession, workflow_id: uuid.UUID) -> Workflow:
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=WORKFLOW_NOT_FOUND_MESSAGE)
    return workflow


async def _save_version(
    db: AsyncSession, workflow: Workflow, graph: dict, *, admin: User, settings: Settings, note: str | None
) -> WorkflowVersion:
    """Validate, then insert as the new active version.

    THE VALIDATION IS THE BOUNDARY. `load_available` is narrowed by this
    workflow's own allow-lists, so a node naming a collection or a tool outside
    them cannot resolve and the graph is refused. `self_id` is what lets the
    workflow-cycle walk know which workflow it is looking at, so `A -> B -> A` is
    refused here rather than discovered by the depth counter at run.
    """
    resolved = resolve(workflow, None)
    resources = await load_available(db, None, resolved)
    try:
        validate_graph(graph, resources, settings=settings, self_id=workflow.id)
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    highest = (
        await db.scalar(
            select(WorkflowVersion.version)
            .where(WorkflowVersion.workflow_id == workflow.id)
            .order_by(WorkflowVersion.version.desc())
            .limit(1)
        )
    ) or 0
    # Deactivate first and FLUSH, or the partial unique index rejects the insert:
    # two active rows never exist even for the length of one statement.
    current = await _active(db, workflow.id)
    if current is not None:
        current.is_active = False
        await db.flush()
    version = WorkflowVersion(
        workflow_id=workflow.id,
        version=highest + 1,
        is_active=True,
        graph=graph,
        note=note,
        created_by=admin.id,
    )
    db.add(version)
    return version


@router.get("/workflows/selectable", response_model=list[WorkflowOption])
async def list_selectable_workflows(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """What the composer's `@` menu lists: ENABLED workflows that have a graph.

    Any authenticated user, unlike every other workflow route. Picking a workflow
    is not an administrative act - it is the same kind of choice as picking a
    model - and this returns exactly what POST /api/chat will accept.

    Declared BEFORE /{workflow_id}: FastAPI matches routes in order, and
    "selectable" would otherwise be parsed as a uuid path parameter and 422.
    """
    rows = (
        await db.execute(
            select(Workflow, WorkflowVersion)
            .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
            .where(Workflow.enabled.is_(True), WorkflowVersion.is_active.is_(True))
            .order_by(Workflow.name)
        )
    ).all()
    return [
        WorkflowOption(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            answer_model=workflow.answer_model,
            node_count=len((version.graph or {}).get("nodes") or []),
        )
        for workflow, version in rows
    ]


@router.get("/tools", response_model=list[CallableToolResponse])
async def list_callable_tools(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """**ONE list, because there is one Tool interface.**

    This is what `@` opens in the composer and what the canvas offers on a node:
    the RAG search, every enabled MCP tool, and every callable workflow. Three
    kinds, one namespace, one menu - the design's section 3 in one endpoint.

    Any authenticated user, exactly as GET /api/mcp/tools is: it lists what a
    question may already reach.
    """
    collections = list((await db.scalars(select(Collection).order_by(Collection.name))).all())
    entries = [
        CallableToolResponse(
            kind="rag",
            ref="rag",
            name="문서 검색",
            description="이 배포의 문서를 검색합니다.",
            risk_level="read",
            collections=[WorkflowCollectionRef(id=c.id, name=c.name) for c in collections],
        )
    ]
    tool_rows = (
        await db.execute(
            select(McpTool, McpServer)
            .join(McpServer, McpServer.id == McpTool.server_id)
            .where(McpTool.enabled.is_(True), McpServer.enabled.is_(True))
            .order_by(McpServer.name, McpTool.name)
        )
    ).all()
    entries.extend(
        CallableToolResponse(
            kind="mcp",
            ref=f"mcp:{server.name}/{tool.name}",
            name=f"{server.name}/{tool.name}",
            description=tool.description,
            risk_level=tool.risk_level,
        )
        for tool, server in tool_rows
    )
    risk_by_ref = {f"{server.name}/{tool.name}": tool.risk_level for tool, server in tool_rows}
    workflow_rows = (
        await db.execute(
            select(Workflow, WorkflowVersion)
            .join(WorkflowVersion, WorkflowVersion.workflow_id == Workflow.id)
            .where(Workflow.enabled.is_(True), WorkflowVersion.is_active.is_(True))
            .order_by(Workflow.name)
        )
    ).all()
    entries.extend(
        CallableToolResponse(
            kind="workflow",
            ref=f"workflow:{workflow.name}",
            name=workflow.name,
            description=workflow.description,
            # The inherited maximum, computed from the graph rather than stored,
            # so a workflow wrapping a destructive tool never lists as safe.
            risk_level=graph_risk_level(version.graph, risk_by_ref),
        )
        for workflow, version in workflow_rows
    )
    return entries


@router.get("/workflows", response_model=list[WorkflowResponse])
async def list_workflows(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    workflows = (await db.scalars(select(Workflow).order_by(Workflow.name))).all()
    emails = dict((await db.execute(select(User.id, User.email))).all())
    servers = await _server_names(db)
    versions = {
        version.workflow_id: version
        for version in (
            await db.scalars(select(WorkflowVersion).where(WorkflowVersion.is_active.is_(True)))
        ).all()
    }
    return [
        _response(workflow, emails.get(workflow.created_by), servers, versions.get(workflow.id))
        for workflow in workflows
    ]


@router.post("/workflows", response_model=WorkflowResponse, status_code=201)
async def create_workflow(
    payload: WorkflowCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """Admin only, because a workflow is configuration every user then answers
    through: its prompt, its corpus scope, its tool list and now its procedure."""
    await _validate_prompt(db, payload.prompt_name)
    _validate_model(payload.answer_model, settings)
    collections = await _load_collections(db, payload.collection_ids)
    tools = await _load_tools(db, payload.tool_ids)

    workflow = Workflow(
        name=payload.name,
        description=payload.description,
        prompt_name=payload.prompt_name,
        answer_model=payload.answer_model,
        enabled=payload.enabled,
        created_by=admin.id,
    )
    workflow.collections = collections
    workflow.tools = tools
    db.add(workflow)
    try:
        # Flushed before the version is validated: `_save_version` reads this
        # workflow's own allow-lists out of the session to build the catalogue.
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc
    version = await _save_version(
        db, workflow, payload.graph or STARTER_GRAPH, admin=admin, settings=settings, note=None
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc

    log_event(
        logger,
        "workflow_created",
        workflow_id=str(workflow.id),
        workflow=workflow.name,
        prompt_name=workflow.prompt_name,
        collections=len(collections),
        tools=len(tools),
        nodes=len((version.graph or {}).get("nodes") or []),
        admin_id=str(admin.id),
    )
    return _response(workflow, admin.email, await _server_names(db), version)


@router.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """The canvas's one request: the row, its boundary lists AND the active
    graph. Splitting the graph into a second endpoint would guarantee a screen
    that shows one workflow's boxes over another's name at least once."""
    workflow = await _get(db, workflow_id)
    email = await db.scalar(select(User.email).where(User.id == workflow.created_by))
    return _response(workflow, email, await _server_names(db), await _active(db, workflow.id))


@router.patch("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: uuid.UUID,
    payload: WorkflowUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    workflow = await _get(db, workflow_id)
    # OMITTED and NULL are different here, and telling them apart needs
    # `model_fields_set` rather than an `is not None` check. `description` and
    # `answer_model` are both nullable, so "clear it" is a state an admin has to
    # be able to reach - and the row's own 중지/사용 button sends nothing but
    # `enabled`, which under an is-not-None reading of a nullable field would
    # silently wipe the model every time somebody paused a workflow.
    fields = payload.model_fields_set
    if "prompt_name" in fields and payload.prompt_name is not None:
        await _validate_prompt(db, payload.prompt_name)
        workflow.prompt_name = payload.prompt_name
    if "name" in fields and payload.name is not None:
        workflow.name = payload.name
    if "description" in fields:
        workflow.description = payload.description
    if "answer_model" in fields:
        # NULL is "use the deployment default", which is always allowed; only a
        # named model is checked against the allowlist.
        _validate_model(payload.answer_model, settings)
        workflow.answer_model = payload.answer_model
    if payload.enabled is not None:
        workflow.enabled = payload.enabled
    if payload.collection_ids is not None:
        workflow.collections = await _load_collections(db, payload.collection_ids)
    if payload.tool_ids is not None:
        workflow.tools = await _load_tools(db, payload.tool_ids)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc
    await db.refresh(workflow)
    log_event(logger, "workflow_updated", workflow_id=str(workflow.id), admin_id=str(admin.id))
    email = await db.scalar(select(User.email).where(User.id == workflow.created_by))
    return _response(workflow, email, await _server_names(db), await _active(db, workflow.id))


@router.delete("/workflows/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """The join rows and the versions cascade; the MESSAGES DO NOT.

    `messages.workflow_name` is a string and `messages.workflow_version` an
    integer, neither a foreign key, precisely so this statement cannot reach a
    transcript. An admin retiring a workflow must not be able to delete - or
    orphan - answers other people are still reading.
    """
    workflow = await _get(db, workflow_id)
    await db.delete(workflow)
    await db.commit()
    log_event(logger, "workflow_deleted", workflow_id=str(workflow_id), admin_id=str(admin.id))


@router.get("/workflows/{workflow_id}/versions", response_model=list[WorkflowVersionResponse])
async def list_versions(
    workflow_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Newest first. This is the 되돌리기 list: a person editing a procedure can
    make it worse, and the only honest answer to that is the row that was there
    before, not a retype from memory."""
    await _get(db, workflow_id)
    rows = (
        await db.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version.desc())
        )
    ).all()
    emails = dict((await db.execute(select(User.id, User.email))).all())
    return [
        WorkflowVersionResponse(
            id=row.id,
            version=row.version,
            is_active=row.is_active,
            graph=row.graph,
            note=row.note,
            created_by_email=emails.get(row.created_by),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/workflows/{workflow_id}/versions", response_model=WorkflowVersionResponse, status_code=201)
async def create_version(
    workflow_id: uuid.UUID,
    payload: WorkflowVersionCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """Saving the canvas. **Every save is a version**, and the new one is active.

    A graph naming a tool outside this workflow's allowed list, a graph whose
    edges cycle, a `workflow:` node that leads back here, a `{{...}}` mixed into a
    string, a forward reference and `kind: "llm"` are all a Korean 400 here.
    """
    workflow = await _get(db, workflow_id)
    version = await _save_version(
        db, workflow, payload.graph, admin=admin, settings=settings, note=payload.note
    )
    await db.commit()
    log_event(
        logger,
        "workflow_version_saved",
        workflow_id=str(workflow.id),
        version=version.version,
        nodes=len((payload.graph or {}).get("nodes") or []),
        admin_id=str(admin.id),
    )
    return WorkflowVersionResponse(
        id=version.id,
        version=version.version,
        is_active=True,
        graph=version.graph,
        note=version.note,
        created_by_email=admin.email,
        created_at=version.created_at,
    )


@router.post(
    "/workflows/{workflow_id}/versions/{version}/activate", response_model=WorkflowVersionResponse
)
async def activate_version(
    workflow_id: uuid.UUID,
    version: int,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """되돌리기. Activates an existing version rather than copying it forward, so
    the history stays a history rather than growing a duplicate every rollback.

    NOT re-validated. A version that was refused never got saved, and
    re-validating here would make a rollback fail because an admin disabled a
    tool afterwards - which is exactly the moment somebody wants to roll back.
    The run-time boundary still holds: `validate_graph` runs again on every
    question, and refuses there with the direct RAG path as the fallback.
    """
    await _get(db, workflow_id)
    target = await db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.version == version
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail=VERSION_NOT_FOUND_MESSAGE)
    current = await _active(db, workflow_id)
    if current is not None and current.id != target.id:
        # Deactivate and FLUSH before activating: the partial unique index would
        # otherwise see two active rows.
        current.is_active = False
        await db.flush()
    target.is_active = True
    await db.commit()
    log_event(
        logger,
        "workflow_version_activated",
        workflow_id=str(workflow_id),
        version=version,
        admin_id=str(admin.id),
    )
    email = await db.scalar(select(User.email).where(User.id == target.created_by))
    return WorkflowVersionResponse(
        id=target.id,
        version=target.version,
        is_active=True,
        graph=target.graph,
        note=target.note,
        created_by_email=email,
        created_at=target.created_at,
    )
