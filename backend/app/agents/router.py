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
from app.models.agent import Agent
from app.models.collection import Collection
from app.models.mcp import McpServer, McpTool
from app.models.prompt import Prompt
from app.models.user import User
from app.schemas.agent import (
    AgentCollectionRef,
    AgentCreate,
    AgentOption,
    AgentResponse,
    AgentToolRef,
    AgentUpdate,
)

logger = logging.getLogger("mopan.agents")
router = APIRouter(prefix="/api/agents", tags=["agents"])

AGENT_NOT_FOUND_MESSAGE = "에이전트를 찾을 수 없습니다."
DUPLICATE_NAME_MESSAGE = "같은 이름의 에이전트가 이미 있습니다."
UNKNOWN_PROMPT_MESSAGE = "등록되지 않은 프롬프트입니다: {name}"
UNKNOWN_MODEL_MESSAGE = "사용할 수 없는 답변 모델입니다: {name}"
UNKNOWN_COLLECTION_MESSAGE = "등록되지 않은 분류가 포함되어 있습니다."
UNKNOWN_TOOL_MESSAGE = "등록되지 않은 MCP 도구가 포함되어 있습니다."


async def _server_names(db: AsyncSession) -> dict[uuid.UUID, str]:
    return dict((await db.execute(select(McpServer.id, McpServer.name))).all())


def _response(agent: Agent, email: str | None, servers: dict[uuid.UUID, str]) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        prompt_name=agent.prompt_name,
        answer_model=agent.answer_model,
        orchestrator=agent.orchestrator,
        enabled=agent.enabled,
        collections=[AgentCollectionRef(id=c.id, name=c.name) for c in agent.collections],
        tools=[
            AgentToolRef(
                id=t.id,
                # The id, not a join: a tool whose server row vanished would be a
                # foreign key violation, so this only falls back for a session
                # that has not loaded the map.
                server_name=servers.get(t.server_id, ""),
                name=t.name,
                risk_level=t.risk_level,
            )
            for t in agent.tools
        ],
        created_by_email=email,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


async def _validate_prompt(db: AsyncSession, name: str) -> None:
    """A prompt an agent names has to exist, or the first question it answers
    dies inside the stream where nothing can explain it.

    `get_prompt` falls back to the module constant, so the built-in names are
    valid even before migration 0004/0007 has seeded them - which is also what
    keeps this check honest on a database whose `prompts` table is empty.
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


async def _get(db: AsyncSession, agent_id: uuid.UUID) -> Agent:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail=AGENT_NOT_FOUND_MESSAGE)
    return agent


@router.get("/selectable", response_model=list[AgentOption])
async def list_selectable_agents(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """What the composer's agent picker lists: ENABLED agents only.

    Any authenticated user, unlike every other route in this module. Picking an
    agent is not an administrative act - it is the same kind of choice as
    picking a model - and this returns exactly what POST /api/chat will accept,
    so a disabled agent is not merely un-runnable, it is unlistable and
    unnameable. The refusal at the other end (409) exists for the race, not for
    the UI.

    Declared BEFORE /{agent_id}: FastAPI matches routes in order, and
    "selectable" would otherwise be parsed as a uuid path parameter and 422.
    """
    rows = (await db.scalars(select(Agent).where(Agent.enabled.is_(True)).order_by(Agent.name))).all()
    return [
        AgentOption(
            id=agent.id,
            name=agent.name,
            description=agent.description,
            answer_model=agent.answer_model,
            orchestrator=agent.orchestrator,
        )
        for agent in rows
    ]


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    agents = (await db.scalars(select(Agent).order_by(Agent.name))).all()
    emails = dict((await db.execute(select(User.id, User.email))).all())
    servers = await _server_names(db)
    return [_response(agent, emails.get(agent.created_by), servers) for agent in agents]


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    payload: AgentCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """Admin only, because an agent is configuration every user then answers
    through: its prompt, its corpus scope and its tool list are exactly the three
    things Slice 1 put behind `require_admin` in the first place."""
    await _validate_prompt(db, payload.prompt_name)
    _validate_model(payload.answer_model, settings)
    collections = await _load_collections(db, payload.collection_ids)
    tools = await _load_tools(db, payload.tool_ids)

    agent = Agent(
        name=payload.name,
        description=payload.description,
        prompt_name=payload.prompt_name,
        answer_model=payload.answer_model,
        orchestrator=payload.orchestrator,
        enabled=payload.enabled,
        created_by=admin.id,
    )
    agent.collections = collections
    agent.tools = tools
    db.add(agent)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc

    log_event(
        logger,
        "agent_created",
        agent_id=str(agent.id),
        agent=agent.name,
        prompt_name=agent.prompt_name,
        collections=len(collections),
        tools=len(tools),
        admin_id=str(admin.id),
    )
    return _response(agent, admin.email, await _server_names(db))


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    payload: AgentUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    agent = await _get(db, agent_id)
    # OMITTED and NULL are different here, and telling them apart needs
    # `model_fields_set` rather than an `is not None` check. `description` and
    # `answer_model` are both nullable, so "clear it" is a state an admin has to
    # be able to reach - and the row's own 중지/사용 button sends nothing but
    # `enabled`, which under an is-not-None reading of a nullable field would
    # have silently wiped the model every time somebody paused an agent. Found
    # by driving it, not by reading it.
    fields = payload.model_fields_set
    if "prompt_name" in fields and payload.prompt_name is not None:
        await _validate_prompt(db, payload.prompt_name)
        agent.prompt_name = payload.prompt_name
    if "name" in fields and payload.name is not None:
        agent.name = payload.name
    if "description" in fields:
        agent.description = payload.description
    if "answer_model" in fields:
        # NULL is "use the deployment default", which is always allowed; only a
        # named model is checked against the allowlist.
        _validate_model(payload.answer_model, settings)
        agent.answer_model = payload.answer_model
    if payload.orchestrator is not None:
        agent.orchestrator = payload.orchestrator
    if payload.enabled is not None:
        agent.enabled = payload.enabled
    if payload.collection_ids is not None:
        agent.collections = await _load_collections(db, payload.collection_ids)
    if payload.tool_ids is not None:
        agent.tools = await _load_tools(db, payload.tool_ids)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc
    await db.refresh(agent)
    log_event(logger, "agent_updated", agent_id=str(agent.id), admin_id=str(admin.id))
    email = await db.scalar(select(User.email).where(User.id == agent.created_by))
    return _response(agent, email, await _server_names(db))


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """The join rows cascade; the MESSAGES DO NOT.

    `messages.agent_name` is a string, not a foreign key, precisely so this
    statement cannot reach a transcript. An admin retiring an agent must not be
    able to delete - or orphan - answers other people are still reading, and
    "which agent said this" has to stay answerable afterwards.
    """
    agent = await _get(db, agent_id)
    await db.delete(agent)
    await db.commit()
    log_event(logger, "agent_deleted", agent_id=str(agent_id), admin_id=str(admin.id))
