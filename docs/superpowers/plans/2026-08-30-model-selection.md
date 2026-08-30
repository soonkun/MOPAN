# MOPAN Answer-Model Selection — Implementation Plan

> **Scope:** the answer model becomes a per-question choice, from an admin-controlled allowlist, in the backend and in the composer. It does not touch prompt administration (`backend/app/chat/prompt.py`, the `prompts` table and `/prompts`), which shipped in parallel under its own plan.

**Why this exists, measured rather than argued.** The owner asked a real question against the real 854-page Korean examination manual and got the OPPOSITE of what the document says. Retrieval was fine: the decisive sentence was in the evidence. Same evidence, same prompt, two models:

```text
decisive sentence is in evidence slot 8

gpt-4o-mini  "...공지예외주장을 할 수 없습니다."       citations: 0   INVERTED
gpt-4o       "네, 가능합니다. ... 인정할 수 있습니다[8]"  citations: 1   CORRECT
```

The source reads `선출원시 공지예외주장을 하지 않았더라도 ... 공지예외주장을 하였다면 ... 그 공지예외주장을 인정하도록 한다`. The cheap model reads Korean legal double negatives backwards. So the model is not a preference: it is the difference between a right answer and a wrong one, and the user has to be able to choose it per question.

**What ships:**
- `ANSWER_MODELS`, an admin allowlist. `POST /api/chat` takes an optional `model` and refuses anything not on it, in Korean, **before** any conversation or message row is written.
- `GET /api/models` — id, human label, and which is the default. Readable by any authenticated user.
- `Message.model` is populated and returned on `MessageResponse`, so a reloaded conversation says which model answered.
- A model picker in the composer: a menu anchored above the trigger on desktop, a bottom sheet on a phone. The choice persists in `localStorage` and rides every send.

## Decisions

**The allowlist is a cost boundary as much as a correctness one.** The operator pays per call and gpt-4o is many times the price of gpt-4o-mini, so a model string from a browser must never reach the provider. That is why the check is the FIRST thing `POST /api/chat` does — ahead of loading attachments, ahead of creating the conversation — and why it answers 400 rather than silently falling back to the default. A silent fallback would bill the owner for gpt-4o on every forged body.

**`selectable_models` is a property, not a value normalised at boot.** `Settings.model_copy(update=...)` does not re-run model validators, and both the test suite and `/api/search`'s `top_n` override use it. An allowlist frozen in `_finalise` would keep offering the model a copy had replaced. The property also guarantees `ANSWER_MODEL` is present and first, so a request that names no model can never be refused by its own default, and it drops blank entries so `ANSWER_MODELS=["gpt-4o",""]` cannot render an unselectable row.

**Vision is now asked per model, and there are two gates rather than one.** The old single derivation from `ANSWER_MODEL` would, with a per-request model, send an image to whichever model the operator happened to make the default and blind every other one. So: `POST /api/attachments` refuses an image only when NO model on the allowlist could read it — a text-only default must not block an upload meant for a vision model in the list — and `POST /api/chat` refuses an image sent WITH a model that cannot see it. The second gate is the one that keeps an image part away from a blind model, and it runs before any row is written for the same reason the allowlist check does. `ANSWER_MODEL_SUPPORTS_VISION` keeps its meaning: an override for the DEFAULT model only, which is the model it was written about.

**The persisted string is the provider's RESOLVED id.** `gpt-4o` comes back as `gpt-4o-2024-08-06`. That is what `ChatResult.model` already carried and what `persist_turn` already wrote; this plan only surfaces it. It names the exact snapshot, which is what a user comparing two answers actually wants, and it is the same string on the `done` frame and on the reloaded row so an answer is labelled identically before and after a refresh.

**`model` rides `**kwargs` into the provider rather than joining the `LLMProvider` ABC.** `OpenAIProvider.chat` builds its request as `{"model": self.answer_model, ..., **kwargs}`, so naming it at the call site overrides the construction-time default and no other implementation has to change. `answer()` omits it entirely when `None`, because a `model=None` would put a null on the wire.

**Radios, not buttons, in the picker.** Arrow-key navigation inside the group, the checked state announced, and one tab stop for the whole list all come from the platform. Two behaviours were found by driving it and are guarded by comments in the file: a radio runs its full activation behaviour for an ARROW key too, so `click` alone cannot tell browsing from choosing (`event.detail` can — a pointer press reports >= 1, a keyboard-synthesised click reports 0); and a button is activated by Enter on *keydown*, so a keyUP handler on the radio caught the same keystroke that opened the sheet and shut it again.

## Global Constraints

- Every user-facing `detail=` is natural Korean; `frontend/lib/api.ts:detailText` drops a detail with no Hangul.
- Design language: `docs/superpowers/specs/2026-08-30-design-language.md`. Tokens only — a raw hex or a Tailwind default-palette class is a defect, and the config no longer emits them.
- Native `<dialog>` + `showModal()` for the sheet, as `ConfirmDialog.tsx` already does: focus trap, Escape, inertness and top-layer stacking all come with it.
- The suite is serial-only. One pytest session at a time, never `-n auto`.
- `.env` is the operator's file; only `.env.example` is edited here.

---

### Task 1: The allowlist, the two vision gates, and `GET /api/models`

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/attachments/service.py`, `backend/app/attachments/router.py`, `backend/app/chat/router.py`, `backend/app/chat/service.py`, `.env.example`, `backend/tests/conftest.py`, `backend/tests/test_chat.py`, `backend/tests/test_chat_service.py`, `backend/tests/test_settings.py`
- Write: `backend/app/schemas/chat.py`

**Interfaces:**
- Produces: `Settings.answer_models`, `Settings.selectable_models`, `Settings.model_supports_vision`, `Settings.any_model_supports_vision`, `ChatRequest.model`, `MessageResponse.model`, `AnswerModelResponse`, `GET /api/models`, a `model` key on the `done` SSE frame.
- Consumed by: Task 2's composer, and every guard test in this task.

- [ ] **Step 1: Modify `backend/app/core/config.py`**

Labels first. Falling back to the id is not a degraded case - a label is a nicety for the picker, never a gate - so a model nobody thought to list is still selectable under its own name.

```python
# Display names for the ids an operator is likely to allow. Falling back to the
# id is not a degraded case - a label is a nicety for the picker, never a gate,
# so a model nobody thought to list here is still selectable under its own name.
MODEL_LABELS = {
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini",
    "gpt-4.1": "GPT-4.1",
    "gpt-4.1-mini": "GPT-4.1 mini",
    "gpt-5": "GPT-5",
}
```

- [ ] **Step 2: Modify `backend/app/core/config.py`**

The allowlist setting. Empty is the pre-existing behaviour: one model, no choice.

```python
    # The admin-controlled allowlist a user picks an answer model from. It is a
    # COST boundary as much as a correctness one - the operator pays per call and
    # gpt-4o is many times the price of gpt-4o-mini - so an arbitrary model string
    # from a client must never reach the provider. Read through
    # `selectable_models`, which always includes ANSWER_MODEL; leave this empty
    # and the picker offers exactly the default, which is the pre-existing
    # behaviour.
    answer_models: list[str] = []
```

- [ ] **Step 3: Modify `backend/app/core/config.py`**

The three readers. `selectable_models` is what both the router and `/api/models` ask; `model_supports_vision` is the per-model question the chat gate asks; `any_model_supports_vision` is the weaker question the upload gate asks.

```python
    @property
    def selectable_models(self) -> list[str]:
        """The allowlist as the app reads it. ANSWER_MODEL is always first and
        always present: it is what a request that names no model gets, so an
        allowlist that omitted it would refuse the default.

        A property rather than a normalisation in `_finalise` because
        `model_copy(update=...)` - which every test and `/api/search`'s top_n
        override uses - does not re-run model validators, and a list frozen at
        boot would then disagree with an overridden `answer_model`.
        """
        seen = dict.fromkeys([self.answer_model] + self.answer_models)
        return [model for model in seen if model.strip()]

    def model_supports_vision(self, model: str) -> bool:
        """Per MODEL, because the answer model is now a per-request choice: the
        old single-model derivation would send an image to whichever model the
        operator happened to make the default and blind the rest.

        ANSWER_MODEL_SUPPORTS_VISION stays an override for the DEFAULT model only
        - that is the model it was written about, and it exists for a local VLM
        whose name no prefix can recognise. Every other entry in the allowlist is
        derived from VISION_CAPABLE_MODEL_PREFIXES.
        """
        if model == self.answer_model:
            return bool(self.answer_model_supports_vision)
        return model.lower().startswith(VISION_CAPABLE_MODEL_PREFIXES)

    @property
    def any_model_supports_vision(self) -> bool:
        """What the UPLOAD gate asks. Storing an image is refused only when NO
        allowlisted model could ever look at it; whether the model the user
        actually picks can is settled at /api/chat, where the choice is known."""
        return any(self.model_supports_vision(model) for model in self.selectable_models)
```

- [ ] **Step 4: Modify `backend/app/attachments/service.py`**

The refusal sentence moves out of `attachments/router.py` so both gates can share it. To the user it is one refusal, so it must be one string.

```python
def no_vision_message(model: str) -> str:
    """Shared by both gates: POST /api/attachments refuses an image no allowlisted
    model could ever read, and POST /api/chat refuses one sent WITH a model that
    cannot read it. Same sentence, because to the user it is the same refusal."""
    return (
        f"현재 답변 모델({model})은 이미지를 읽을 수 없습니다. "
        "이미지 대신 문서 파일을 첨부하거나 관리자에게 문의해 주세요."
    )
```

- [ ] **Step 5: Modify `backend/app/attachments/router.py`**

The upload gate, widened from the default model to the whole allowlist.

```python
    kind = "image" if extension in IMAGE_EXTENSIONS else "document"
    # Refused here rather than at answer time, so the user is told while attaching
    # rather than after composing a whole message around a thumbnail.
    #
    # Against the WHOLE allowlist, not the default model: with a per-request model
    # the user may well pick a vision model for this very question, and gating on
    # ANSWER_MODEL alone would refuse the upload for a model they never chose. It
    # is no longer the check that makes an image part unable to reach a blind
    # model - POST /api/chat owns that, where the choice is actually known - it is
    # the early "no model here can see at all" one.
    if kind == "image" and not settings.any_model_supports_vision:
        raise HTTPException(status_code=400, detail=no_vision_message(settings.answer_model))
```

- [ ] **Step 6: Write `backend/app/schemas/chat.py`**

`model` on the request, `model` on the response, and the shape `GET /api/models` returns. Validation of the value stays in the router: a `Field(pattern=...)` would freeze operator configuration at import time and a 422 would answer in English.

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 7: Modify `backend/app/chat/router.py`**

The allowlist check, first in the handler. Note what is above it in the file: the same paragraph that explains why the attachment checks run before the conversation row.

```python
    # Both checks below run before the conversation is created, for the same
    # reason the ownership check does: a bad attachment id - or a model that is
    # not on the allowlist - must not leave a titled, empty conversation in the
    # sidebar that the user then has to delete by hand.
    #
    # The model FIRST, before the attachments are even loaded: an arbitrary model
    # string from a client must never reach the provider, because the operator
    # pays per call and this allowlist is the only thing standing between a forged
    # body and gpt-4o pricing - or a model that does not exist, whose 400 would
    # otherwise arrive as an error frame inside a 200 after the row was written.
    model = payload.model or settings.answer_model
    if model not in settings.selectable_models:
        raise HTTPException(status_code=400, detail=f"사용할 수 없는 답변 모델입니다: {model}")
```

- [ ] **Step 8: Modify `backend/app/chat/router.py`**

The chat-side vision gate, immediately after the images are read off disk and still before the conversation row.

```python
    # The upload gate only proved SOME allowlisted model can see. This is the one
    # that proves the model the user actually picked can - without it, choosing a
    # text-only model for a question with a screenshot in it sends an image part
    # to a blind model and gets an opaque provider 400 back inside a 200 stream.
    if images and not settings.model_supports_vision(model):
        raise HTTPException(status_code=400, detail=no_vision_message(model))
```

- [ ] **Step 9: Modify `backend/app/chat/router.py`**

The resolved model reaches `answer()`, and comes back on the `done` frame so the answer on screen is labelled without waiting for a reload.

```python
            chat_answer = await answer(
                llm_provider,
                payload.message,
                history,
                attachment_evidence + evidence,
                settings=settings,
                images=images,
                model=model,
            )

            # Phase 3: a fresh short session to persist the turn. `conversation` is
            # the ownership-checked object from above - detached, not expired - so
            # nothing here re-reads it by bare id.
            async with sessionmaker() as persist_db:
                await persist_turn(
                    persist_db,
                    conversation,
                    payload.message,
                    chat_answer,
                    retrieval_ms,
                    attachment_ids=attachment_ids,
                )

            yield _sse({"type": "citations", "citations": chat_answer.citations})
            yield _sse(
                {
                    "type": "done",
                    "conversation_id": str(conversation.id),
                    "content": chat_answer.content,
```

- [ ] **Step 10: Modify `backend/app/chat/router.py`**

The endpoint the picker lists from.

```python
@router.get("/models", response_model=list[AnswerModelResponse])
async def list_models(
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
):
    """What the composer's model picker lists. Any authenticated user may read it:
    it is the same allowlist POST /api/chat enforces, so it discloses nothing a
    user could not already learn by sending a model and being refused."""
    return [
        AnswerModelResponse(
            id=model,
            label=MODEL_LABELS.get(model, model),
            is_default=model == settings.answer_model,
        )
        for model in settings.selectable_models
    ]
```

- [ ] **Step 11: Modify `backend/app/chat/service.py`**

`answer()` takes the model as data, exactly like `images`: a string the caller has ALREADY validated, not a capability.

```python
    started = time.perf_counter()
    # tools=None in Slice 1; the parameter exists so Slice 2's MCP work does not
    # break the LLMProvider ABC.
    #
    # `model` rides **kwargs rather than joining the ABC signature:
    # OpenAIProvider.chat builds its request as {"model": self.answer_model, ...,
    # **kwargs}, so naming it here overrides the provider's construction-time
    # default and no other implementation has to change. Omitted entirely when
    # None - the caller wants the provider's own default, and model=None would
    # put a null on the wire. The router resolves it against the allowlist before
    # calling; this function is not the trust boundary and must not be used as one.
    result = await llm_provider.chat(messages, tools=None, **({"model": model} if model else {}))
```

- [ ] **Step 12: Modify `.env.example`**

The operator-facing documentation, including why the cheap model is offered but is not the default.

```text
# The default answer model: what a question sent without a model choice gets, and
# what ANSWER_MODEL_SUPPORTS_VISION below is about.
ANSWER_MODEL=gpt-4o
# The allowlist the composer's model picker offers and POST /api/chat enforces.
# ANSWER_MODEL is always in it and always first, so leaving this unset gives
# exactly the previous behaviour: one model, no choice.
#
# It is a COST boundary, not a preference. The operator pays per call and gpt-4o
# is many times the price of gpt-4o-mini, so a model string from a browser is
# refused with a Korean 400 unless it is listed here - before any conversation or
# message row is written.
#
# It is also a CORRECTNESS boundary, which is why the cheap model is not the
# default. Measured on the real 854-page Korean examination manual, same
# retrieval, same evidence, same prompt, the decisive sentence in evidence slot
# 8: gpt-4o-mini answered "공지예외주장을 할 수 없습니다" with 0 citations, the
# exact opposite of what the source says, while gpt-4o answered "네, 가능합니다
# ... 인정할 수 있습니다[8]". The cheap model reads Korean legal double negatives
# backwards. Offer it for speed and cost; do not make it the default.
ANSWER_MODELS=["gpt-4o","gpt-4o-mini"]
```

- [ ] **Step 13: Modify `.env.example`**

And the changed meaning of the vision override, next to the attachment settings it governs.

```text
# Whether the DEFAULT model (ANSWER_MODEL) can read images. Leave unset and it is
# derived from the model name; set it for a local VLM whose name no prefix
# recognises. It applies to ANSWER_MODEL only - every other entry in
# ANSWER_MODELS is derived from its name.
#
# Two gates use it. POST /api/attachments refuses an image only when NO model in
# ANSWER_MODELS could read it, so a text-only default no longer blocks an upload
# meant for a vision model in the list. POST /api/chat then refuses an image sent
# WITH a model that cannot see, which is the gate that actually keeps an image
# part away from a blind model.
# ANSWER_MODEL_SUPPORTS_VISION=true
```

- [ ] **Step 14: Modify `backend/tests/conftest.py`**

Pin the allowlist in the app fixture. The suite must not change because an operator added a model to `.env`.

```python
            "environment": "development",
            # Pinned for the same reason allow_self_registration is: the model
            # allowlist is a deployment decision, and an operator adding a model
            # to .env must not be able to change what the suite asserts. A test
            # that cares overrides it locally (tests/test_chat.py).
            "answer_model": "gpt-4o",
            "answer_models": [],
```

- [ ] **Step 15: Modify `backend/tests/test_chat.py`**

The guards. Each of the five was staged as failing before it was kept: the allowlist check removed, the same check moved below the conversation row, the `or settings.answer_model` default dropped, `MessageResponse.model` removed, and the chat-side vision gate removed.

```python
# --- Answer model selection --------------------------------------------------


@pytest.fixture
def two_models(app):
    """An allowlist with a second, cheaper model on it. The suite's default is one
    model - conftest pins ANSWER_MODELS empty, which is the behaviour that
    predates the picker - and this is the deployment the picker exists for."""
    app.state.settings = app.state.settings.model_copy(
        update={"answer_model": "gpt-4o", "answer_models": ["gpt-4o-mini"]}
    )
    return app.state.settings


def model_sent(fake_llm) -> str | None:
    return fake_llm.chat.await_args.kwargs.get("model")


async def test_a_model_outside_the_allowlist_is_refused_in_korean(logged_in, two_models):
    """The allowlist is a cost boundary before it is anything else: the operator
    pays per call, so a model string a browser invented must never reach the
    provider."""
    response = await logged_in.post("/api/chat", json={"message": "hi", "model": "gpt-4-turbo"})

    assert response.status_code == 400
    assert response.json()["detail"] == "사용할 수 없는 답변 모델입니다: gpt-4-turbo"


async def test_an_unallowed_model_writes_nothing_and_never_calls_the_provider(logged_in, fake_llm, db):
    """Refused BEFORE the conversation row, the same order attachment_ids follows:
    a rejected model must not leave a titled, empty conversation in the sidebar
    for the user to delete by hand - and must never be paid for."""
    response = await logged_in.post("/api/chat", json={"message": "hi", "model": "claude-opus-4"})

    assert response.status_code == 400
    assert (await logged_in.get("/api/conversations")).json() == []
    assert (await db.scalars(select(Message))).all() == []
    fake_llm.chat.assert_not_awaited()


async def test_no_model_in_the_body_means_the_default_model(logged_in, fake_llm, two_models):
    """ANSWER_MODEL is what a body with no `model` gets - every client that
    predates the picker, and the picker itself before its list has loaded."""
    response = await logged_in.post("/api/chat", json={"message": "hi"})

    assert response.status_code == 200
    assert model_sent(fake_llm) == "gpt-4o"


async def test_an_allowlisted_model_reaches_the_provider(logged_in, fake_llm, two_models):
    response = await logged_in.post("/api/chat", json={"message": "hi", "model": "gpt-4o-mini"})

    assert response.status_code == 200
    assert model_sent(fake_llm) == "gpt-4o-mini"


async def test_the_answer_model_survives_a_reload(logged_in, fake_llm, two_models):
    """The provider's RESOLVED id, on the `done` frame and on the persisted row
    alike, so an answer is labelled the same before and after a refresh. A user
    comparing two answers has no other way to tell which model gave which."""
    fake_llm.chat.return_value = ChatResult(content="answer", model="gpt-4o-mini-2024-07-18")

    response = await logged_in.post("/api/chat", json={"message": "hi", "model": "gpt-4o-mini"})
    done = parse_sse(response.text)[-1]
    assert done["model"] == "gpt-4o-mini-2024-07-18"

    messages = (await logged_in.get(f"/api/conversations/{done['conversation_id']}/messages")).json()
    assert [m["model"] for m in messages] == [None, "gpt-4o-mini-2024-07-18"]


async def test_an_image_with_a_text_only_model_is_refused_before_anything_is_written(
    logged_in, fake_llm, app, db
):
    """The upload gate only proves SOME allowlisted model can see - and here one
    can, so the PNG stores fine. This is the gate that proves the model the user
    actually picked can, and without it an image part reaches a blind model and
    comes back as an opaque provider error inside a 200."""
    app.state.settings = app.state.settings.model_copy(
        update={"answer_model": "gpt-4o", "answer_models": ["text-only-1"]}
    )
    attachment_id = await attach(logged_in, "shot.png", PNG_1X1, "image/png")

    response = await logged_in.post(
        "/api/chat",
        json={"message": "what is this?", "model": "text-only-1", "attachment_ids": [attachment_id]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "현재 답변 모델(text-only-1)은 이미지를 읽을 수 없습니다. "
        "이미지 대신 문서 파일을 첨부하거나 관리자에게 문의해 주세요."
    )
    assert (await logged_in.get("/api/conversations")).json() == []
    assert (await db.scalars(select(Message))).all() == []
    fake_llm.chat.assert_not_awaited()
    # The same image, the same allowlist, asked of the model that CAN see it.
    sent = await logged_in.post(
        "/api/chat",
        json={"message": "what is this?", "model": "gpt-4o", "attachment_ids": [attachment_id]},
    )
    assert sent.status_code == 200
```

- [ ] **Step 16: Modify `backend/tests/test_chat.py`**

And the endpoint's own tests.

```python
async def test_the_model_list_is_readable_by_any_authenticated_user(logged_in, two_models):
    """No admin gate: it is the same allowlist POST /api/chat enforces, so it
    discloses nothing a user could not learn by sending a model and being
    refused. The default is first, because it is what an unset picker sends."""
    response = await logged_in.get("/api/models")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "gpt-4o", "label": "GPT-4o", "is_default": True},
        {"id": "gpt-4o-mini", "label": "GPT-4o mini", "is_default": False},
    ]


async def test_the_model_list_needs_a_session(client):
    assert (await client.get("/api/models")).status_code == 401


async def test_a_model_with_no_label_is_still_offered_under_its_id(logged_in, app):
    """MODEL_LABELS is a nicety for the picker, never a gate - an operator can
    allowlist a local model nobody has written a label for."""
    app.state.settings = app.state.settings.model_copy(update={"answer_models": ["my-local-vlm"]})

    listed = (await logged_in.get("/api/models")).json()

    assert {"id": "my-local-vlm", "label": "my-local-vlm", "is_default": False} in listed
```

- [ ] **Step 17: Modify `backend/tests/test_settings.py`**

The settings-level properties, including the `model_copy` case that is the whole reason `selectable_models` is a property.

```python
def test_the_default_model_is_always_selectable_and_always_first():
    """A body with no `model` gets ANSWER_MODEL, so an allowlist that omitted it
    would refuse the default. A duplicate entry must not offer it twice either -
    the picker would render two identical rows."""
    assert Settings(answer_model="gpt-4o", answer_models=[]).selectable_models == ["gpt-4o"]
    assert Settings(answer_model="gpt-4o", answer_models=["gpt-4o-mini"]).selectable_models == [
        "gpt-4o",
        "gpt-4o-mini",
    ]
    assert Settings(answer_model="gpt-4o", answer_models=["gpt-4o-mini", "gpt-4o"]).selectable_models == [
        "gpt-4o",
        "gpt-4o-mini",
    ]
    # A stray empty entry - ANSWER_MODELS=["gpt-4o",""] - would otherwise be an
    # unselectable blank row that POST /api/chat still accepts.
    assert Settings(answer_model="gpt-4o", answer_models=["", "  "]).selectable_models == ["gpt-4o"]


def test_selectable_models_follows_an_overridden_answer_model():
    """model_copy(update=...) does not re-run model validators, which is why the
    allowlist is a property and not a value normalised at boot: a list frozen
    there would keep offering the model the copy replaced."""
    settings = Settings(answer_model="gpt-4o", answer_models=[])
    assert settings.model_copy(update={"answer_model": "text-only-1"}).selectable_models == ["text-only-1"]


def test_vision_is_asked_per_model_not_of_the_default_alone():
    """With a per-request model the old single-model derivation would blind every
    model but the default. The explicit override still applies to ANSWER_MODEL
    only - that is the model it was written about."""
    settings = Settings(
        answer_model="my-local-vlm",
        answer_model_supports_vision=True,
        answer_models=["gpt-4o", "o1-mini"],
    )
    assert settings.model_supports_vision("my-local-vlm") is True
    assert settings.model_supports_vision("gpt-4o") is True
    assert settings.model_supports_vision("o1-mini") is False
    assert settings.any_model_supports_vision is True
    # The upload gate: nothing on this allowlist could ever read an image.
    blind = Settings(answer_model="o1-mini", answer_models=["llama-3-8b-instruct"])
    assert blind.any_model_supports_vision is False
```

- [ ] **Step 18: Modify `backend/tests/test_chat_service.py`**

The signature guard, amended rather than deleted: `model` is data, and the property this test is about - no session, no retrieval collaborator - is unchanged.

```python
    # `images` is data, like `evidence`: chat attachments of kind 'image', already
    # read off disk by the caller. `model` is the same shape - a string the caller
    # has ALREADY validated against the allowlist, not a capability. Neither
    # carries a session or a retrieval collaborator, which is the property this
    # test is actually about.
    assert params == [
        "llm_provider",
        "question",
        "history",
        "evidence",
        "settings",
        "images",
        "model",
    ]
```

- [ ] **Step 19: Run the backend suite**

Own database, one session, never `-n auto`.

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://mopan:mopan@127.0.0.1:5432/mopan_test_model python -m pytest
ruff check .
```

---

### Task 2: The composer's model picker

**Files:**
- Write: `frontend/components/chat/ModelPicker.tsx`
- Modify: `frontend/lib/types.ts`, `frontend/lib/api.ts`, `frontend/components/chat/Composer.tsx`, `frontend/components/chat/ChatWindow.tsx`, `frontend/components/chat/MessageBubble.tsx`

**Interfaces:**
- Consumes: Task 1's `GET /api/models`, the `model` field on `POST /api/chat`, `MessageResponse.model` and the `done` frame's `model`.
- Produces: nothing the backend reads. The stored choice is validated against the fetched list on every mount, because an admin can remove a model from `ANSWER_MODELS` and a stale id would otherwise be refused on every send with a 400 the user cannot act on.

- [ ] **Step 1: Write `frontend/components/chat/ModelPicker.tsx`**

One native `<dialog>`, two placements. The composer is pinned to the bottom of the viewport, so a menu that opened DOWNWARD from its trigger would open off-screen: on desktop it is anchored above the trigger, on a phone it is a bottom sheet. Measured at 1280x900 the menu box lands at y=696 h=124 against a trigger top of 828 - above it - and at 390x844 the sheet is x=0 w=390 pinned to the bottom edge.

```tsx
"use client";

import { useRef, useState } from "react";
import type { AnswerModel } from "@/lib/types";

/** Which model answers the next question.
 *
 * ONE native <dialog>, two placements. showModal() is what buys the focus trap,
 * Escape, an inert background and top-layer stacking - the same reasoning
 * ConfirmDialog.tsx gives - and none of it has to be written here. The
 * difference between the desktop menu and the mobile sheet is where the box
 * sits, which is CSS plus four inline properties, not a second component.
 *
 * The composer is pinned to the bottom of the viewport, so a menu that opened
 * DOWNWARD from its trigger would open off-screen. On desktop it is anchored
 * above the trigger; on a phone it is a bottom sheet, which is what the owner
 * asked for and what every phone keyboard-adjacent menu does, for the same
 * reason: the bottom of the screen is where the thumb is. */

// Must equal the `sm:w-60` below - the anchoring maths needs the number.
const MENU_WIDTH = 240;
const EDGE = 8;

export default function ModelPicker({
  models,
  value,
  onChange,
}: {
  models: AnswerModel[];
  value: string;
  onChange: (id: string) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  // Mirrors the dialog's open state purely so aria-expanded can be announced;
  // the <dialog> itself is the source of truth and `onClose` is what syncs it,
  // so Escape - which fires no click handler - cannot leave the two disagreeing.
  const [open, setOpen] = useState(false);

  const current = models.find((m) => m.id === value) ?? models[0];

  function openPicker() {
    const dialog = dialogRef.current;
    const trigger = triggerRef.current;
    if (!dialog || !trigger) return;
    if (window.matchMedia("(min-width: 640px)").matches) {
      const rect = trigger.getBoundingClientRect();
      // Right-aligned to the trigger and clamped to the viewport, so the menu
      // cannot hang off either edge on a narrow desktop window.
      const left = Math.min(
        Math.max(EDGE, rect.right - MENU_WIDTH),
        window.innerWidth - MENU_WIDTH - EDGE,
      );
      dialog.style.left = `${left}px`;
      dialog.style.right = "auto";
      dialog.style.top = "auto";
      dialog.style.bottom = `${window.innerHeight - rect.top + EDGE}px`;
    } else {
      // Back to the class-driven bottom sheet. Without this a resize from
      // desktop to phone width would keep the anchored coordinates.
      dialog.style.cssText = "";
    }
    dialog.showModal();
    setOpen(true);
  }

  // Selecting and dismissing are deliberately two different events, and this was
  // found by driving it: with `onChange` closing the sheet, the first ArrowDown
  // a keyboard user pressed moved the selection AND shut the menu, so they could
  // never reach the third model. `change` fires on an arrow key, `click` does not
  // - it fires on a pointer press and on Space, which are the two gestures that
  // MEAN "this one". So change commits the choice and click is what closes.
  // Escape closes too, and keeps whatever the arrows landed on, because the
  // choice was already committed on the way past.

  if (models.length < 2) return null;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        // Same reason as the composer's + button: reaching for the model picker
        // is the user still composing, and a pointer press that moves focus off
        // the textarea dismisses the phone keyboard under them.
        onMouseDown={(event) => event.preventDefault()}
        onClick={openPicker}
        aria-haspopup="dialog"
        aria-expanded={open}
        // The name carries the CURRENT selection, because that is the question a
        // screen reader user has when they land on this control. The visible
        // label is hidden from AT so it is not read twice.
        aria-label={`답변 모델: ${current.label}`}
        className="inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full px-2 text-label text-on-surface-variant transition-colors duration-150 hover:bg-surface-container-high sm:px-3"
      >
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          className="h-4 w-4 shrink-0"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
        >
          <rect x="8" y="8" width="8" height="8" rx="1.5" />
          <path d="M10 4v3M14 4v3M10 17v3M14 17v3M4 10h3M4 14h3M17 10h3M17 14h3" />
        </svg>
        {/* The label costs ~70px of a 390px composer, where the + button, the
            textarea and 전송 all have to fit; the icon and the accessible name
            carry it there instead. */}
        <span aria-hidden="true" className="hidden max-w-[8rem] truncate sm:inline">
          {current.label}
        </span>
      </button>

      <dialog
        ref={dialogRef}
        aria-labelledby="model-picker-title"
        onClose={() => {
          setOpen(false);
          // Explicit, not left to the UA: focus has to land back on the control
          // the user opened, or a keyboard user is returned to the top of the
          // document with the composer behind them.
          triggerRef.current?.focus();
        }}
        // A transparent desktop backdrop still fills the viewport, so this is
        // what closes the menu on an outside click. `=== dialog` because every
        // click inside a child bubbles to the dialog too.
        onClick={(event) => {
          if (event.target === dialogRef.current) dialogRef.current.close();
        }}
        // Mobile: a bottom sheet pinned to the bottom edge, full width, rounded
        // on top only. Desktop: 240px anchored above the trigger by openPicker,
        // and no scrim - it is a menu, not a modal, whatever showModal() calls it.
        className="fixed inset-x-0 bottom-0 top-auto m-0 w-full max-w-none rounded-t-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim sm:w-60 sm:rounded-md sm:shadow-menu sm:backdrop:bg-transparent"
      >
        {/* Radios, not buttons: arrow-key navigation inside the group, the
            checked state announced, and one tab stop for the whole list all
            come from the platform. pb-6 is the phone's home indicator. */}
        <fieldset className="border-0 p-2 pb-6 sm:pb-2">
          <legend id="model-picker-title" className="px-3 py-2 text-label font-medium text-on-surface-variant">
            답변 모델
          </legend>
          {models.map((model) => (
            <label
              key={model.id}
              className="flex cursor-pointer items-center gap-3 rounded-md px-3 py-3 transition-colors duration-150 hover:bg-surface-container-high has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-primary sm:py-2"
            >
              <input
                type="radio"
                name="answer-model"
                value={model.id}
                checked={model.id === value}
                onChange={() => onChange(model.id)}
                // `detail` is the click count. A radio runs its full activation
                // behaviour for an ARROW key too - measured, the sheet closed on
                // the first ArrowDown - so `click` on its own cannot tell
                // browsing from choosing. A pointer press reports detail >= 1;
                // every keyboard-synthesised click reports 0.
                onClick={(event) => {
                  if (event.detail > 0) dialogRef.current?.close();
                }}
                // The keyboard half: Space and Enter mean "this one", arrows
                // mean "show me the next one".
                //
                // keyDOWN, not keyup, and this was measured too. A button is
                // activated by Enter on keydown, so opening the sheet with Enter
                // moved focus onto this radio in time for the SAME press's keyup
                // to land here and close it again - the sheet flickered open and
                // shut on one keystroke. A keydown belongs to whatever had focus
                // when the key went down, which is the distinction that fixes it.
                onKeyDown={(event) => {
                  if (event.key === " " || event.key === "Enter") dialogRef.current?.close();
                }}
                className="sr-only"
              />
              <span aria-hidden="true" className="h-4 w-4 shrink-0 text-primary">
                {model.id === value && (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="m5 13 4 4L19 7" />
                  </svg>
                )}
              </span>
              <span className="min-w-0 flex-1 truncate text-body">{model.label}</span>
              {model.is_default && <span className="shrink-0 text-caption text-on-surface-variant">기본</span>}
            </label>
          ))}
        </fieldset>
      </dialog>
    </>
  );
}
```

- [ ] **Step 2: Modify `frontend/lib/types.ts`**

The list shape.

```typescript
/** GET /api/models - the admin's ANSWER_MODELS allowlist, which POST /api/chat
 * enforces. `label` falls back to the id server-side, so it is never empty. */
export interface AnswerModel {
  id: string;
  label: string;
  is_default: boolean;
}
```

- [ ] **Step 3: Modify `frontend/lib/types.ts`**

The per-message model, and the same field on the frame that ends a stream.

```typescript
  // The model that produced this answer, as the provider resolved it
  // ("gpt-4o-2024-08-06"). Null on every user turn, and on assistant turns
  // written before the model became a per-question choice.
  model: string | null;
  created_at: string;
}
```

- [ ] **Step 4: Modify `frontend/lib/types.ts`**



```typescript
  | {
      type: "done";
      conversation_id: string;
      content: string;
      citations: Citation[];
      model: string | null;
    }
```

- [ ] **Step 5: Modify `frontend/lib/api.ts`**

Optional on the wire, so a client that predates the picker is unchanged.

```typescript
    collection_ids?: string[];
    attachment_ids?: string[];
    /** One of GET /api/models. Omitted means the server's ANSWER_MODEL default,
     * which is what the composer sends before its list has loaded. Anything not
     * on the allowlist comes back as a Korean 400 before a row is written. */
    model?: string;
```

- [ ] **Step 6: Modify `frontend/components/chat/Composer.tsx`**

To the RIGHT of the input, where Gemini and ChatGPT put it, and before the send button so Tab order runs input -> model -> 전송.

```tsx
        {/* To the RIGHT of the input, where Gemini and ChatGPT put it, and
            before the send button so that Tab order runs input -> model ->
            전송: the model is a property of the message being sent, so it
            belongs on the way to sending it rather than after. */}
        <ModelPicker models={models} value={model} onChange={onModelChange} />
```

- [ ] **Step 7: Modify `frontend/components/chat/ChatWindow.tsx`**

The stored choice. A per-viewer convenience with no server-side meaning - the server re-checks the value on every request - so `localStorage` is the right home, and a browser that refuses to store it just starts on the default every time.

```tsx
// The chosen answer model, remembered across messages and across reloads. A
// per-viewer convenience with no server-side meaning - the server re-checks the
// value against its allowlist on every request - so localStorage is the right
// home for it, and a browser that refuses to store it just starts on the
// default every time.
const MODEL_STORAGE_KEY = "mopan.answer-model";
```

- [ ] **Step 8: Modify `frontend/components/chat/ChatWindow.tsx`**

State, and the one fetch. Deliberately not wired to `error`: a failure to load the list must not put a red banner over a conversation that answers perfectly well on the server's default.

```tsx
  const [models, setModels] = useState<AnswerModel[]>([]);
  // "" until GET /api/models has answered. A send in that window carries no
  // `model` at all, which the server reads as its own default - so the picker
  // failing to load costs the user the choice, never the answer.
  const [model, setModel] = useState("");
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The answer, repeated into an off-screen live region - see the markup below
  // for why the transcript itself cannot be the live region.
  const [announcement, setAnnouncement] = useState("");
  // A second region, for the things that are not the answer: an attachment
  // added or removed, and 복사됨. Separate because the answer's region is only
  // ever written on `done`, and mixing the two would re-announce an old answer
  // every time a file was attached.
  const [notice, setNotice] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Set only by 중지, so the shared AbortError catch below can tell "the user
  // pressed stop" from "this component unmounted mid-answer".
  const stoppedRef = useRef(false);
  // One controller per in-flight upload, so removing a chip that is still
  // uploading cancels its request instead of letting it land on a chip that no
  // longer exists.
  const uploadsRef = useRef(new Map<string, AbortController>());
  // Every blob: URL handed to a thumbnail, revoked on unmount. Without this a
  // session of attaching and removing images leaks one buffer per preview.
  const previewUrlsRef = useRef<string[]>([]);
  // dragenter/dragleave fire for every child element the pointer crosses, so a
  // plain boolean flickers off the moment the cursor moves over a message. The
  // depth counter is what makes the drop state survive the crossing.
  const dragDepth = useRef(0);

  // Abort an answer still in flight when this window stops being the one on
  // screen. Without it streamChat outlived the component and its closure kept
  // the old `router`: ask at /chat, click another conversation mid-answer, and
  // ~3.5s later the abandoned stream's `done` frame ran router.replace and
  // threw the browser onto a conversation the user never chose.
  //
  // Keyed on initialConversationId, not []: /chat/{a} -> /chat/{b} is the same
  // component in the same slot, so React re-renders it with a new prop rather
  // than unmounting it, and a []-keyed cleanup never runs for the case that
  // actually reproduced.
  useEffect(() => () => abortRef.current?.abort(), [initialConversationId]);

  useEffect(() => {
    const urls = previewUrlsRef.current;
    return () => urls.forEach((url) => URL.revokeObjectURL(url));
  }, []);

  // Once per mount, and deliberately not wired to `error`: the model list is a
  // convenience, and a failure to fetch it must not put a red banner over a
  // conversation that answers perfectly well on the server's default.
  //
  // localStorage is read HERE rather than in a useState initialiser: this
  // component is server-rendered, where `window` does not exist, and reading it
  // during render would also hydrate a different value than the server emitted.
  useEffect(() => {
    apiFetch<AnswerModel[]>("/api/models")
      .then((list) => {
        setModels(list);
        let stored: string | null = null;
        try {
          stored = localStorage.getItem(MODEL_STORAGE_KEY);
        } catch {
          // Private mode, or site data blocked. Fall through to the default.
        }
        // Validated against the list, not trusted: an admin can remove a model
        // from ANSWER_MODELS, and a stale id would then be refused on every
        // send with a 400 the user cannot act on.
        const fallback = list.find((m) => m.is_default)?.id ?? list[0]?.id ?? "";
        setModel(list.some((m) => m.id === stored) ? stored! : fallback);
      })
      .catch(() => setModels([]));
```

- [ ] **Step 9: Modify `frontend/components/chat/ChatWindow.tsx`**

The send, and the answer's label taken from the frame rather than from state - the user may well switch the picker while an answer is still streaming.

```tsx
          // Omitted, not sent empty, while the list is still loading: the
          // backend reads an absent `model` as ANSWER_MODEL and an unknown one
          // as a 400.
          ...(model ? { model } : {}),
```

- [ ] **Step 10: Modify `frontend/components/chat/ChatWindow.tsx`**



```tsx
                // From the frame, not from `model` state: the user may well
                // switch the picker while this answer is still streaming, and
                // the label has to name what actually answered.
                model: event.model,
```

- [ ] **Step 11: Modify `frontend/components/chat/ChatWindow.tsx`**

Choosing, announced through the notice region the composer already owns.

```tsx
  function chooseModel(id: string) {
    setModel(id);
    setNotice(`답변 모델을 ${models.find((m) => m.id === id)?.label ?? id}(으)로 바꿨습니다.`);
    try {
      localStorage.setItem(MODEL_STORAGE_KEY, id);
    } catch {
      // The choice still applies to this session; it just will not survive a
      // reload. Nothing to tell the user about.
    }
```

- [ ] **Step 12: Modify `frontend/components/chat/MessageBubble.tsx`**

Quiet, and directly under the citations, because it answers the same question they do: where did this come from.

```tsx
          {/* Quiet, and directly under the citations, because it answers the
              same question they do: where did this come from. A user comparing
              two answers to the same question has no other way to tell which
              model gave which - and this is the resolved provider id, so it
              names the exact snapshot, not just the family.
              Null on a user turn and on any answer written before the model
              became a per-question choice. */}
          {message.model && (
            <span className="min-w-0 truncate text-caption text-on-surface-variant">
              <span className="sr-only">답변 모델 </span>
              {message.model}
            </span>
          )}
        </div>
```

- [ ] **Step 13: Verify**

```bash
cd frontend && npx tsc --noEmit && npm run build && npm test
```

Then drive it: the picker at desktop width and the sheet at 390px in both themes, Tab from the input onto the trigger, Enter to open, arrows to browse without dismissing, Space to commit, Escape to close with focus back on the trigger, one question answered by each of two models with the model shown on each answer, the choice surviving a reload, and `{"model":"gpt-4-turbo"}` refused with `사용할 수 없는 답변 모델입니다: gpt-4-turbo`.
