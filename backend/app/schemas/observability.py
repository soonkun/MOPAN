import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

# Long enough for a sentence about what went wrong, short enough that the column
# is not a free-form document store. Trimmed, and an all-whitespace comment is
# normalised to "no comment" in the router rather than stored as spaces.
FeedbackComment = Annotated[str, StringConstraints(max_length=1000)]


class FeedbackRequest(BaseModel):
    """Body of PUT /api/messages/{id}/feedback. There is no message id in it -
    the path owns that - and no user id: it is always the caller."""

    rating: Literal["up", "down"]
    comment: FeedbackComment | None = None


class FeedbackResponse(BaseModel):
    rating: Literal["up", "down"]
    comment: str | None = None
    updated_at: datetime


class TraceEvidence(BaseModel):
    """One retrieved item, as it was at answer time.

    `included` is the field this whole screen exists for: false means the item
    was retrieved and then dropped because ANSWER_CONTEXT_TOKEN_BUDGET ran out
    before it, so the model never saw it.
    """

    index: int
    source_type: str
    ref: str
    chunk_id: str | None = None
    document_id: str | None = None
    filename: str | None = None
    page: int | None = None
    section: str | None = None
    # Null means the item was absent from that ranking entirely - a chunk the
    # keyword search never returned has no keyword_rank, and that is a fact worth
    # showing rather than a gap to fill with a zero.
    vector_rank: int | None = None
    keyword_rank: int | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    # What neighbour expansion merged into this item, one entry per neighbour:
    # chunk_id, chunk_index, offset (-1 before / +1 after), page, reason and
    # tokens. Empty for every item when NEIGHBOR_EXPANSION is off, and for every
    # trace written before it existed. `dict`, not a model: it is a record for a
    # human reading the screen, not a contract anything computes on.
    neighbors: list[dict] = Field(default_factory=list)
    score: float | None = None
    tokens: int = 0
    snippet: str = ""
    included: bool = True


class TraceRetrieval(BaseModel):
    """The knobs the answer was produced under, as they were AT THAT MOMENT.

    Recorded rather than read back from the current settings, because the whole
    point of the 고급 설정 screen is that these change - reading them live would
    make every old trace describe today's configuration.
    """

    top_n: int | None = None
    candidate_limit: int | None = None
    rrf_k: int | None = None
    sparse_weight: float | None = None
    token_budget: int | None = None
    # The system prompt's own cost, and the allowance it is charged against.
    # None on every trace written before the budget stopped bounding the whole
    # request - honestly different from 0, which would say the prompt was empty.
    prompt_tokens: int | None = None
    mandatory_allowance: int | None = None
    # None on every trace written before neighbour expansion existed, which is
    # honestly different from "off" - it means nobody recorded a value, not that
    # the value was off.
    neighbor_expansion: str | None = None
    evidence_count: int = 0
    included_count: int = 0


class TracePlanStep(BaseModel):
    """One NODE of a workflow graph, as it actually ran.

    `state` is the field worth reading: `done`, `failed` (recorded and the run
    carried on), `skipped` (the human refused it, or a branch did not select it),
    or `timeout` (the run's wall clock ran out before it started). `error`
    carries the Korean sentence that goes with the last three.
    """

    id: str
    kind: str
    label: str
    state: str
    query: str | None = None
    collections: list[str] = Field(default_factory=list)
    tool: str | None = None
    risk_level: str | None = None
    arguments: dict | None = None
    depends_on: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    ms: int = 0
    error: str | None = None
    # How deep this node ran. 0 is the graph the request named; 1 and above are
    # nodes inside a workflow another workflow called, which is the one thing a
    # flat step list could not otherwise show.
    depth: int = 0


class TracePlan(BaseModel):
    """The graph behind an answer, or the record that there was not one.

    **ONE SHAPE, whoever authored the graph.** `author` is the only field that
    differs between a 워크플로우 a person drew and one 슈퍼 에이전트 wrote, and it is
    a field rather than a second trace on purpose: two trace shapes would make
    "which one am I looking at" unanswerable on the screen.

    Absent entirely on the direct RAG path, which is still the default and still
    most answers. Present with `steps: []` and `refused` set when an author
    produced something the executor would not run, which is the case this screen
    exists to explain: the answer came from the direct path and the reason is a
    sentence, not a shrug.
    """

    # 사람 or 슈퍼 에이전트. None on every trace written before Slice 6.
    author: str | None = None
    # Which workflow, and which version of it. Both null when 슈퍼 에이전트 ran
    # without one selected.
    workflow_name: str | None = None
    workflow_version: int | None = None
    steps: list[TracePlanStep] = Field(default_factory=list)
    step_count: int = 0
    tool_step_count: int = 0
    timed_out: bool = False
    elapsed_ms: int = 0
    fell_back_to_direct_rag: bool = False
    refused: str | None = None
    budget_seconds: float | None = None
    max_steps: int | None = None
    max_nodes: int | None = None
    max_tool_calls: int | None = None
    max_depth: int | None = None
    approval_risk_level: str | None = None


class TraceResponse(BaseModel):
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    created_at: datetime

    # Straight off the Message columns Slice 1 added for this.
    model: str | None = None
    # WHICH WORKFLOW ANSWERED, and which version. Null when none was named and
    # for every answer written before workflows existed, which the screen renders
    # as 기본 - the same sentence, because they are the same fact.
    workflow_name: str | None = None
    workflow_version: int | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    latency_ms: int | None = None
    retrieval_ms: int | None = None
    usage: dict = Field(default_factory=dict)

    # From messages.trace. Empty for every answer written before migration 0005,
    # which the screen reports as "추적 정보가 없습니다" rather than as an error.
    has_trace: bool = False
    retrieval: TraceRetrieval = Field(default_factory=TraceRetrieval)
    evidence: list[TraceEvidence] = Field(default_factory=list)
    # None for every answer from the direct RAG path, which is still the default.
    # build_trace reserved this key and the column is JSONB, so Slice 3 needed no
    # migration to fill it.
    plan: TracePlan | None = None


class SettingResponse(BaseModel):
    """One runtime-safe setting. `value` is what the app is using right now;
    `env_value` is what it would fall back to if the override were removed, which
    is what makes 기본값으로 되돌리기 a promise the screen can keep."""

    key: str
    label: str
    help: str
    group: str
    kind: Literal["int", "float"]
    minimum: float
    maximum: float
    value: float
    env_value: float
    overridden: bool


class EnvOnlySettingResponse(BaseModel):
    """A value that is deliberately NOT editable here, with the reason. Served by
    the API rather than written into the screen so the reason lives beside the
    decision in app/core/settings_store.py."""

    key: str
    label: str
    reason: str


class SettingsResponse(BaseModel):
    settings: list[SettingResponse]
    env_only: list[EnvOnlySettingResponse]


class SettingUpdate(BaseModel):
    # A string, not a number: the form sends text, and the per-key parse in
    # SettingSpec is what turns it into an int or a float with a Korean message
    # when it will not. A `float` here would answer "6.5" for an integer setting
    # with a silent truncation, and a bad value with an English 422.
    value: str = Field(max_length=100)
