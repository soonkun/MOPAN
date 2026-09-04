"""자동 도구 사용(app/mcp/auto.py) - 숙고 한 번, read 등급만, 실패는 강등.

네트워크는 없다: 모델은 AsyncMock이고 실행(run_tool_calls)도 mock이다. 이
모듈이 책임지는 것은 그 사이 - 무엇을 모델에게 보여주고, 모델의 답을 어떤
호출로 바꾸는가 - 뿐이므로 거기서만 잰다.
"""

import json
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.llm.base import ChatResult, ToolCall
from app.mcp import auto
from app.mcp.auto import deliberate_and_run
from app.models.mcp import McpServer, McpTool
from app.models.user import User


def settings_with(**overrides) -> Settings:
    return Settings().model_copy(update=overrides)


@pytest_asyncio.fixture
async def catalogue(db):
    """서버 둘(하나는 꺼짐)과 등급별 도구들. 반환값은 이름 -> id 사전."""
    owner = User(email="auto@example.com", password_hash="x", role="admin")
    db.add(owner)
    await db.flush()
    server = McpServer(name="날씨", base_url="http://93.184.216.34/mcp", created_by=owner.id)
    dead = McpServer(
        name="꺼진서버", base_url="http://93.184.216.34/mcp", enabled=False, created_by=owner.id
    )
    db.add_all([server, dead])
    await db.flush()
    tools = {
        "read": McpTool(
            server_id=server.id,
            name="current_weather",
            description="Current weather.",
            input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
            risk_level="read",
        ),
        "write": McpTool(server_id=server.id, name="set_alert", risk_level="write"),
        "destructive": McpTool(server_id=server.id, name="wipe", risk_level="destructive"),
        "disabled": McpTool(
            server_id=server.id, name="off_tool", risk_level="read", enabled=False
        ),
        "dead_server": McpTool(server_id=dead.id, name="dead_tool", risk_level="read"),
    }
    db.add_all(tools.values())
    await db.commit()
    return {key: tool.id for key, tool in tools.items()}


@pytest.fixture
def provider():
    p = AsyncMock()
    p.chat = AsyncMock(return_value=ChatResult(content="pass"))
    return p


async def test_only_enabled_read_tools_on_enabled_servers_are_offered(db, catalogue, provider):
    evidence, trace = await deliberate_and_run(
        db,
        provider,
        settings=settings_with(),
        question="서울 날씨 어때?",
        auto_tool_ids=list(catalogue.values()),
        model="gpt-4o",
    )
    assert evidence == []
    assert trace["offered"] == 1
    specs = provider.chat.call_args.kwargs["tools"]
    assert [s["function"]["name"] for s in specs] == ["___current_weather"]


async def test_a_chosen_call_is_executed_as_a_read_pending_call(
    db, catalogue, provider, monkeypatch
):
    executed = AsyncMock(return_value=["EVIDENCE"])
    monkeypatch.setattr(auto, "run_tool_calls", executed)
    provider.chat.return_value = ChatResult(
        content="",
        tool_calls=[
            ToolCall(id="1", name="___current_weather", arguments=json.dumps({"city": "서울"}))
        ],
    )

    evidence, trace = await deliberate_and_run(
        db,
        provider,
        settings=settings_with(),
        question="서울 날씨 어때?",
        auto_tool_ids=list(catalogue.values()),
        model="gpt-4o",
    )
    assert evidence == ["EVIDENCE"]
    assert trace["called"] == ["날씨/current_weather"]
    calls = executed.call_args.args[0]
    assert len(calls) == 1
    assert calls[0].server_name == "날씨"
    assert calls[0].tool_name == "current_weather"
    assert calls[0].arguments == {"city": "서울"}
    assert calls[0].risk_level == "read"


async def test_more_calls_than_the_ceiling_are_capped(db, catalogue, provider, monkeypatch):
    executed = AsyncMock(return_value=[])
    monkeypatch.setattr(auto, "run_tool_calls", executed)
    provider.chat.return_value = ChatResult(
        content="",
        tool_calls=[
            ToolCall(id=str(i), name="___current_weather", arguments="{}") for i in range(9)
        ],
    )
    await deliberate_and_run(
        db,
        provider,
        settings=settings_with(max_tool_calls_per_message=2),
        question="q",
        auto_tool_ids=[catalogue["read"]],
        model="gpt-4o",
    )
    assert len(executed.call_args.args[0]) == 2


async def test_stale_ids_mean_no_deliberation_at_all(db, provider):
    """브라우저에 남은 낡은 id는 정상 상태다 - 조용히 걸러지고, 남는 도구가
    없으면 모델 호출 자체가 없다(비용 0)."""
    evidence, trace = await deliberate_and_run(
        db,
        provider,
        settings=settings_with(),
        question="q",
        auto_tool_ids=[uuid.uuid4()],
        model="gpt-4o",
    )
    assert (evidence, trace) == ([], None)
    provider.chat.assert_not_called()


async def test_failures_degrade_to_no_extra_evidence(db, catalogue, provider):
    # 숙고 호출이 죽어도 답변은 나간다.
    provider.chat.side_effect = RuntimeError("provider down")
    evidence, trace = await deliberate_and_run(
        db,
        provider,
        settings=settings_with(),
        question="q",
        auto_tool_ids=[catalogue["read"]],
        model="gpt-4o",
    )
    assert evidence == []
    assert trace["error"] == "deliberation_failed"

    # 모델이 JSON도 아닌 인자나 모르는 이름을 적어도 마찬가지다.
    provider.chat.side_effect = None
    provider.chat.return_value = ChatResult(
        content="",
        tool_calls=[
            ToolCall(id="1", name="___current_weather", arguments="not json"),
            ToolCall(id="2", name="invented_tool", arguments="{}"),
        ],
    )
    evidence, trace = await deliberate_and_run(
        db,
        provider,
        settings=settings_with(),
        question="q",
        auto_tool_ids=[catalogue["read"]],
        model="gpt-4o",
    )
    assert evidence == []
    assert trace["called"] == []
