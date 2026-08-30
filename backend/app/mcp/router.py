import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.mcp.client import MCPError, check_url
from app.mcp.service import discover
from app.models.mcp import McpServer, McpTool
from app.models.user import User
from app.schemas.mcp import (
    McpServerCreate,
    McpServerResponse,
    McpServerUpdate,
    McpToolOption,
    McpToolResponse,
    McpToolUpdate,
)

logger = logging.getLogger("mopan.mcp")
router = APIRouter(prefix="/api/mcp", tags=["mcp"])

SERVER_NOT_FOUND_MESSAGE = "MCP 서버를 찾을 수 없습니다."
TOOL_NOT_FOUND_MESSAGE = "MCP 도구를 찾을 수 없습니다."
DUPLICATE_NAME_MESSAGE = "같은 이름의 MCP 서버가 이미 있습니다."


def _response(
    server: McpServer, tools: list[McpTool], email: str | None, discovery_error: str | None = None
) -> McpServerResponse:
    return McpServerResponse(
        id=server.id,
        name=server.name,
        base_url=server.base_url,
        auth_kind=server.auth_kind,
        # The BOOLEAN, never the value. McpServerResponse has no field that could
        # carry the token even if this line were written wrongly.
        has_auth_token=bool(server.auth_token),
        enabled=server.enabled,
        created_by_email=email,
        created_at=server.created_at,
        updated_at=server.updated_at,
        tools=[McpToolResponse.model_validate(tool) for tool in tools],
        discovery_error=discovery_error,
    )


async def _tools_of(db: AsyncSession, server_id: uuid.UUID) -> list[McpTool]:
    return list(
        (
            await db.scalars(select(McpTool).where(McpTool.server_id == server_id).order_by(McpTool.name))
        ).all()
    )


async def _email_of(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    return await db.scalar(select(User.email).where(User.id == user_id))


async def _get_server(db: AsyncSession, server_id: uuid.UUID) -> McpServer:
    server = await db.get(McpServer, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_MESSAGE)
    return server


@router.get("/servers", response_model=list[McpServerResponse])
async def list_servers(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Servers with their tools in one payload. Two queries and a group-by in
    Python rather than a relationship: `discover` writes tools through the
    session directly, and a `selectin` relationship would hand this endpoint
    whatever was loaded before that commit."""
    rows = (
        await db.execute(
            select(McpServer, User.email)
            .outerjoin(User, User.id == McpServer.created_by)
            .order_by(McpServer.name)
        )
    ).all()
    tools = (await db.scalars(select(McpTool).order_by(McpTool.name))).all()
    by_server: dict[uuid.UUID, list[McpTool]] = {}
    for tool in tools:
        by_server.setdefault(tool.server_id, []).append(tool)
    return [_response(server, by_server.get(server.id, []), email) for server, email in rows]


@router.post("/servers", response_model=McpServerResponse, status_code=201)
async def create_server(
    payload: McpServerCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """Register, then discover.

    The URL is checked BEFORE the row is written: an address on the internal
    network is not a server that happens to be down, it is one this backend must
    never fetch, and a 400 has to say so rather than leaving a row behind that
    re-discovery would retry forever. A server that is merely unreachable is the
    opposite case - the row is kept and `discovery_error` explains the empty tool
    list, so an admin can fix a typo instead of registering it again.
    """
    try:
        await check_url(payload.base_url, allow_private=settings.mcp_allow_private_networks)
    except MCPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    server = McpServer(
        name=payload.name,
        base_url=payload.base_url,
        auth_kind=payload.auth_kind,
        auth_token=payload.auth_token if payload.auth_kind == "bearer" else None,
        created_by=admin.id,
    )
    db.add(server)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc

    log_event(
        logger,
        "mcp_server_registered",
        server_id=str(server.id),
        server=server.name,
        # Whether a token is set, never the token, and never the URL's userinfo.
        auth_kind=server.auth_kind,
        admin_id=str(admin.id),
    )
    error = None
    try:
        tools = await discover(db, server, settings=settings)
    except MCPError as exc:
        await db.rollback()
        tools, error = [], str(exc)
    return _response(server, tools, admin.email, error)


@router.patch("/servers/{server_id}", response_model=McpServerResponse)
async def update_server(
    server_id: uuid.UUID,
    payload: McpServerUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    server = await _get_server(db, server_id)
    if payload.base_url is not None:
        try:
            await check_url(payload.base_url, allow_private=settings.mcp_allow_private_networks)
        except MCPError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        server.base_url = payload.base_url
    if payload.name is not None:
        server.name = payload.name
    if payload.enabled is not None:
        server.enabled = payload.enabled
    if payload.auth_kind is not None:
        server.auth_kind = payload.auth_kind
        # Switching to `none` is the ONE way to clear a stored token. An omitted
        # auth_token means "leave it alone", which is what an admin editing the
        # name of a server whose token they do not know needs it to mean.
        if payload.auth_kind == "none":
            server.auth_token = None
    if payload.auth_token is not None and server.auth_kind == "bearer":
        server.auth_token = payload.auth_token
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc
    await db.refresh(server)
    return _response(server, await _tools_of(db, server.id), await _email_of(db, server.created_by))


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(
    server_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    server = await _get_server(db, server_id)
    await db.delete(server)  # tools cascade
    await db.commit()
    log_event(logger, "mcp_server_deleted", server_id=str(server_id), admin_id=str(admin.id))


@router.post("/servers/{server_id}/discover", response_model=McpServerResponse)
async def rediscover(
    server_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    server = await _get_server(db, server_id)
    error = None
    try:
        tools = await discover(db, server, settings=settings)
    except MCPError as exc:
        await db.rollback()
        tools, error = await _tools_of(db, server.id), str(exc)
    return _response(server, tools, await _email_of(db, server.created_by), error)


@router.patch("/tools/{tool_id}", response_model=McpToolResponse)
async def update_tool(
    tool_id: uuid.UUID,
    payload: McpToolUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Risk classification is an ADMIN's decision and lives only here. Discovery
    never writes it after the first sighting, so a server author cannot
    reclassify their own tool by editing its description."""
    tool = await db.get(McpTool, tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail=TOOL_NOT_FOUND_MESSAGE)
    if payload.risk_level is not None:
        tool.risk_level = payload.risk_level
    if payload.enabled is not None:
        tool.enabled = payload.enabled
    await db.commit()
    await db.refresh(tool)
    log_event(
        logger,
        "mcp_tool_updated",
        tool_id=str(tool.id),
        tool=tool.name,
        risk_level=tool.risk_level,
        enabled=tool.enabled,
        admin_id=str(admin.id),
    )
    return tool


@router.get("/tools", response_model=list[McpToolOption])
async def list_callable_tools(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """What the composer's tool picker lists: enabled tools on enabled servers.

    Any authenticated user, unlike every route above. This is the same argument
    GET /api/models makes - it returns exactly what POST /api/chat would accept,
    so it discloses nothing a user could not learn by trying - and it carries no
    base_url and no hint that a token exists.
    """
    rows = (
        await db.execute(
            select(McpTool, McpServer.name)
            .join(McpServer, McpServer.id == McpTool.server_id)
            .where(McpTool.enabled.is_(True), McpServer.enabled.is_(True))
            .order_by(McpServer.name, McpTool.name)
        )
    ).all()
    return [
        McpToolOption(
            id=tool.id,
            server_name=server_name,
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            risk_level=tool.risk_level,
        )
        for tool, server_name in rows
    ]
