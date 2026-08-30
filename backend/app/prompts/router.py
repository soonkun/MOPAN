import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.core.db import get_db_session
from app.core.logging import log_event
from app.models.prompt import Prompt
from app.models.user import User
from app.schemas.prompt import PromptResponse, PromptVersionCreate, PromptVersionResponse

logger = logging.getLogger("mopan.prompts")
router = APIRouter(prefix="/api", tags=["prompts"])

PROMPT_NOT_FOUND_MESSAGE = "프롬프트를 찾을 수 없습니다."
VERSION_NOT_FOUND_MESSAGE = "해당 버전을 찾을 수 없습니다."
# 400, not the 422 a Pydantic min_length would give: this is the one refusal an
# admin will actually hit, and it has to read like a sentence rather than like a
# validation dump. A blank system prompt is not a valid state - it would send the
# model an empty system message and strip every citation and anti-injection
# instruction in one save.
EMPTY_PROMPT_MESSAGE = "프롬프트 내용을 입력해 주세요. 빈 내용으로는 저장할 수 없습니다."


def _to_version_response(prompt: Prompt, email: str | None) -> PromptVersionResponse:
    return PromptVersionResponse(
        id=str(prompt.id),
        version=prompt.version,
        text=prompt.text,
        is_active=prompt.is_active,
        created_by_email=email,
        created_at=prompt.created_at,
    )


async def _versions_of(db: AsyncSession, name: str) -> list[tuple[Prompt, str | None]]:
    """Newest first. Outer join, because created_by is NULL on the row migration
    0004 seeded and would otherwise drop the oldest version off the history."""
    rows = await db.execute(
        select(Prompt, User.email)
        .outerjoin(User, User.id == Prompt.created_by)
        .where(Prompt.name == name)
        .order_by(Prompt.created_at.desc())
    )
    return [(prompt, email) for prompt, email in rows.all()]


@router.get("/prompts", response_model=list[PromptResponse])
async def list_prompts(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """One entry per prompt NAME, carrying the text that is live right now.

    Every version's text in one payload would be simpler to consume and would
    grow without bound as the owner iterates; the history is a second request
    that only the expanded row makes."""
    rows = (await db.scalars(select(Prompt).order_by(Prompt.name, Prompt.created_at))).all()
    by_name: dict[str, list[Prompt]] = {}
    for row in rows:
        by_name.setdefault(row.name, []).append(row)

    responses: list[PromptResponse] = []
    for name, versions in by_name.items():
        # The ACTIVE row, and only it - "the newest" is not the same thing the
        # moment an admin rolls back to version 1. Falling back to the newest
        # keeps the screen readable if the partial unique index is ever dropped;
        # it is not what get_prompt does, which is why the row also shows which
        # version is live.
        active = next((v for v in versions if v.is_active), versions[-1])
        responses.append(
            PromptResponse(
                name=name,
                version=active.version,
                text=active.text,
                version_count=len(versions),
                updated_at=active.created_at,
            )
        )
    return responses


@router.get("/prompts/{name}/versions", response_model=list[PromptVersionResponse])
async def list_prompt_versions(
    name: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    versions = await _versions_of(db, name)
    if not versions:
        raise HTTPException(status_code=404, detail=PROMPT_NOT_FOUND_MESSAGE)
    return [_to_version_response(prompt, email) for prompt, email in versions]


@router.post("/prompts/{name}/versions", response_model=PromptVersionResponse, status_code=201)
async def create_prompt_version(
    name: str,
    payload: PromptVersionCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """An edit INSERTs a new version and makes it active. It never overwrites.

    Message.prompt_version names the text an answer was produced from, so an
    UPDATE in place would rewrite history the moment the owner tries a change
    and wants it back."""
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail=EMPTY_PROMPT_MESSAGE)

    # FOR UPDATE over this name's rows before reading the highest version: two
    # admins saving at the same instant would otherwise both compute the same
    # next number, and the loser would hit uq_prompts_name_version as a 500. The
    # second saver now blocks, then sees the number the first one left behind.
    existing = (
        await db.scalars(select(Prompt).where(Prompt.name == name).with_for_update())
    ).all()
    if not existing:
        raise HTTPException(status_code=404, detail=PROMPT_NOT_FOUND_MESSAGE)

    # int(), not string ordering: "10" sorts before "9". A version this code did
    # not write is not expected, but it must not crash the save either.
    numbers = [int(v.version) for v in existing if v.version.isdigit()]
    next_version = str(max(numbers, default=len(existing)) + 1)

    # An explicit UPDATE, not `row.is_active = False` on the loaded objects: the
    # unit of work emits INSERTs before UPDATEs for a mapper, which would insert
    # the new active row while the old one is still active and trip
    # uq_prompts_name_active. Stated as two statements in this order, it cannot.
    await db.execute(update(Prompt).where(Prompt.name == name).values(is_active=False))
    db.add(
        Prompt(
            name=name,
            version=next_version,
            text=payload.text,
            is_active=True,
            created_by=admin.id,
        )
    )
    await db.commit()

    created = await db.scalar(
        select(Prompt).where(Prompt.name == name, Prompt.version == next_version)
    )
    log_event(
        logger,
        "prompt_version_created",
        prompt_name=name,
        prompt_version=next_version,
        admin_id=str(admin.id),
        chars=len(payload.text),
    )
    return _to_version_response(created, admin.email)


@router.post("/prompts/{name}/versions/{version}/activate", response_model=PromptVersionResponse)
async def activate_prompt_version(
    name: str,
    version: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    existing = (
        await db.scalars(select(Prompt).where(Prompt.name == name).with_for_update())
    ).all()
    if not existing:
        raise HTTPException(status_code=404, detail=PROMPT_NOT_FOUND_MESSAGE)
    target = next((v for v in existing if v.version == version), None)
    if target is None:
        raise HTTPException(status_code=404, detail=VERSION_NOT_FOUND_MESSAGE)

    # Two statements, in this order. Postgres checks a non-deferrable unique
    # index per ROW as an UPDATE walks them, so a single
    # `SET is_active = (version = :v)` would collide with the row that is still
    # active whenever it happens to reach the new one first.
    await db.execute(update(Prompt).where(Prompt.name == name).values(is_active=False))
    await db.execute(
        update(Prompt)
        .where(Prompt.name == name, Prompt.version == version)
        .values(is_active=True)
    )
    await db.commit()
    # The loaded `target` predates both UPDATEs; without this the response would
    # report the is_active it had when it was read.
    await db.refresh(target)

    log_event(
        logger,
        "prompt_version_activated",
        prompt_name=name,
        prompt_version=version,
        admin_id=str(admin.id),
    )
    author = await db.scalar(select(User.email).where(User.id == target.created_by))
    return _to_version_response(target, author)
