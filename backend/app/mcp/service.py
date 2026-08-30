import logging
import time
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.service import (
    DEFAULT_AGENT,
    TOOL_NOT_ALLOWED_MESSAGE,
    ResolvedAgent,
)
from app.core.config import Settings
from app.core.logging import log_event
from app.mcp.client import MCPClient, MCPError, MCPTarget
from app.models.mcp import DEFAULT_RISK_LEVEL, McpServer, McpTool
from app.retrieval.evidence import Evidence

logger = logging.getLogger("mopan.mcp")

TOOL_NOT_FOUND_MESSAGE = "요청한 MCP 도구를 찾을 수 없습니다."
TOOL_UNAVAILABLE_MESSAGE = "지금은 사용할 수 없는 MCP 도구입니다. 관리자에게 문의해 주세요."
# Slice 3 adds the approval frame that would let a human authorise one of these.
# Until it exists, an unattended destructive call is the failure this system must
# not have, so the manual path refuses rather than asking nobody.
DESTRUCTIVE_MESSAGE = (
    "위험도가 '파괴적'으로 분류된 도구는 아직 직접 호출할 수 없습니다. 관리자에게 문의해 주세요."
)


def target_of(server: McpServer) -> MCPTarget:
    """A detached copy of everything the network call needs. The ORM object stays
    behind: a tool call runs with no session open, exactly like the LLM round
    trip in app/chat/router.py."""
    return MCPTarget(name=server.name, base_url=server.base_url, auth_token=server.auth_token)


def to_evidence(server_name: str, tool_name: str, content: str, metadata: dict | None = None) -> Evidence:
    """The whole security argument of this slice in one function.

    A tool result enters the prompt as ordinary Evidence, so it lands inside the
    same per-request nonce fence, goes through the same `_strip_fence_markers`,
    and competes for the same ANSWER_CONTEXT_TOKEN_BUDGET as corpus text -
    structurally, not because anyone remembered to. A server returning "ignore
    previous instructions" therefore gets exactly as far as a PDF saying the
    same: nowhere. `answer()` does not change to accommodate this, which is the
    Slice 1 seam holding.
    """
    return Evidence(
        source_type="mcp",
        ref=f"mcp:{server_name}/{tool_name}",
        content=content,
        # Nothing ranked this. The user picked it, the way an attachment is
        # picked - see app/attachments/service.py:to_evidence.
        score=None,
        metadata={"server": server_name, "tool": tool_name, **(metadata or {})},
    )


async def discover(db: AsyncSession, server: McpServer, *, settings: Settings) -> list[McpTool]:
    """`tools/list`, upserted. Returns every tool row for this server afterwards,
    tombstones included, because the admin screen shows them.

    Two rules that are not obvious and are each guarded by a test:

    A tool that DISAPPEARS is disabled, never deleted. `messages.citations` names
    it, and a citation pointing at nothing is worse than a row that says the
    server stopped offering it.

    `risk_level` and `enabled` on a tool that is STILL THERE are never
    overwritten. Both are admin decisions; re-discovery is a refresh of what the
    server says about itself, and a server author must not be able to reclassify
    their own tool from `destructive` back to `read` by editing a description.
    """
    async with MCPClient(
        target_of(server),
        timeout=settings.mcp_timeout_seconds,
        allow_private_networks=settings.mcp_allow_private_networks,
    ) as client:
        listed = await client.list_tools()

    existing = {
        row.name: row
        for row in (await db.scalars(select(McpTool).where(McpTool.server_id == server.id))).all()
    }
    seen: set[str] = set()
    for entry in listed:
        name = entry["name"][:200]
        seen.add(name)
        description = entry.get("description")
        schema = entry.get("inputSchema")
        row = existing.get(name)
        if row is None:
            db.add(
                McpTool(
                    server_id=server.id,
                    name=name,
                    description=description if isinstance(description, str) else None,
                    input_schema=schema if isinstance(schema, dict) else {},
                    risk_level=DEFAULT_RISK_LEVEL,
                )
            )
            continue
        row.description = description if isinstance(description, str) else None
        row.input_schema = schema if isinstance(schema, dict) else {}
        # Not `row.enabled = True` and not `row.risk_level = ...`. See the
        # docstring: a tool that came back from the dead stays disabled until an
        # admin says otherwise, and a classification is the admin's, not the
        # server author's.

    vanished = [row for name, row in existing.items() if name not in seen and row.enabled]
    for row in vanished:
        row.enabled = False
    await db.commit()

    log_event(
        logger,
        "mcp_tools_discovered",
        server_id=str(server.id),
        server=server.name,
        listed=len(seen),
        tombstoned=len(vanished),
    )
    return list(
        (
            await db.scalars(
                select(McpTool).where(McpTool.server_id == server.id).order_by(McpTool.name)
            )
        ).all()
    )


@dataclass(frozen=True)
class PendingToolCall:
    """A tool call that has been fully authorised and detached from the session.

    Slice 3's orchestrator produces these from an execution plan; Slice 2's chat
    router produces them from what the user picked. `run_tool_calls` below takes
    no database and no request, so it is callable from either.
    """

    target: MCPTarget
    server_name: str
    tool_name: str
    arguments: dict
    risk_level: str


async def load_tool_calls(
    db: AsyncSession,
    requested: list[tuple[uuid.UUID, dict]],
    agent: ResolvedAgent = DEFAULT_AGENT,
) -> list[PendingToolCall]:
    """Resolve tool ids to callable targets, or refuse.

    Called BEFORE the conversation is created, for the same reason
    `load_claimable` is: a bad tool id must not leave a titled, empty
    conversation in the sidebar, and once a StreamingResponse has begun there is
    no status line left to set - a 404 would degrade into an error frame inside
    a 200.

    THE AGENT CHECK IS HERE, not in the router, because this is the manual half
    of the same boundary `load_available` keeps for the planner. Restricting an
    agent to read-only tools would mean nothing if the user could pick a
    `destructive` one out of the composer's own tool picker on the very same
    turn - the planner would be fenced and the human would not be, which is the
    wrong way round. DEFAULT_AGENT allows everything, so the pre-agent behaviour
    is unchanged.
    """
    if not requested:
        return []
    ids = [tool_id for tool_id, _ in requested]
    rows = (
        await db.execute(
            select(McpTool, McpServer).join(McpServer, McpServer.id == McpTool.server_id).where(
                McpTool.id.in_(ids)
            )
        )
    ).all()
    by_id = {tool.id: (tool, server) for tool, server in rows}

    calls: list[PendingToolCall] = []
    for tool_id, arguments in requested:
        found = by_id.get(tool_id)
        if found is None:
            raise HTTPException(status_code=404, detail=TOOL_NOT_FOUND_MESSAGE)
        tool, server = found
        if not tool.enabled or not server.enabled:
            # 409, not the 404 above: the row exists and an admin turned it off,
            # so there is nothing to conceal - only a state to explain.
            raise HTTPException(status_code=409, detail=TOOL_UNAVAILABLE_MESSAGE)
        if not agent.allows_tool(tool.id):
            # 403, not the 409 above: the tool is fine and enabled, the CALLER is
            # not allowed to reach it through this agent. Checked before the
            # risk_level rule so the message names the real reason.
            raise HTTPException(status_code=403, detail=TOOL_NOT_ALLOWED_MESSAGE)
        if tool.risk_level == "destructive":
            raise HTTPException(status_code=400, detail=DESTRUCTIVE_MESSAGE)
        calls.append(
            PendingToolCall(
                target=target_of(server),
                server_name=server.name,
                tool_name=tool.name,
                arguments=arguments,
                risk_level=tool.risk_level,
            )
        )
    return calls


async def run_tool_calls(calls: list[PendingToolCall], *, settings: Settings) -> list[Evidence]:
    """Execute them and return Evidence. No session, no request, no response.

    A step that fails becomes Evidence saying so rather than killing the answer:
    the user asked a question, and "the tool did not answer" is information the
    model can use and cite. That is also the Slice 3 rule - a failed step is
    recorded and the plan continues.
    """
    evidence: list[Evidence] = []
    for call in calls:
        started = time.perf_counter()
        try:
            async with MCPClient(
                call.target,
                timeout=settings.mcp_timeout_seconds,
                allow_private_networks=settings.mcp_allow_private_networks,
            ) as client:
                content = await client.call_tool(call.tool_name, call.arguments)
        except MCPError as exc:
            # str(exc) is one of the Korean constants in app/mcp/client.py, all of
            # which have already been through `redact`.
            content = f"[도구 호출 실패] {exc}"
            log_event(
                logger,
                "mcp_tool_failed",
                server=call.server_name,
                tool=call.tool_name,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        else:
            log_event(
                logger,
                "mcp_tool_called",
                server=call.server_name,
                tool=call.tool_name,
                risk_level=call.risk_level,
                # The LENGTH, never the text and never the arguments: a tool
                # result is third-party content and the arguments are the user's.
                result_chars=len(content),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        evidence.append(
            to_evidence(
                call.server_name,
                call.tool_name,
                content or "[도구가 빈 결과를 반환했습니다]",
                {"risk_level": call.risk_level},
            )
        )
    return evidence
