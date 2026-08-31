import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class WorkflowCollectionRef(BaseModel):
    id: uuid.UUID
    name: str


class WorkflowToolRef(BaseModel):
    """A tool a workflow carries, named the way a graph node and a citation name
    it: `server/tool`. `risk_level` rides along because it is the one property an
    admin composing a read-only workflow is actually choosing on."""

    id: uuid.UUID
    server_name: str
    name: str
    risk_level: str


class WorkflowCreate(BaseModel):
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
    enabled: bool = True
    # EMPTY MEANS UNRESTRICTED, for both, and the screen says 전체 허용 rather than
    # 없음 beside an empty selection. See app/models/workflow.py.
    collection_ids: list[uuid.UUID] = Field(default_factory=list)
    tool_ids: list[uuid.UUID] = Field(default_factory=list)
    # OPTIONAL on create. A workflow with no graph is a workflow nobody can call
    # yet - it does not appear in the `@` menu and cannot be selected - which is
    # exactly right for the moment before somebody has drawn one. Omitted here
    # means the router seeds the same three-node graph migration 0010 wrote for
    # every converted row, so a new workflow is immediately runnable and the
    # canvas opens on something rather than a blank sheet.
    graph: dict | None = None

    @field_validator("name")
    @classmethod
    def _stripped(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("워크플로우 이름을 입력해 주세요.")
        return stripped

    @field_validator("description", "answer_model")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class WorkflowUpdate(BaseModel):
    """PATCH semantics: an OMITTED field is left alone.

    The two lists are the exception that proves it - sending `collection_ids: []`
    means "unrestricted", which is a real state an admin has to be able to get
    back to, so they are replaced wholesale when present and untouched when
    absent.

    THE GRAPH IS NOT HERE. A graph is saved by POSTing a version, because every
    save makes a version and a PATCH that silently created one would hide that.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    prompt_name: str | None = Field(default=None, min_length=1, max_length=100)
    answer_model: str | None = Field(default=None, max_length=100)
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
            raise ValueError("워크플로우 이름을 입력해 주세요.")
        return stripped

    @field_validator("description", "answer_model")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class WorkflowVersionCreate(BaseModel):
    """One save of the canvas.

    `graph` is the whole thing - `{"nodes": [...], "edges": [...]}` - and node
    coordinates ride on the nodes. It is validated by
    `app/workflow/graph.py:validate_graph` against this workflow's own catalogue,
    so a node naming a tool outside the allowed list, a cycle, a template
    reference or `kind: "llm"` is a Korean 400 HERE rather than a surprise on
    somebody's question.
    """

    graph: dict
    note: str | None = Field(default=None, max_length=500)


class WorkflowVersionResponse(BaseModel):
    id: uuid.UUID
    version: int
    is_active: bool
    graph: dict
    note: str | None = None
    created_by_email: str | None = None
    created_at: datetime


class WorkflowResponse(BaseModel):
    """The admin screen's row. Admin only, because it names the collections and
    the tools a workflow may reach and that is the configuration itself."""

    id: uuid.UUID
    name: str
    description: str | None
    prompt_name: str
    answer_model: str | None
    enabled: bool
    collections: list[WorkflowCollectionRef] = []
    tools: list[WorkflowToolRef] = []
    # The active version's number and its graph, so opening the canvas is one
    # request. Null when a workflow has no active version, which makes it
    # uncallable rather than broken.
    active_version: int | None = None
    graph: dict | None = None
    created_by_email: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowOption(BaseModel):
    """GET /api/workflows/selectable - what the composer's `@` menu lists.

    Deliberately narrower than WorkflowResponse and deliberately readable by any
    authenticated user, exactly as GET /api/models and GET /api/mcp/tools are: it
    lists only what POST /api/chat would accept, so it discloses nothing a user
    could not learn by picking a workflow and being answered. It carries no
    collection list and no tool list - those are the boundary, and enumerating a
    boundary is how you tell someone what to try next.
    """

    id: uuid.UUID
    name: str
    description: str | None
    # Shown so the composer can move its own model picker to the workflow's model
    # when one is chosen. Null means "the deployment default", which the picker
    # already shows as 기본.
    answer_model: str | None
    # How many nodes are in the graph it would run. The one number that tells a
    # user this is a procedure rather than a prompt swap, without naming what it
    # reaches.
    node_count: int = 0


class CallableToolResponse(BaseModel):
    """One entry of GET /api/tools - the `@` menu, which is ONE list because RAG,
    MCP and workflows are one interface.

    `ref` is what a graph node writes in its `tool` field, verbatim: `rag`,
    `mcp:서버/도구`, or `workflow:이름`. The composer puts it in a chip; the canvas
    puts it on a node. One namespace, one menu.
    """

    kind: str
    ref: str
    name: str
    description: str | None = None
    risk_level: str = "read"
    # RAG only: the collections this deployment has, so the canvas can offer them
    # on a search node. Empty for every other kind.
    collections: list[WorkflowCollectionRef] = []
