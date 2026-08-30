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
    evidence_count: int = 0
    included_count: int = 0


class TraceResponse(BaseModel):
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    created_at: datetime

    # Straight off the Message columns Slice 1 added for this.
    model: str | None = None
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
