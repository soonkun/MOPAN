import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.chat.service import evidence_utilization
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.core.settings_store import (
    ENV_ONLY_SETTINGS,
    NOT_RUNTIME_SAFE_MESSAGE,
    RUNTIME_SAFE_SETTINGS,
    SettingSpec,
    load_overrides,
    validated_settings,
)
from app.models.app_setting import AppSetting
from app.models.conversation import Conversation
from app.models.feedback import MessageFeedback
from app.models.message import Message
from app.models.user import User
from app.schemas.observability import (
    EnvOnlySettingResponse,
    FeedbackRequest,
    FeedbackResponse,
    SettingResponse,
    SettingsResponse,
    SettingUpdate,
    TraceResponse,
)

logger = logging.getLogger("mopan.observability")
router = APIRouter(prefix="/api", tags=["observability"])

# The SAME message for "no such message", "someone else's message" and "a user
# turn, which has no trace" - the rule get_owned_conversation established, for
# the same reason: a 403 on the second case would confirm that an id someone
# guessed exists. There is deliberately no admin bypass; see the module note in
# the plan.
MESSAGE_NOT_FOUND_MESSAGE = "답변을 찾을 수 없습니다."


async def _owned_assistant_message(db: AsyncSession, message_id: uuid.UUID, user: User) -> Message:
    """One statement, joined through `conversations`, so ownership is a predicate
    the database applies rather than a check a caller can forget after loading
    the row by bare id."""
    message = await db.scalar(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == message_id,
            Message.role == "assistant",
            Conversation.user_id == user.id,
        )
    )
    if message is None:
        raise HTTPException(status_code=404, detail=MESSAGE_NOT_FOUND_MESSAGE)
    return message


@router.get("/messages/{message_id}/trace", response_model=TraceResponse)
async def get_trace(
    message_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Why this answer looks the way it does: every retrieved item with its
    per-stage ranks and scores, WHICH OF THEM THE TOKEN BUDGET CUT, the model,
    the prompt version and the timings.

    Owner-scoped exactly like the transcript it belongs to."""
    message = await _owned_assistant_message(db, message_id, user)
    trace = message.trace or {}
    return TraceResponse(
        message_id=message.id,
        conversation_id=message.conversation_id,
        created_at=message.created_at,
        model=message.model,
        workflow_name=message.workflow_name,
        workflow_version=message.workflow_version,
        prompt_name=message.prompt_name,
        prompt_version=message.prompt_version,
        latency_ms=message.latency_ms,
        retrieval_ms=message.retrieval_ms,
        usage=message.usage or {},
        # An answer written before 0005 has {} here and is not an error: the
        # screen says so and still shows the columns, which are real.
        has_trace=bool(trace.get("evidence") is not None),
        retrieval=trace.get("retrieval") or {},
        evidence=trace.get("evidence") or [],
        # None, not {}: "this answer had no plan" and "this answer had an empty
        # plan" are different facts and the screen says different things about
        # them. The direct path writes no key at all.
        plan=trace.get("plan"),
        # COMPUTED ON READ, not stored. Both numbers were already on the row -
        # `included_count` is what build_prompt reported putting in front of the
        # model, `citations` is what the answer actually referenced - so this
        # needed no column and it is true of answers written long before anyone
        # thought to ask. `included_count`, NOT `evidence_count`: the denominator
        # is what was sent, and an item the token budget cut is not one the model
        # declined to use. Missing on a pre-0005 trace, which reads as delivered=0
        # and so as no ratio at all rather than as a fabricated failure.
        utilization=evidence_utilization(
            (trace.get("retrieval") or {}).get("included_count") or 0,
            len(message.citations or []),
        ),
    )


@router.put("/messages/{message_id}/feedback", response_model=FeedbackResponse)
async def put_feedback(
    message_id: uuid.UUID,
    payload: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """One rating per user per message, changeable. PUT rather than POST because
    that is what it is: the same URL, written again, replaces what was there.

    ON CONFLICT rather than select-then-insert. The unique constraint is the rule;
    reading it first and then writing leaves a window between the two halves that
    a double click reaches, and the loser would be a 500 on a duplicate key.
    """
    message = await _owned_assistant_message(db, message_id, user)
    comment = (payload.comment or "").strip() or None
    row = (
        await db.execute(
            pg_insert(MessageFeedback)
            .values(
                id=uuid.uuid4(),
                message_id=message.id,
                user_id=user.id,
                rating=payload.rating,
                comment=comment,
            )
            .on_conflict_do_update(
                constraint="uq_message_feedback_message_user",
                # updated_at explicitly: `onupdate` is a SQLAlchemy ORM hook and
                # this is a Core statement, so without it a changed rating would
                # keep the timestamp of the original one.
                set_={"rating": payload.rating, "comment": comment, "updated_at": func.now()},
            )
            .returning(MessageFeedback.rating, MessageFeedback.comment, MessageFeedback.updated_at)
        )
    ).one()
    await db.commit()
    log_event(
        logger,
        "message_feedback_recorded",
        message_id=str(message.id),
        rating=payload.rating,
        has_comment=comment is not None,
    )
    return FeedbackResponse(rating=row.rating, comment=row.comment, updated_at=row.updated_at)


def _setting_response(spec: SettingSpec, effective: Settings, base: Settings) -> SettingResponse:
    return SettingResponse(
        key=spec.key,
        label=spec.label,
        help=spec.help,
        group=spec.group,
        kind="int" if spec.kind is int else "float",
        minimum=spec.minimum,
        maximum=spec.maximum,
        value=getattr(effective, spec.field),
        env_value=getattr(base, spec.field),
        overridden=getattr(effective, spec.field) != getattr(base, spec.field),
    )


@router.get("/settings", response_model=SettingsResponse)
async def list_settings(
    request: Request,
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_app_settings),
):
    """Only the keys in RUNTIME_SAFE_SETTINGS are enumerable here, which is why
    no secret can leak through this endpoint: OPENAI_API_KEY has no entry, so
    there is nothing to filter out and nothing a new key can be added to by
    accident. `env_value` comes from app.state.settings - the values the process
    booted with - so the screen can show what removing an override would restore.
    """
    base: Settings = request.app.state.settings
    return SettingsResponse(
        settings=[_setting_response(spec, settings, base) for spec in RUNTIME_SAFE_SETTINGS.values()],
        env_only=[
            EnvOnlySettingResponse(key=item.key, label=item.label, reason=item.reason)
            for item in ENV_ONLY_SETTINGS
        ],
    )


def _spec_or_400(key: str) -> SettingSpec:
    spec = RUNTIME_SAFE_SETTINGS.get(key)
    if spec is None:
        # 400, not 404: the key may well exist as a `.env` value, and saying "not
        # found" would be a lie that sends an admin looking for a typo. This is
        # the refusal OPENAI_API_KEY gets, whatever case it is written in - the
        # lookup is exact, and nothing outside the spec table is reachable.
        raise HTTPException(status_code=400, detail=NOT_RUNTIME_SAFE_MESSAGE.format(key=key))
    return spec


@router.put("/settings/{key}", response_model=SettingResponse)
async def put_setting(
    key: str,
    payload: SettingUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Validated against the FULL settings object, not just this key's own range:
    CHUNK_OVERLAP has to stay under CHUNK_SIZE, and checking one at a time would
    let an admin save a pair that boots fine and then breaks every ingestion."""
    spec = _spec_or_400(key)
    base: Settings = request.app.state.settings
    overrides = await load_overrides(db)
    overrides[key] = payload.value
    try:
        effective = validated_settings(base, overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.execute(
        pg_insert(AppSetting)
        .values(key=key, value=payload.value)
        .on_conflict_do_update(
            index_elements=["key"], set_={"value": payload.value, "updated_at": func.now()}
        )
    )
    await db.commit()
    log_event(
        logger,
        "app_setting_changed",
        key=key,
        value=payload.value,
        admin_id=str(admin.id),
    )
    return _setting_response(spec, effective, base)


@router.delete("/settings/{key}", response_model=SettingResponse)
async def delete_setting(
    key: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Removes the override so the key falls back to its `.env` value. Idempotent
    - deleting a key with no override is a 200 describing the environment value,
    because the state the caller asked for is the state that now holds."""
    spec = _spec_or_400(key)
    base: Settings = request.app.state.settings
    await db.execute(delete(AppSetting).where(AppSetting.key == key))
    await db.commit()
    log_event(logger, "app_setting_reset", key=key, admin_id=str(admin.id))
    return _setting_response(spec, base, base)
