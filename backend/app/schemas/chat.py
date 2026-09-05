import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.observability import FeedbackResponse


class ToolCallRequest(BaseModel):
    """One MCP tool the USER picked for this turn.

    Slice 2 is manual invocation only: the model is never asked which tool to
    call, so this arrives from the composer's tool picker. Slice 3's orchestrator
    produces the same `PendingToolCall` objects from a plan instead, which is why
    app/mcp/service.py:run_tool_calls takes neither a request nor a session.

    `arguments` is a free-form object because its shape is the tool's own
    `input_schema`, which is discovered at runtime and cannot be a Pydantic
    model. It is not validated here: the MCP server owns that schema and answers
    a bad argument set with a JSON-RPC error, which becomes evidence saying the
    call failed rather than a 500.
    """

    tool_id: uuid.UUID
    arguments: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    collection_ids: list[uuid.UUID] | None = None
    # Ids from POST /api/attachments. The count ceiling is
    # MAX_ATTACHMENTS_PER_MESSAGE and is enforced in the router, not here: it is
    # operator configuration, and a Field(max_length=...) would freeze it at
    # import time and answer with an English 422 body instead of Korean.
    attachment_ids: list[uuid.UUID] | None = None
    # The answer model, chosen per question. Validated against
    # Settings.selectable_models in the router, not here, for the same two reasons
    # attachment_ids' ceiling is: it is operator configuration that a
    # Field(pattern=...) would freeze at import time, and a 422 would answer in
    # English. None means the default, ANSWER_MODEL.
    model: str | None = Field(default=None, max_length=100)
    # 추론 모델의 사고 깊이. None이면 프로바이더 기본값이고, 비추론 모델에
    # 실려 오면(모델을 바꾼 브라우저의 기억) 프로바이더가 조용히 버린다.
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    # 브라우저의 IANA 시간대(Intl.DateTimeFormat). "올해·내일"을 사용자의
    # 시계로 풀기 위한 것이고, 없거나 이상한 값은 DEFAULT_TIMEZONE으로 강등
    # (app/core/localtime.py) - 그래서 여기서는 길이만 본다.
    client_tz: str | None = Field(default=None, max_length=64)
    # Ids from GET /api/mcp/tools. The count ceiling is
    # MAX_TOOL_CALLS_PER_MESSAGE and is enforced in the router for the same two
    # reasons attachment_ids' is: it is operator configuration, and a
    # Field(max_length=...) would freeze it at import time and answer in English.
    tool_calls: list[ToolCallRequest] | None = None
    # 자동 사용이 켜진 도구들(+ 메뉴의 서버 토글이 브라우저에 기억하는 기본
    # 설정). 모델이 이 중에서 필요할 때 알아서 부른다 - app/mcp/auto.py.
    # 낡은 id가 섞여 있는 것이 정상이라(서버 삭제 등) 서버는 조용히 거른다.
    auto_tool_ids: list[uuid.UUID] | None = Field(default=None, max_length=64)
    # 슈퍼 에이전트, OPT-IN and defaulting to off, chosen per question
    # the way `model` is. The direct RAG path of Slice 1 stays the default until
    # the orchestrator measures better on scripts/eval_questions_ko.json: a
    # planner is a new failure mode, and making it mandatory on day one would put
    # two systems under every regression.
    #
    # It composes with everything above rather than replacing it: attachments
    # still ride the turn, and a tool the USER picked in `tool_calls` still runs
    # before the plan does. The planner decides what ELSE to reach for.
    orchestrator: bool = False
    # THE WORKFLOW, chosen per question the way `model` is - this is what the
    # composer's `@` menu puts in the request. None means no workflow: no prompt
    # override, no restriction and no graph, which is this app exactly as it
    # behaved before any of this existed, so an empty `workflows` table changes
    # nothing about this request.
    #
    # It does not merely supply defaults for the fields above it: the workflow's
    # collection and tool lists are a BOUNDARY, so `collection_ids` and
    # `tool_calls` are narrowed and refused against it server-side
    # (app/workflow/catalogue.py). Resolved in the router before the response
    # starts, like the model and the attachment ids, for the reason they are: a
    # refusal after a StreamingResponse has begun is an error frame inside a 200.
    #
    # With `orchestrator` also on, the model writes the graph and this row
    # supplies the boundary, the prompt and the answer model - 슈퍼 에이전트 is a
    # way of AUTHORING a graph, so a saved graph and an authored one never both
    # run on one turn.
    workflow_id: uuid.UUID | None = None


class ApprovalDecision(BaseModel):
    """Body of POST /api/chat/approve - the second request that resumes a plan
    paused on a high-risk step.

    A TOKEN AND A SECOND REQUEST, not a generator held open across the pause.
    The reasoning is in app/workflow/approval.py; the short version is that a
    held-open generator dies with the connection, and the pause is exactly when a
    user walks away. The token is opaque, single-use and owner-checked.

    There is no `message` here and no `conversation_id`: both are in the stored
    payload, and accepting either from the client would let a replay attach an
    approved tool call to a different question.
    """

    approval_token: str = Field(min_length=1, max_length=200)
    # False is not "cancel the answer" - it is "run the rest of the plan without
    # this step". The question is still worth answering from whatever else the
    # plan finds, which is the same rule a failed step follows.
    approved: bool


class AttachmentResponse(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    kind: str
    # The text itself is never returned: it is prompt input, sometimes megabytes,
    # and the composer only needs to know whether the file gave up anything.
    has_text: bool = Field(validation_alias="extracted_text")
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("has_text", mode="before")
    @classmethod
    def _has_text_from_extract(cls, value: object) -> bool:
        return bool(value)


class AnswerModelResponse(BaseModel):
    """One entry of GET /api/models. `label` falls back to the id, so a model an
    operator allowlists that MODEL_LABELS has never heard of still renders."""

    id: str
    label: str
    is_default: bool
    # 이 모델이 추론 수준(reasoning_effort)을 받는가. 화면이 조절 UI를 그릴지
    # 말지의 근거이고, 실제 강제는 프로바이더가 한다(비추론 모델의 effort는
    # 조용히 버려진다).
    reasoning: bool = False


class ConversationResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list[dict]
    # Empty on every assistant message and on any user turn sent without files.
    # A reloaded transcript has no other way to show what was attached.
    attachments: list[AttachmentResponse] = []
    # What actually answered - the provider's resolved id, so "gpt-4o" comes back
    # as "gpt-4o-2024-08-06". None on every user turn, and on assistant turns
    # written before the answer model became a per-request choice. Without it a
    # reloaded conversation cannot say which model gave which answer, which is the
    # whole point of being able to pick one.
    model: str | None = None
    # 어느 저장 프롬프트가 답했는가. smalltalk_agent면 검색 없이 답한
    # 대화형 응답이라, 화면이 근거-없음 경고를 붙이지 않는다.
    prompt_name: str | None = None
    # WHICH WORKFLOW ANSWERED, and which VERSION of it. Null on every user turn,
    # on every answer written before workflows existed, and on every answer given
    # without one - all three of which the transcript renders the same way,
    # because they are the same thing: the app answering as it always did.
    workflow_name: str | None = None
    workflow_version: int | None = None
    # The CALLER's own rating, null when they have not rated this answer. It
    # rides the transcript rather than a second request because the alternative
    # is one fetch per assistant message on every conversation open. Always the
    # caller's, because a conversation has exactly one reader.
    feedback: FeedbackResponse | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("feedback", mode="before")
    @classmethod
    def _first_feedback(cls, value: object) -> object:
        # Message.feedback is a relationship LIST (see the note on the model).
        # from_attributes hands it straight through, so this is what turns it
        # into the single rating the client renders.
        if isinstance(value, list):
            return value[0] if value else None
        return value
