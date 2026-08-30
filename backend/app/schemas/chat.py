import uuid
from datetime import datetime

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
    # Ids from GET /api/mcp/tools. The count ceiling is
    # MAX_TOOL_CALLS_PER_MESSAGE and is enforced in the router for the same two
    # reasons attachment_ids' is: it is operator configuration, and a
    # Field(max_length=...) would freeze it at import time and answer in English.
    tool_calls: list[ToolCallRequest] | None = None


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
