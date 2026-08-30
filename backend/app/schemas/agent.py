import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AgentCollectionRef(BaseModel):
    id: uuid.UUID
    name: str


class AgentToolRef(BaseModel):
    """A tool an agent carries, named the way the planner and the citations name
    it: `server/tool`. `risk_level` rides along because it is the one property an
    admin composing a read-only agent is actually choosing on."""

    id: uuid.UUID
    server_name: str
    name: str
    risk_level: str


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # A NAME from the prompt store. Checked against `prompts` in the router
    # rather than with a Literal here: the set of prompt names is data an admin
    # adds to, and a Literal would freeze it at import time.
    prompt_name: str = Field(default="answer_agent", min_length=1, max_length=100)
    # None means the deployment's ANSWER_MODEL. Validated against
    # Settings.selectable_models in the router, for the same reason
    # ChatRequest.model is: it is operator configuration, and a Field(pattern=...)
    # would freeze it at import time and answer in English.
    answer_model: str | None = Field(default=None, max_length=100)
    orchestrator: bool = False
    enabled: bool = True
    # EMPTY MEANS UNRESTRICTED, for both, and the screen says 전체 허용 rather
    # than 없음 beside an empty selection. See app/models/agent.py.
    collection_ids: list[uuid.UUID] = Field(default_factory=list)
    tool_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _stripped(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("에이전트 이름을 입력해 주세요.")
        return stripped

    @field_validator("description", "answer_model")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AgentUpdate(BaseModel):
    """PATCH semantics: an OMITTED field is left alone.

    The two lists are the exception that proves it - sending `collection_ids: []`
    means "unrestricted", which is a real state an admin has to be able to get
    back to, so they are replaced wholesale when present and untouched when
    absent."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    prompt_name: str | None = Field(default=None, min_length=1, max_length=100)
    answer_model: str | None = Field(default=None, max_length=100)
    orchestrator: bool | None = None
    enabled: bool | None = None
    collection_ids: list[uuid.UUID] | None = None
    tool_ids: list[uuid.UUID] | None = None

    @field_validator("name")
    @classmethod
    def _stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("에이전트 이름을 입력해 주세요.")
        return stripped

    @field_validator("description", "answer_model")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class AgentResponse(BaseModel):
    """The admin screen's row. Admin only, because it names the collections and
    the tools an agent may reach and that is the configuration itself."""

    id: uuid.UUID
    name: str
    description: str | None
    prompt_name: str
    answer_model: str | None
    orchestrator: bool
    enabled: bool
    collections: list[AgentCollectionRef] = []
    tools: list[AgentToolRef] = []
    created_by_email: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentOption(BaseModel):
    """GET /api/agents/selectable - what the composer's agent picker lists.

    Deliberately narrower than AgentResponse and deliberately readable by any
    authenticated user, exactly as GET /api/models and GET /api/mcp/tools are:
    it lists only what POST /api/chat would accept, so it discloses nothing a
    user could not learn by picking an agent and being answered. It carries no
    collection list and no tool list - those are the boundary, and enumerating a
    boundary is how you tell someone what to try next.
    """

    id: uuid.UUID
    name: str
    description: str | None
    # Shown so the composer can move its own model picker to the agent's model
    # when one is chosen. Null means "the deployment default", which is what the
    # picker already shows as 기본.
    answer_model: str | None
    # Shown because the composer's 슈퍼 에이전트 toggle is forced on for an agent
    # that carries it, and a control that ignores a click without saying why is
    # a bug report.
    orchestrator: bool
