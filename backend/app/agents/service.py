"""What an agent is at request time, and the boundary that enforces it.

THE TWO LISTS ARE PERMISSION BOUNDARIES, NOT HINTS. An admin who reads
"이 에이전트는 A 분류만 사용" on a screen has been told something, and the only
way that sentence is true is if the restriction lives where nothing routes
around it. So it lives here, in one object, and the two functions that decide
what a question may reach - `app/orchestrator/plan.py:load_available` and
`app/chat/service.py:retrieve` - both apply it themselves rather than trusting
the router to have narrowed first. Applying it twice is free: intersecting an
already-intersected set changes nothing.

The refusal posture is the orchestrator's, deliberately. A plan naming a tool
this agent does not carry is not filtered down to the steps that are allowed -
`load_available` never puts the tool in the catalogue, so `validate_plan` cannot
resolve the name and refuses the plan WHOLE and falls back to plain RAG. A model
that named one thing it may not touch has told you what its other choices are
worth.

EMPTY MEANS UNRESTRICTED, for both lists. That is what makes the default agent -
`DEFAULT_AGENT`, used when the request names none - identical to the app as it
behaved before agents existed, and it is why an empty `agents` table changes
nothing. It is the one rule here that could mislead an admin, so the admin
screen prints 전체 허용 beside an empty selection rather than 없음.
"""

import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent

AGENT_NOT_FOUND_MESSAGE = "에이전트를 찾을 수 없습니다."
AGENT_DISABLED_MESSAGE = "사용이 중지된 에이전트입니다. 관리자에게 문의해 주세요."
COLLECTION_NOT_ALLOWED_MESSAGE = "이 에이전트가 사용할 수 없는 분류입니다."
TOOL_NOT_ALLOWED_MESSAGE = "이 에이전트가 사용할 수 없는 도구입니다."

# The prompt an agent answers with unless it names another. Matches
# app/chat/service.py:answer's own default, which is what makes the default
# agent a no-op rather than a second code path.
DEFAULT_PROMPT_NAME = "answer_agent"


class AgentScopeError(ValueError):
    """The request asked for something outside this agent's boundary.

    A ValueError rather than an HTTPException so the boundary object stays
    usable off the request path (the executor, the tests, a future worker). The
    router turns it into a 400 with this message, which is already Korean.
    """


@dataclass(frozen=True)
class ResolvedAgent:
    """One agent, flattened to what a request needs, with no session attached.

    Detached on purpose, the same rule `app/mcp/client.py:MCPTarget` follows: it
    travels through the streaming generator and into the executor, both of which
    run with no database session open.
    """

    id: uuid.UUID | None = None
    # None is the default agent, and it is what lands in `messages.agent_name`:
    # NULL there means "the app answered as it always did".
    name: str | None = None
    prompt_name: str = DEFAULT_PROMPT_NAME
    answer_model: str | None = None
    orchestrator: bool = False
    # EMPTY = UNRESTRICTED for both. See the module docstring.
    collection_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    tool_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def scope_collections(self, requested: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        """The collections this question may actually search.

        None out means "no restriction" - which is what `hybrid_search` reads as
        every collection. A LIST out is a closed set, and an empty list is a
        closed set of nothing: `collection_ids=[]` renders as an IN () predicate
        that matches no row, so it returns no evidence rather than silently
        falling back to everything. That distinction is the whole guard; the
        `or None` that used to sit in the executor is exactly how it gets lost.

        Idempotent, so both the router and `load_available` can call it.
        """
        if not self.collection_ids:
            return requested
        if requested is None:
            return sorted(self.collection_ids, key=str)
        allowed = [c for c in requested if c in self.collection_ids]
        if not allowed:
            # Refused, not silently emptied. A question scoped to a collection
            # this agent cannot reach is a mistake worth a sentence; answering it
            # from nothing would look like the corpus had no answer.
            raise AgentScopeError(COLLECTION_NOT_ALLOWED_MESSAGE)
        return allowed

    def allows_tool(self, tool_id: uuid.UUID) -> bool:
        return not self.tool_ids or tool_id in self.tool_ids


# The agent a request gets when it names none: every field at the value the app
# used before agents existed. `answer()` already defaults to `answer_agent`, the
# orchestrator already defaults to off, and both sets are empty, so nothing about
# this object narrows anything.
DEFAULT_AGENT = ResolvedAgent()


def resolve(agent: Agent) -> ResolvedAgent:
    return ResolvedAgent(
        id=agent.id,
        name=agent.name,
        prompt_name=agent.prompt_name,
        answer_model=agent.answer_model,
        orchestrator=agent.orchestrator,
        collection_ids=frozenset(c.id for c in agent.collections),
        tool_ids=frozenset(t.id for t in agent.tools),
    )


async def load_agent(db: AsyncSession, agent_id: uuid.UUID | None) -> ResolvedAgent:
    """Resolve the agent a chat request named, or refuse.

    Called BEFORE the conversation is created and before the StreamingResponse
    begins, for the reason every other pre-flight check in that router is: once
    the status line is on the wire a refusal degrades into an error frame inside
    a 200, and a bad agent id must not leave a titled, empty conversation in the
    sidebar.
    """
    if agent_id is None:
        return DEFAULT_AGENT
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id))
    if agent is None:
        raise HTTPException(status_code=404, detail=AGENT_NOT_FOUND_MESSAGE)
    if not agent.enabled:
        # 409, not the 404 above: the row exists and an admin turned it off, so
        # there is nothing to conceal - only a state to explain. Same rule
        # app/mcp/service.py:load_tool_calls follows for a disabled tool.
        raise HTTPException(status_code=409, detail=AGENT_DISABLED_MESSAGE)
    return resolve(agent)
