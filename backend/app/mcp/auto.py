"""자동 도구 사용 - 켜 둔 MCP 서버의 도구를 모델이 상황을 보고 알아서 부른다.

소유자가 지목한 클로드 데스크톱의 모양이다: MCP 서버는 토글로 켜고 끄는
**기본 사용 설정**이고, 켜져 있으면 모델이 질문을 보고 필요할 때 알아서
부른다. `@`는 그와 별개로 - 꺼져 있어도 - 특정 도구를 적극적으로 부르는
길이며, 그 경로(load_tool_calls)는 여기와 한 줄도 겹치지 않는다.

동작은 숙고(deliberation) 한 번이다: 켜진 도구들의 스키마를 function-calling
으로 모델에게 보여주고, 모델이 호출을 적으면 실행해서 Evidence로 만든다.
반복(멀티홉)은 없다 - 한 바퀴가 상한이고, 그 상한이 청구서의 상한이다.

**자동 경로는 read 등급만 부른다.** 이 저장소의 규칙에서 write·destructive는
사람의 결정(@로 직접 부르기, 워크플로우의 승인 게이트)을 지나야 한다. 모델이
고른 호출이 무인으로 부수는 것은 승인 게이트가 존재하는 이유 그 자체다.
새로 발견된 도구의 기본 등급이 write(미분류는 싼 것이 아니다)이므로, 자동
사용을 원하는 도구는 관리자가 MCP 관리에서 read로 분류해 주어야 한다.

모든 실패는 "추가 근거 없음"으로 강등된다. 도구가 죽어도 답변은 나간다.
"""

import json
import logging
import re
import time
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.base import ChatMessage, LLMProvider
from app.mcp.client import MCPTarget
from app.mcp.service import PendingToolCall, run_tool_calls
from app.models.mcp import McpServer, McpTool
from app.retrieval.evidence import Evidence
from app.workflow.catalogue import DEFAULT_WORKFLOW, ResolvedWorkflow

logger = logging.getLogger("mopan.mcp")

# 숙고에 보여줄 도구 수의 상한. 스키마가 프롬프트에 통째로 실리므로 이것이
# 토큰 상한이기도 하다. 서버 몇 개 분량이고, 넘치면 앞에서 자른다.
MAX_OFFERED_TOOLS = 24

_SYSTEM = (
    "You can call the tools below. Call one ONLY when the user's question actually needs live "
    "or external data that a tool provides - a greeting, an opinion, or a question answerable "
    "from documents needs NO tool. When no tool helps, reply with the single word: pass. "
    "Never invent tool output."
)


def _spec_name(server: str, tool: str, taken: set[str]) -> str:
    """OpenAI function 이름 규칙(^[a-zA-Z0-9_-]{1,64}$)에 맞춘 유일한 이름."""
    base = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{server}_{tool}")[:60] or "tool"
    name = base
    suffix = 1
    while name in taken:
        suffix += 1
        name = f"{base}_{suffix}"
    taken.add(name)
    return name


async def deliberate_and_run(
    db: AsyncSession,
    llm_provider: LLMProvider,
    *,
    settings: Settings,
    question: str,
    auto_tool_ids: list[uuid.UUID],
    model: str,
    workflow: ResolvedWorkflow = DEFAULT_WORKFLOW,
) -> tuple[list[Evidence], dict | None]:
    """켜진 도구를 모델에게 보여주고, 부르겠다는 것을 실행해 Evidence로.

    돌려주는 dict는 추적용이다: 무엇을 보여줬고 무엇이 불렸는지. 아무 도구도
    남지 않았거나 모델이 부르지 않았으면 ([], trace)로 조용히 끝난다.
    """
    started = time.perf_counter()
    if not auto_tool_ids:
        return [], None

    # 알 수 없는 id·꺼진 도구·read가 아닌 등급·워크플로우 경계 밖은 조용히
    # 거른다. @ 경로(load_tool_calls)가 404·409로 거절하는 것과 다른 이유:
    # 저쪽은 사용자가 방금 고른 것이라 거절을 보여야 하고, 이쪽은 브라우저에
    # 남아 있던 기본 설정이라 낡은 id가 섞여 있는 것이 정상 상태다.
    rows = (
        await db.execute(
            select(McpTool, McpServer)
            .join(McpServer, McpServer.id == McpTool.server_id)
            .where(
                McpTool.id.in_(auto_tool_ids),
                McpTool.enabled.is_(True),
                McpServer.enabled.is_(True),
                McpTool.risk_level == "read",
            )
        )
    ).all()
    rows = [(t, s) for t, s in rows if workflow.allows_tool(t.id)][:MAX_OFFERED_TOOLS]
    if not rows:
        return [], None

    taken: set[str] = set()
    by_name: dict[str, tuple[McpTool, McpServer]] = {}
    specs: list[dict] = []
    for tool, server in rows:
        name = _spec_name(server.name, tool.name, taken)
        by_name[name] = (tool, server)
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": (tool.description or "")[:400],
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            }
        )

    trace: dict = {"offered": len(specs), "called": []}
    try:
        result = await llm_provider.chat(
            [
                ChatMessage(role="system", content=_SYSTEM),
                ChatMessage(role="user", content=question),
            ],
            temperature=0.0,
            tools=specs,
            model=model,
        )
    except Exception:
        logger.warning("tool deliberation failed; answering without tools", exc_info=True)
        trace["error"] = "deliberation_failed"
        trace["ms"] = int((time.perf_counter() - started) * 1000)
        return [], trace

    calls: list[PendingToolCall] = []
    for tool_call in (result.tool_calls or [])[: settings.max_tool_calls_per_message]:
        found = by_name.get(tool_call.name)
        if found is None:
            continue
        tool, server = found
        try:
            arguments = json.loads(tool_call.arguments or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(arguments, dict):
            continue
        calls.append(
            PendingToolCall(
                target=MCPTarget(
                    name=server.name, base_url=server.base_url, auth_token=server.auth_token
                ),
                server_name=server.name,
                tool_name=tool.name,
                arguments=arguments,
                risk_level=tool.risk_level,
            )
        )
        trace["called"].append(f"{server.name}/{tool.name}")

    if not calls:
        trace["ms"] = int((time.perf_counter() - started) * 1000)
        return [], trace

    evidence = await run_tool_calls(calls, settings=settings)
    trace["ms"] = int((time.perf_counter() - started) * 1000)
    return evidence, trace
