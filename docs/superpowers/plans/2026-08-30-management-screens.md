# MOPAN Management Screens — Backend Implementation Plan

> **Scope:** backend only. The admin screens are built separately against the contract this plan ships. Slice 1's plan (`docs/superpowers/plans/2026-08-28-vertical-slice-1.md`) is frozen history and is not amended by this document.

**Goal:** Give the admin role a real management surface. Slice 1's design spec says "Collection 쓰기 = admin only" but shipped only `POST /api/collections` and `GET /api/collections` — there is no way to rename or remove a collection, and no user administration at all, so the permission model exists on paper and nowhere else.

**What ships:**
- `PATCH /api/collections/{id}` and `DELETE /api/collections/{id}`, admin only. The delete refuses while the collection still holds documents.
- `GET /api/users` and `PATCH /api/users/{id}`, admin only, with guards against an admin locking every admin out.
- `users.is_active`, enforced in both the login path and `get_current_user`, with live Redis sessions revoked the moment an account is deactivated.

**Spec:** `docs/superpowers/specs/2026-08-28-vertical-slice-1-design.md`

## Decisions

**The last collection IS deletable.** `register_user` seeds `일반` for the bootstrap admin only, so an admin who deletes their last collection has none — and with none they cannot upload. That state is one click from recovery: `POST /api/collections` is the same call the upload form's empty state already offers. A floor of one would instead make a single mis-named collection permanent for the life of the deployment, and would have to be explained in the UI. The delete guard that actually protects data is the document count, not a collection count.

**Duplicate collection names are refused, by a unique constraint.** The name is the only thing distinguishing one collection from another in the upload dropdown and the document table's 분류 column; two rows called `일반` leave an admin guessing which one they picked, and there is no second identifier on screen. `uq_collections_name` is added in `0002`, names are stripped in the Pydantic schema so trailing whitespace cannot walk around it, and both the create and the rename path translate the `IntegrityError` into a Korean 409. Case is deliberately NOT normalised: a `lower(name)` expression index is not something alembic's autogenerate comparison handles cleanly, and `test_orm_matches_migrated_schema` would report it as permanent drift.

**Sessions needed a reverse index.** `session:<id> -> user_id` cannot be searched by user without a full keyspace `SCAN` plus a `GET` per key — O(all sessions online) on a request an admin makes from a form. `user-sessions:<user_id>` is a Redis set of that user's session ids, written on login with the same TTL. Members may outlive the session keys they name; that is harmless, because revocation only issues `DELETE`s.

**Deactivation is checked twice.** `update_user` revokes the user's Redis sessions, and `get_current_user` re-checks `is_active` on every request. The second check is not redundant: a session created before `0002` ran, or one held by a process that has not been redeployed, would otherwise stay valid for its full 24-hour TTL.

**The last-admin check runs BEFORE the self-check.** The only way to reach it is a self-demotion — any other active-admin target implies the acting admin is a second one — and "you are the last admin, promote someone else first" names a cause the admin can act on, where "you cannot change yourself" would send them looking for another admin who does not exist.

## Global Constraints

Slice 1's Global Constraints all still apply. The ones this plan is most exposed to:

- Every user-facing `detail=` is natural Korean. `frontend/lib/api.ts:detailText` drops a `detail` with no Hangul in it and shows a generic fallback instead, so an English string is invisible to the user.
- The word for a collection in front of the user is **분류**, not 컬렉션 — that is what the documents screen's column header, upload label and filter already say. `0001`-era code that said 컬렉션 in a 404 is corrected here for that reason.
- Alembic only. `0001` is frozen now that Slice 1 has shipped, so this is a real fixup migration, `0002`. Both `upgrade()` and `downgrade()` must work: `tests/conftest.py:migrated_database` runs `downgrade base` at the start of every session, so a broken `downgrade()` breaks the entire suite.
- The suite is serial-only. One pytest session at a time, never `-n auto`.
- `compare_metadata` drift test stays green: every migration change has a matching ORM change.

---


### Task 1: `users.is_active`, a unique collection name, and migration 0002

**Files:**
- Modify: `backend/app/models/user.py`, `backend/app/models/collection.py`
- Create: `backend/alembic/versions/0002_user_is_active_and_collection_name_unique.py`

**Interfaces:**
- Produces: `User.is_active: bool` (NOT NULL, `server_default true`), `uq_collections_name`.
- Consumed by: every guard in Tasks 2–4, and `tests/test_schema.py:test_orm_matches_migrated_schema`, which fails if the ORM and the migration disagree on either.

- [ ] **Step 1: Write `backend/app/models/user.py`**

`server_default=text("true")` on both sides, not a Python-side default alone: `get_current_user` rejects `is_active=false`, so a NULL or false backfill would log out every existing session the moment `0002` runs.

```python
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

USER_ROLES = ("admin", "user")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Email is normalised to lowercase in the auth service; this makes the
        # invariant real at the database level too, so a raw INSERT cannot create
        # a case-variant duplicate.
        CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
        CheckConstraint("role in ('admin', 'user')", name="ck_users_role_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", server_default=text("'user'")
    )
    # Deactivation is the only way to take an account away: rows are never deleted
    # because documents.uploaded_by and collections.created_by are ON DELETE
    # RESTRICT, so a DELETE would either fail or take the shared corpus with it.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 2: Write `backend/app/models/collection.py`**

The `UniqueConstraint` has to be declared here as well as in the migration or `compare_metadata` reports it as drift on the next test run.

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        # The name is the ONLY thing that distinguishes one collection from
        # another in the upload dropdown and the document table's 분류 column -
        # two rows called 일반 leave an admin guessing which one they picked.
        # Names are stripped in app/schemas/collection.py so trailing whitespace
        # cannot walk around this. Case still can; that is deliberate, a
        # lower(name) expression index is not something alembic's autogenerate
        # comparison handles cleanly and the schema drift test would flag it.
        UniqueConstraint("name", name="uq_collections_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # RESTRICT: deleting a user must not silently delete a shared collection.
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 3: Write `backend/alembic/versions/0002_user_is_active_and_collection_name_unique.py`**

`downgrade()` drops both in reverse order. It is exercised on every pytest session, not only by `test_downgrade_then_upgrade_round_trips`.

```python
"""users.is_active and a unique collection name

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default, not just a Python-side default: every row that predates this
    # migration has to become active, and get_current_user rejects is_active=false,
    # so a NULL or false backfill would log out every existing session.
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_unique_constraint("uq_collections_name", "collections", ["name"])


def downgrade() -> None:
    op.drop_constraint("uq_collections_name", "collections", type_="unique")
    op.drop_column("users", "is_active")
```

---


### Task 2: Session revocation and `is_active` enforcement

**Files:**
- Modify: `backend/app/core/security.py`, `backend/app/auth/service.py`, `backend/app/auth/dependencies.py`

**Interfaces:**
- Produces: `USER_SESSIONS_KEY_PREFIX`, `async revoke_user_sessions(redis, user_id) -> int` (returns how many session keys were removed).
- `create_session` now also writes the reverse index; its signature is unchanged, so `app/auth/router.py` needs no edit.
- Consumed by: `app/users/router.py` (Task 4).

- [ ] **Step 1: Write `backend/app/core/security.py`**

`revoke_user_sessions` guards the empty case: Redis `DELETE` with no keys is an error, not a no-op.

```python
import secrets

import bcrypt
from redis.asyncio import Redis

SESSION_KEY_PREFIX = "session:"
# Reverse index: session:<id> stores a user id, which cannot be searched BY user
# without a full keyspace scan, and deactivating an account has to revoke every
# live session it already has. Redis SCAN over session:* plus a GET per key would
# be O(all sessions online) on a request an admin makes from a form.
USER_SESSIONS_KEY_PREFIX = "user-sessions:"
MIN_PASSWORD_LENGTH = 8
# bcrypt silently TRUNCATES at 72 bytes (verified against bcrypt 4.2.0: hashpw of
# a 73-byte password succeeds, and checkpw then matches any longer string sharing
# the first 72 bytes). It does not raise. So this limit has to be enforced here -
# do not delete the check in hash_password believing the library covers it.
MAX_PASSWORD_BYTES = 72

# Pre-computed hash of a value nobody will submit, used to burn the same CPU on
# the "no such user" branch as on a real verification.
_DUMMY_HASH = bcrypt.hashpw(b"mopan-dummy-password", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def dummy_verify() -> None:
    """Call on the user-not-found path to avoid a response-time oracle."""
    bcrypt.checkpw(b"mopan-dummy-password", _DUMMY_HASH.encode())


async def create_session(redis: Redis, user_id: str, ttl_seconds: int) -> str:
    # TTL is a parameter, not a get_settings() read: that accessor is lru_cached and
    # would ignore the live Settings on app.state. Callers pass get_app_settings().
    session_id = secrets.token_urlsafe(32)
    await redis.set(f"{SESSION_KEY_PREFIX}{session_id}", user_id, ex=ttl_seconds)
    # A member may outlive the session key it names (logout and expiry both leave
    # it behind). That is harmless: revoke_user_sessions only issues DELETEs, and
    # deleting an absent key is a no-op. The set's own TTL, refreshed on each
    # login, is what keeps it from growing without bound.
    index_key = f"{USER_SESSIONS_KEY_PREFIX}{user_id}"
    await redis.sadd(index_key, session_id)
    await redis.expire(index_key, ttl_seconds)
    return session_id


async def get_session_user_id(redis: Redis, session_id: str) -> str | None:
    return await redis.get(f"{SESSION_KEY_PREFIX}{session_id}")


async def delete_session(redis: Redis, session_id: str) -> None:
    await redis.delete(f"{SESSION_KEY_PREFIX}{session_id}")


async def revoke_user_sessions(redis: Redis, user_id: str) -> int:
    """Drop every live session for one user and return how many keys were removed.

    An account deactivated while it holds a session cookie is not deactivated:
    session_ttl_seconds is 24h by default, so without this the user keeps full
    access for up to a day after an admin has taken it away."""
    index_key = f"{USER_SESSIONS_KEY_PREFIX}{user_id}"
    session_ids = await redis.smembers(index_key)
    if session_ids:
        await redis.delete(*(f"{SESSION_KEY_PREFIX}{s}" for s in session_ids))
    await redis.delete(index_key)
    return len(session_ids)
```

- [ ] **Step 2: Write `backend/app/auth/service.py`**

The `is_active` check goes AFTER `verify_password` so the bcrypt cost is paid on this branch too, and reuses `AuthError` so the router renders the same "이메일 또는 비밀번호가 올바르지 않습니다." as a wrong password — a distinct "deactivated" message would confirm the address is registered.

```python
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import log_event
from app.core.security import dummy_verify, hash_password, verify_password
from app.models.collection import Collection
from app.models.user import User

logger = logging.getLogger("mopan.auth")

DEFAULT_COLLECTION_NAME = "일반"


class AuthError(Exception):
    """Raised for any failed registration or authentication. The message is
    intentionally generic - specific reasons leak account existence."""


async def register_user(db: AsyncSession, settings: Settings, email: str, password: str) -> User:
    email = email.strip().lower()
    user_count = await db.scalar(select(func.count()).select_from(User)) or 0

    # Outside production the first account bootstraps the system: it becomes admin
    # and gets a default collection, so `docker compose up` -> open browser ->
    # register works with no seeding step. In production that would be a land-grab -
    # an unauthenticated endpoint handing admin over the shared RAG corpus to
    # whoever POSTs first - so there the admin must come from scripts/create_admin.py.
    is_first_user = user_count == 0 and settings.environment != "production"
    if not is_first_user and not settings.allow_self_registration:
        raise AuthError("회원가입이 비활성화되어 있습니다.")

    existing = await db.scalar(select(User).where(User.email == email))
    if existing is not None:
        # Says nothing about the address: "already registered" hands an account
        # enumeration oracle to anyone who can POST a guess. It is deliberately
        # NOT the disabled-registration message above either - that one names a
        # real, checkable cause, and reusing it here would name a false one.
        log_event(logger, "register_duplicate_email")
        raise AuthError("회원가입을 완료하지 못했습니다.")

    user = User(
        email=email,
        password_hash=hash_password(password),
        role="admin" if is_first_user else "user",
    )
    db.add(user)
    await db.flush()

    if is_first_user:
        db.add(Collection(name=DEFAULT_COLLECTION_NAME, created_by=user.id))

    await db.commit()
    await db.refresh(user)
    log_event(logger, "user_registered", user_id=str(user.id), role=user.role)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    email = email.strip().lower()
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        dummy_verify()  # equalise response time with the "wrong password" path
        raise AuthError("invalid credentials")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid credentials")
    if not user.is_active:
        # Checked AFTER the password so the bcrypt cost is paid on this branch too,
        # and raised as the same AuthError the router renders as "이메일 또는
        # 비밀번호가 올바르지 않습니다.": a distinct "deactivated" message would
        # confirm to anyone guessing that the address is registered here.
        log_event(logger, "login_rejected_inactive", user_id=str(user.id))
        raise AuthError("inactive account")
    return user
```

- [ ] **Step 3: Write `backend/app/auth/dependencies.py`**

401 rather than 403: `frontend/lib/api.ts:redirectIfSessionGone` redirects to `/login` on 401 only, and there is nothing this session can usefully do where it is.

```python
import uuid

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.core.redis import get_redis
from app.core.security import get_session_user_id
from app.models.user import User

SESSION_COOKIE_NAME = "mopan_session"


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> User:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")

    user_id = await get_session_user_id(redis, session_id)
    if not user_id:
        raise HTTPException(status_code=401, detail="세션이 만료되었습니다. 다시 로그인해 주세요.")

    try:
        parsed_user_id = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="세션이 올바르지 않습니다. 다시 로그인해 주세요."
        ) from exc

    user = await db.get(User, parsed_user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    if not user.is_active:
        # Belt as well as the braces in update_user, which revokes this user's
        # Redis sessions on deactivation. A session created before 0002 ran, or
        # one written by a process that has not been redeployed yet, would
        # otherwise stay valid for its full TTL. 401 rather than 403 on purpose:
        # frontend/lib/api.ts redirects to /login on 401 only, and there is
        # nothing this session can usefully do while it stays where it is.
        raise HTTPException(status_code=401, detail="비활성화된 계정입니다. 관리자에게 문의해 주세요.")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate for every write to the shared RAG corpus and for Slice 4/5 admin
    surfaces. Anyone who can upload can poison every other user's answers."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    return user
```

---


### Task 3: Collection rename and delete

**Files:**
- Modify: `backend/app/schemas/collection.py`, `backend/app/documents/router.py`

**Interfaces:**
- Produces: `CollectionUpdate`, `CollectionName` (a `StringConstraints` alias that strips whitespace and bounds length), `PATCH /api/collections/{id}`, `DELETE /api/collections/{id}`.
- Produces the message constants the frontend's copy has to match: `COLLECTION_NOT_FOUND_MESSAGE`, `DUPLICATE_COLLECTION_MESSAGE`, and the interpolated document-count 409.

- [ ] **Step 1: Write `backend/app/schemas/collection.py`**

`exclude_unset` in the router is what makes an omitted field mean "leave it" and an explicit null mean "clear it" — the only way to empty a description. `name` is NOT NULL, so an explicit null there is a 422 and not an `IntegrityError` reported as the wrong 409.

```python
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints, field_validator

# strip_whitespace is what makes uq_collections_name mean something: without it
# "일반 " and "일반" are two different rows the admin cannot tell apart.
CollectionName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class CollectionCreate(BaseModel):
    name: CollectionName
    description: str | None = None


class CollectionUpdate(BaseModel):
    """PATCH body. The router dumps this with `exclude_unset=True`, so an omitted
    field means "leave it alone" and an explicit null means "clear it" - which is
    the only way to empty a description."""

    name: CollectionName | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _reject_explicit_null(cls, value: str | None) -> str:
        # Validators do not run on defaults, so an ABSENT name never arrives here.
        # An explicit null does, and collections.name is NOT NULL - left alone it
        # reaches the database as an IntegrityError, which the router reports as
        # the duplicate-name 409 and so names the wrong cause.
        if value is None:
            raise ValueError("name must not be null")
        return value


class CollectionResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Write `backend/app/documents/router.py`**

`db.get(..., with_for_update=True)` in the delete is load-bearing: inserting a document takes `FOR KEY SHARE` on the collections row it references, which conflicts with `FOR UPDATE`. Without it a concurrent upload commits between the count and the `DELETE`, and `ON DELETE CASCADE` then takes the row, its chunks and the admin's just-uploaded file with it, silently.

```python
import logging
import uuid

from anyio import to_thread
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authorization import get_readable_document
from app.auth.dependencies import get_current_user, require_admin
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.documents.service import enqueue_document_processing, get_arq_pool
from app.documents.storage import delete_document_files, save_upload_stream
from app.documents.validation import (
    MAGIC_SNIFF_BYTES,
    UploadTooLarge,
    UploadValidationError,
    validate_magic_bytes,
    validate_upload_metadata,
)
from app.models.chunk import Chunk
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.schemas.collection import CollectionCreate, CollectionResponse, CollectionUpdate
from app.schemas.document import BlockResponse, ChunkResponse, DocumentResponse

logger = logging.getLogger("mopan.documents")
router = APIRouter(prefix="/api", tags=["documents"])

ENQUEUE_FAILED_MESSAGE = "처리 작업을 큐에 등록하지 못했습니다. 잠시 후 다시 시도해 주세요."
# 분류, not 컬렉션: 분류 is the word the documents screen already puts in front of
# the user - the table column header, the upload label and the filter all say it.
# The management screen shows the same rows, so it has to say the same word.
COLLECTION_NOT_FOUND_MESSAGE = "분류를 찾을 수 없습니다."
DUPLICATE_COLLECTION_MESSAGE = "같은 이름의 분류가 이미 있습니다. 다른 이름을 입력해 주세요."


def _document_list_query():
    # chunk_count via a correlated subquery, not one extra SELECT per row.
    chunk_count = (
        select(func.count(Chunk.id))
        .where(Chunk.document_id == Document.id)
        .correlate(Document)
        .scalar_subquery()
    )
    return (
        select(Document, Collection.name, User.email, chunk_count)
        .join(Collection, Collection.id == Document.collection_id)
        .join(User, User.id == Document.uploaded_by)
    )


def _to_response(document, collection_name, uploader_email, chunk_count) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        collection_id=document.collection_id,
        collection_name=collection_name,
        filename=document.filename,
        file_type=document.file_type,
        size_bytes=document.size_bytes,
        status=document.status,
        error_message=document.error_message,
        uploader_email=uploader_email,
        chunk_count=chunk_count or 0,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@router.post("/collections", response_model=CollectionResponse)
async def create_collection(
    payload: CollectionCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    collection = Collection(name=payload.name, description=payload.description, created_by=admin.id)
    db.add(collection)
    try:
        await db.commit()
    except IntegrityError as exc:
        # uq_collections_name. Caught rather than pre-checked with a SELECT: the
        # check-then-insert version still loses to a concurrent insert and turns
        # into the same 500, just less often.
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_COLLECTION_MESSAGE) from exc
    await db.refresh(collection)
    return collection


@router.get("/collections", response_model=list[CollectionResponse])
async def list_collections(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.scalars(select(Collection).order_by(Collection.created_at))
    return list(result)


@router.patch("/collections/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: uuid.UUID,
    payload: CollectionUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail=COLLECTION_NOT_FOUND_MESSAGE)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(collection, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=DUPLICATE_COLLECTION_MESSAGE) from exc
    await db.refresh(collection)
    return collection


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    """Refuses while the collection still holds documents. The last remaining
    collection IS deletable: an admin left with none simply creates one, which is
    the same click the empty state already offers, whereas a floor of one would
    make a single mis-named collection permanent."""
    # FOR UPDATE, not a bare get: inserting a document takes FOR KEY SHARE on the
    # collections row it references, which conflicts with this lock. Without it a
    # concurrent upload commits between the count below and the DELETE, and
    # documents.collection_id is ON DELETE CASCADE with chunks cascading from
    # documents - so the row, its chunks and the admin's just-uploaded file (left
    # orphaned under upload_dir) all disappear with no error anywhere.
    collection = await db.get(Collection, collection_id, with_for_update=True)
    if collection is None:
        raise HTTPException(status_code=404, detail=COLLECTION_NOT_FOUND_MESSAGE)

    document_count = await db.scalar(
        select(func.count(Document.id)).where(Document.collection_id == collection_id)
    )
    if document_count:
        raise HTTPException(
            status_code=409,
            detail=(
                f"문서 {document_count}개가 들어 있는 분류는 삭제할 수 없습니다. "
                "먼저 문서를 삭제해 주세요."
            ),
        )

    await db.delete(collection)
    await db.commit()
    log_event(logger, "collection_deleted", collection_id=str(collection_id))


@router.post("/documents", response_model=DocumentResponse, status_code=202)
async def upload_document(
    collection_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    arq_pool: ArqRedis = Depends(get_arq_pool),
):
    collection = await db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail=COLLECTION_NOT_FOUND_MESSAGE)

    filename = (file.filename or "").strip()
    try:
        extension = validate_upload_metadata(
            filename, file.content_type or "", file.size or 0, settings.max_upload_size_mb
        )
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    head = await file.read(MAGIC_SNIFF_BYTES)
    try:
        validate_magic_bytes(extension, head)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await file.seek(0)

    document = Document(
        collection_id=collection_id,
        filename=filename[:500],
        file_type=extension,
        size_bytes=0,
        storage_path="",
        status="uploaded",
        uploaded_by=admin.id,
    )
    db.add(document)
    await db.flush()

    # MEMORY is bounded here, DISK is not. Starlette spools each multipart part to
    # a SpooledTemporaryFile(max_size=1MB) before this handler runs, and
    # save_upload_stream writes in CHUNK_BYTES pieces, so nothing ever holds the
    # whole body in RAM. But an oversized body is still written to the spool's temp
    # file in full before max_bytes can reject it. Capping that needs a body limit
    # on a proxy in front of this app, and there is none: Task 24 exposes the
    # stack with `cloudflared tunnel --url http://localhost:3000` straight to
    # Next, whose middlewareClientMaxBodySize only bounds its own rewrite hop. If
    # a reverse proxy is ever added, raise its limit (nginx: client_max_body_size)
    # to match settings.max_upload_size_mb.
    try:
        path, size = await save_upload_stream(
            settings.upload_dir,
            str(document.id),
            extension,
            file,
            max_bytes=settings.max_upload_size_mb * 1024 * 1024,
        )
    except UploadTooLarge as exc:
        await db.rollback()
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    document.storage_path = str(path)
    document.size_bytes = size
    await db.commit()

    try:
        await enqueue_document_processing(arq_pool, str(document.id))
    except Exception:
        # Never return success for a job that was silently dropped: the document
        # would sit at "uploaded" forever with no explanation. The stored file is
        # unreachable too - nothing will ever parse it - so drop it rather than
        # leak disk under a row that has no retry route in Slice 1.
        logger.exception("failed to enqueue document processing")
        document.status = "failed"
        document.error_message = ENQUEUE_FAILED_MESSAGE
        await db.commit()
        await delete_document_files(settings.upload_dir, str(document.id))
        await db.refresh(document)
        return JSONResponse(
            status_code=503,
            # `detail` as well as the document body: the client reads `detail` for
            # the banner text, and without it a 503 with a perfectly good Korean
            # error_message rendered as the browser's own "Service Unavailable".
            content={
                **jsonable_encoder(_to_response(document, collection.name, admin.email, 0)),
                "detail": ENQUEUE_FAILED_MESSAGE,
            },
        )

    await db.refresh(document)
    log_event(logger, "document_uploaded", document_id=str(document.id), size_bytes=size)
    return _to_response(document, collection.name, admin.email, 0)


@router.get("/documents", response_model=list[DocumentResponse])
async def list_documents(
    collection_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    query = _document_list_query().order_by(Document.created_at.desc())
    if collection_id is not None:
        query = query.where(Document.collection_id == collection_id)
    rows = (await db.execute(query)).all()
    return [_to_response(*row) for row in rows]


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    row = (await db.execute(_document_list_query().where(Document.id == document_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return _to_response(*row)


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkResponse])
async def list_chunks(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    await get_readable_document(db, document_id)
    result = await db.scalars(
        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
    )
    return list(result)


@router.get("/documents/{document_id}/structure", response_model=list[BlockResponse])
async def get_document_structure(
    document_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Left pane of the document detail view: the parsed original structure, so an
    admin can eyeball chunking quality against it. Re-parsed on demand (in a
    thread) rather than duplicating every document's text into a JSONB column."""
    # Imported here, not at module scope: app.rag.parsers lands in Task 8, and a
    # module-level import would stop app.main from importing at all until then.
    from app.rag.parsers import get_parser

    document = await get_readable_document(db, document_id)
    parser = get_parser(document.file_type)
    try:
        parsed = await to_thread.run_sync(parser.parse, document.storage_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="원본 파일을 더 이상 찾을 수 없습니다.") from exc
    return [
        BlockResponse(text=b.text, block_type=b.block_type, page=b.page, section=b.section)
        for b in parsed.blocks
    ]


@router.get("/chunks/{chunk_id}", response_model=ChunkResponse)
async def get_chunk(
    chunk_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Backs citation click-through: the modal shows the full chunk, not the
    300-character snippet."""
    chunk = await db.get(Chunk, chunk_id)
    if chunk is None:
        # Worded for where it is actually read: this detail renders inside the
        # chat citation modal, which is labelled 출처. 청크 is an internal word
        # the chat surface never uses anywhere else.
        raise HTTPException(status_code=404, detail="출처 내용을 불러올 수 없습니다.")
    return chunk


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    document = await get_readable_document(db, document_id)
    await db.delete(document)  # chunks cascade via ON DELETE CASCADE
    await db.commit()
    await delete_document_files(settings.upload_dir, str(document_id))
```

---


### Task 4: User management API

**Files:**
- Modify: `backend/app/schemas/auth.py`, `backend/app/main.py`
- Create: `backend/app/users/__init__.py`, `backend/app/users/router.py`

**Interfaces:**
- Produces: `AdminUserResponse`, `UserUpdate`, `GET /api/users`, `PATCH /api/users/{id}`.
- Produces the message constants the frontend's copy has to match: `USER_NOT_FOUND_MESSAGE`, `LAST_ADMIN_MESSAGE`, `SELF_ROLE_MESSAGE`, `SELF_DEACTIVATE_MESSAGE`.
- Consumes: `require_admin` (Task 5 of Slice 1), `revoke_user_sessions` (Task 2 here).

- [ ] **Step 1: Write `backend/app/schemas/auth.py`**

`AdminUserResponse` extends `UserResponse` rather than replacing it: `/api/auth/me` returns the narrow shape to every logged-in user, and `is_active`/`created_at` are for the admin screen.

```python
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import MAX_PASSWORD_BYTES, MIN_PASSWORD_LENGTH


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def _within_bcrypt_limit(cls, value: str) -> str:
        # NOT Field(max_length=...): that counts CHARACTERS, and bcrypt's limit is
        # BYTES. "가" * 72 is 72 characters but 216 bytes - it would pass schema
        # validation and then raise out of hash_password as a 500 instead of a 422.
        if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return value.strip().lower()


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str

    model_config = {"from_attributes": True}


class AdminUserResponse(UserResponse):
    """The user-management list. Kept separate from UserResponse, which is what
    /api/auth/me returns to every logged-in user - is_active and created_at are
    for the admin screen, not for advertising to the account itself."""

    is_active: bool
    created_at: datetime


class UserUpdate(BaseModel):
    """PATCH body, dumped with `exclude_unset=True, exclude_none=True`. Both
    columns are NOT NULL, so an explicit null can only mean "no change" here -
    unlike CollectionUpdate.description, where null is a real value."""

    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None
```

- [ ] **Step 2: Create `backend/app/users/__init__.py`**

Empty, like every other package marker in `app/`.

```python

```

- [ ] **Step 3: Write `backend/app/users/router.py`**

The active-admin count is taken `FOR UPDATE` and re-read under the lock. Without it, two admins demoting each other at the same instant both read a count of two, both commit, and leave the system with no administrator and no route back through the UI. Postgres re-evaluates the `WHERE` clause after taking each row lock, so the loser of the race sees the count the winner left behind.

```python
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.core.db import get_db_session
from app.core.logging import log_event
from app.core.redis import get_redis
from app.core.security import revoke_user_sessions
from app.models.user import User
from app.schemas.auth import AdminUserResponse, UserUpdate

logger = logging.getLogger("mopan.users")
router = APIRouter(prefix="/api", tags=["users"])

USER_NOT_FOUND_MESSAGE = "사용자를 찾을 수 없습니다."
LAST_ADMIN_MESSAGE = "마지막 관리자입니다. 다른 사용자를 관리자로 지정한 뒤에 변경해 주세요."
SELF_ROLE_MESSAGE = "자신의 권한은 변경할 수 없습니다. 다른 관리자에게 요청해 주세요."
SELF_DEACTIVATE_MESSAGE = "자신의 계정은 비활성화할 수 없습니다."


@router.get("/users", response_model=list[AdminUserResponse])
async def list_users(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.scalars(select(User).order_by(User.created_at))
    return list(result)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=USER_NOT_FOUND_MESSAGE)

    # exclude_none as well as exclude_unset: both columns are NOT NULL, so an
    # explicit null can only mean "no change" (see UserUpdate).
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    role = changes.get("role", user.role)
    is_active = changes.get("is_active", user.is_active)

    if user.role == "admin" and user.is_active and (role != "admin" or not is_active):
        # FOR UPDATE, and re-read under the lock: two admins demoting each other
        # at the same instant would otherwise both see a count of two, both
        # commit, and leave the system with no administrator at all - a state
        # nothing in the UI can undo. Postgres re-evaluates the WHERE clause
        # after taking each row lock, so the loser of the race sees the count the
        # winner left behind.
        #
        # This runs BEFORE the self-checks below deliberately. The only way to
        # reach it is a self-demotion (any OTHER active admin target implies the
        # acting admin is a second one), and "you are the last admin, promote
        # someone else first" names a cause the admin can act on, where "you
        # cannot change yourself" would leave them looking for another admin who
        # does not exist.
        active_admin_ids = set(
            (
                await db.scalars(
                    select(User.id)
                    .where(User.role == "admin", User.is_active.is_(True))
                    .with_for_update()
                )
            ).all()
        )
        if user.id in active_admin_ids and len(active_admin_ids) <= 1:
            raise HTTPException(status_code=409, detail=LAST_ADMIN_MESSAGE)

    if user.id == admin.id:
        if role != user.role:
            raise HTTPException(status_code=409, detail=SELF_ROLE_MESSAGE)
        if is_active != user.is_active:
            raise HTTPException(status_code=409, detail=SELF_DEACTIVATE_MESSAGE)

    for field, value in changes.items():
        setattr(user, field, value)
    await db.commit()

    if not user.is_active:
        # After the commit, so a rolled-back transaction cannot log a user out for
        # a change that never happened. A deactivated account holding a live
        # session cookie is not deactivated - see revoke_user_sessions.
        revoked = await revoke_user_sessions(redis, str(user.id))
        log_event(logger, "user_deactivated", user_id=str(user.id), sessions_revoked=revoked)

    await db.refresh(user)
    return user
```

- [ ] **Step 4: Write `backend/app/main.py`**

Router imports stay inside `create_app()`, matching the existing three.

```python
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db_session, make_engine, make_sessionmaker
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.redis import get_redis, make_redis

logger = logging.getLogger("mopan.app")

# pgvector-specific and deliberately NOT behind VectorStore: it inspects the
# Postgres catalog, which no remote backend has. Whoever adds Qdrant deletes this
# readiness check rather than reimplementing it - see app/retrieval/vector_store.py.
EMBEDDING_DIM_SQL = """
SELECT a.atttypmod
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
WHERE c.relname = 'chunks' AND a.attname = 'embedding'
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.environment)
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    app.state.settings = settings
    app.state.engine = make_engine(settings)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.redis = make_redis(settings)

    from app.documents.service import make_arq_pool
    from app.llm.openai_provider import OpenAIProvider

    app.state.arq_pool = await make_arq_pool(settings)
    # One provider for the whole process. Building an AsyncOpenAI per request
    # creates a fresh httpx pool and TLS handshake every time and never closes it.
    app.state.llm_provider = OpenAIProvider(
        api_key=settings.openai_api_key,
        embedding_model=settings.embedding_model,
        answer_model=settings.answer_model,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        batch_size=settings.embedding_batch_size,
        batch_chars=settings.embedding_batch_chars,
        embedding_dim=settings.embedding_dim,
    )
    try:
        yield
    finally:
        await app.state.llm_provider.aclose()
        await app.state.arq_pool.aclose()
        await app.state.redis.aclose()
        await app.state.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="MOPAN API", lifespan=lifespan)

    app.add_middleware(RequestContextMiddleware)
    # The browser normally reaches the API through the Next.js same-origin proxy,
    # so CORS is a fallback for direct backend access. Origins are configuration.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["content-type", "authorization"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default handler echoes the rejected value back under "input".
        # On /api/auth/register that value is the plaintext password. Drop it.
        errors = [{k: v for k, v in error.items() if k != "input"} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/ready")
    async def ready(
        request: Request,
        db: AsyncSession = Depends(get_db_session),
        redis: Redis = Depends(get_redis),
    ) -> dict[str, str]:
        try:
            await db.execute(text("SELECT 1"))
            await redis.ping()
            deployed_dim = await db.scalar(text(EMBEDDING_DIM_SQL))
        except Exception as exc:
            logger.exception("readiness check failed")
            raise HTTPException(status_code=503, detail="의존 서비스에 연결할 수 없습니다.") from exc

        # app.state, not get_settings(): the lifespan owns the live Settings and
        # tests swap it there. Reading the module-global ignores both.
        configured = request.app.state.settings.embedding_dim
        if deployed_dim is not None and deployed_dim != configured:
            raise HTTPException(
                status_code=503,
                # Korean like every other `detail=`. The frontend never calls this
                # endpoint and compose healthchecks /api/health, so nothing here
                # reaches the UI - but "no English in a detail" is a constraint the
                # API client leans on (frontend/lib/api.ts), and a constraint with a
                # standing exception is not one. EMBEDDING_DIM and chunks.embedding
                # stay as they are: they are the names the operator has to type.
                detail=(
                    f"EMBEDDING_DIM={configured}이(가) 배포된 chunks.embedding 차원"
                    f"({deployed_dim})과 다릅니다. 마이그레이션 후 다시 색인해 주세요."
                ),
            )
        return {"status": "ready"}

    from app.auth.router import router as auth_router
    from app.chat.router import router as chat_router
    from app.documents.router import router as documents_router
    from app.users.router import router as users_router

    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(documents_router)
    app.include_router(users_router)

    return app


app = create_app()
```

---


### Task 5: Tests

**Files:**
- Modify: `backend/tests/test_security.py`, `backend/tests/test_auth.py`, `backend/tests/test_documents_api.py`

**Interfaces:**
- Consumes everything above. No new test file and no new fixture convention: `member_client` in `test_auth.py` is the same second-cookie-jar pattern `test_documents_api.py` already uses.

**Every guard below was staged by commenting the guard out and confirming the named test fails:**

| Guard | Test that fails without it |
| --- | --- |
| Delete refuses while documents remain | `test_delete_refuses_while_the_collection_holds_documents` |
| `uq_collections_name` + the 409 translation | `test_duplicate_collection_name_is_refused_on_create_and_rename` |
| Last active admin cannot lose admin | `test_the_last_active_admin_cannot_lose_admin`, `test_a_deactivated_admin_does_not_count_towards_the_last_admin_check` |
| Admin cannot demote or deactivate themselves | `test_admin_cannot_demote_themselves`, `test_admin_cannot_deactivate_themselves` |
| Deactivation revokes Redis sessions | `test_deactivating_a_user_kills_their_live_session` |
| `get_current_user` rejects an inactive account | `test_a_session_that_predates_deactivation_is_rejected_by_get_current_user` |
| Login rejects an inactive account | `test_a_deactivated_user_cannot_log_in_and_the_message_hides_the_account` |

- [ ] **Step 1: Write `backend/tests/test_security.py`**

The empty-user case is its own test because Redis `DELETE` with zero keys raises.

```python
import fakeredis.aioredis
import pytest

from app.core.security import (
    MAX_PASSWORD_BYTES,
    SESSION_KEY_PREFIX,
    USER_SESSIONS_KEY_PREFIX,
    create_session,
    delete_session,
    dummy_verify,
    get_session_user_id,
    hash_password,
    revoke_user_sessions,
    verify_password,
)


def test_hash_and_verify_password():
    hashed = hash_password("correct-horse")
    assert hashed != "correct-horse"
    assert verify_password("correct-horse", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_verify_password_returns_false_for_a_corrupt_hash():
    # Must not raise: a malformed stored hash is a 401, not a 500.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_hash_password_rejects_passwords_over_the_bcrypt_limit():
    with pytest.raises(ValueError):
        hash_password("a" * (MAX_PASSWORD_BYTES + 1))


def test_dummy_verify_runs_without_error():
    # Used on the "user not found" path so login timing does not reveal which
    # email addresses exist.
    dummy_verify()


async def test_session_lifecycle():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session_id = await create_session(redis, "user-123", 3600)
    assert await get_session_user_id(redis, session_id) == "user-123"

    await delete_session(redis, session_id)
    assert await get_session_user_id(redis, session_id) is None
    await redis.aclose()


async def test_session_uses_the_ttl_it_was_given():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    session_id = await create_session(redis, "user-123", 1234)
    # Exact TTL, not just > 0: a regression to a literal would still be "> 0".
    assert await redis.ttl(f"{SESSION_KEY_PREFIX}{session_id}") == 1234
    # The reverse index has to expire too, or a set of dead session ids outlives
    # every session it names.
    assert await redis.ttl(f"{USER_SESSIONS_KEY_PREFIX}user-123") == 1234
    await redis.aclose()


async def test_revoke_user_sessions_drops_every_session_of_one_user_only():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    # Two devices for the same account: revoking one cookie is not a deactivation.
    first = await create_session(redis, "user-123", 3600)
    second = await create_session(redis, "user-123", 3600)
    other = await create_session(redis, "user-456", 3600)

    assert await revoke_user_sessions(redis, "user-123") == 2
    assert await get_session_user_id(redis, first) is None
    assert await get_session_user_id(redis, second) is None
    assert await get_session_user_id(redis, other) == "user-456"
    assert await redis.exists(f"{USER_SESSIONS_KEY_PREFIX}user-123") == 0
    await redis.aclose()


async def test_revoke_user_sessions_is_a_no_op_for_a_user_with_none():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    # DELETE with no keys is a Redis error, not a no-op, so the empty case needs
    # its own branch.
    assert await revoke_user_sessions(redis, "never-logged-in") == 0
    await redis.aclose()
```

- [ ] **Step 2: Write `backend/tests/test_auth.py`**

`two_admins` exists so the self-guards are reachable at all: with one admin the last-admin guard fires first, by design.

```python
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.auth.authorization import get_owned_conversation, get_readable_document
from app.auth.dependencies import require_admin
from app.core.security import SESSION_KEY_PREFIX, hash_password
from app.models.conversation import Conversation
from app.models.user import User

MISSING_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def admin_client(client):
    """The first registered user is the bootstrap admin."""
    registered = await client.post(
        "/api/auth/register", json={"email": "admin@example.com", "password": "pw123456"}
    )
    assert registered.status_code == 200
    logged_in = await client.post(
        "/api/auth/login", json={"email": "admin@example.com", "password": "pw123456"}
    )
    assert logged_in.status_code == 200
    return client


async def test_first_user_becomes_admin(client):
    response = await client.post(
        "/api/auth/register", json={"email": "first@example.com", "password": "pw123456"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


async def test_second_user_is_a_plain_user(admin_client):
    response = await admin_client.post(
        "/api/auth/register", json={"email": "second@example.com", "password": "pw123456"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "user"


async def test_register_login_me_logout(client):
    await client.post("/api/auth/register", json={"email": "a@example.com", "password": "pw123456"})
    login = await client.post("/api/auth/login", json={"email": "a@example.com", "password": "pw123456"})
    assert login.status_code == 200
    assert "mopan_session" in login.cookies

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"

    assert (await client.post("/api/auth/logout")).status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_deletes_the_redis_session(client, fake_redis):
    await client.post("/api/auth/register", json={"email": "b@example.com", "password": "pw123456"})
    login = await client.post("/api/auth/login", json={"email": "b@example.com", "password": "pw123456"})
    session_id = login.cookies["mopan_session"]
    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is not None

    await client.post("/api/auth/logout")
    # Re-read the key: clearing the cookie alone would leave this session valid.
    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is None


async def test_email_is_case_insensitive(client):
    await client.post("/api/auth/register", json={"email": "Mixed@Example.COM", "password": "pw123456"})
    login = await client.post("/api/auth/login", json={"email": "mixed@example.com", "password": "pw123456"})
    assert login.status_code == 200


async def test_duplicate_registration_does_not_confirm_the_account_exists(client):
    await client.post("/api/auth/register", json={"email": "c@example.com", "password": "pw123456"})
    duplicate = await client.post(
        "/api/auth/register", json={"email": "c@example.com", "password": "pw123456"}
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "회원가입을 완료하지 못했습니다."


async def test_short_password_is_rejected(client):
    response = await client.post("/api/auth/register", json={"email": "d@example.com", "password": "short"})
    assert response.status_code == 422


async def test_long_password_is_rejected_not_a_500(client):
    response = await client.post("/api/auth/register", json={"email": "e@example.com", "password": "a" * 200})
    assert response.status_code == 422


async def test_multibyte_password_over_72_bytes_is_422_not_500(client):
    # 72 characters, 216 bytes. Pydantic max_length counts CHARACTERS, so a
    # character limit lets this through and hash_password raises -> 500.
    password = "가" * 72
    assert len(password) <= 72 < len(password.encode("utf-8"))
    response = await client.post("/api/auth/register", json={"email": "g@example.com", "password": password})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "email,password",
    [
        ("h@example.com", "sh0rtpw"),  # too short
        ("h@example.com", "가" * 72),  # over 72 bytes
        ("not-an-email", "Zq7-marker-Pw!"),  # invalid email, valid password
        ("h@example.com", "Zq7-marker-Pw!" + "x" * 200),  # over 72 bytes, ascii
    ],
)
async def test_validation_errors_do_not_echo_the_password(client, email, password):
    # FastAPI's default handler returns the rejected value under "input"; on this
    # route that is the plaintext password.
    response = await client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 422
    assert password not in response.text


async def test_malformed_json_does_not_echo_the_password(client):
    # The raw body is the "input" for a JSON decode error, so it carries the password.
    secret = "Zq7-marker-Pw!"
    response = await client.post(
        "/api/auth/register",
        content=f'{{"email": "h@example.com", "password": "{secret}"',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert secret not in response.text


async def test_me_requires_auth(client):
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_wrong_password(client):
    await client.post("/api/auth/register", json={"email": "f@example.com", "password": "pw123456"})
    response = await client.post("/api/auth/login", json={"email": "f@example.com", "password": "nope"})
    assert response.status_code == 401


async def test_login_unknown_email_matches_the_wrong_password_response(client):
    # Exercises the dummy_verify() branch. Identical body to the wrong-password
    # case, so the response reveals nothing about which emails exist.
    response = await client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "nope"})
    assert response.status_code == 401
    assert response.json() == {"detail": "이메일 또는 비밀번호가 올바르지 않습니다."}


async def test_self_registration_can_be_disabled(app, client):
    """Settings must come from app.state.settings, not the lru_cached get_settings()."""
    await client.post("/api/auth/register", json={"email": "i@example.com", "password": "pw123456"})
    app.state.settings = app.state.settings.model_copy(update={"allow_self_registration": False})
    blocked = await client.post("/api/auth/register", json={"email": "j@example.com", "password": "pw123456"})
    assert blocked.status_code == 400


async def test_production_refuses_to_bootstrap_an_admin_by_registration(app, client):
    """In production /api/auth/register must not hand admin to whoever POSTs first -
    the admin comes from scripts/create_admin.py."""
    app.state.settings = app.state.settings.model_copy(
        update={"environment": "production", "allow_self_registration": False}
    )
    response = await client.post(
        "/api/auth/register", json={"email": "landgrab@example.com", "password": "pw123456"}
    )
    assert response.status_code == 400


async def test_require_admin_rejects_a_plain_user():
    plain = User(email="plain@example.com", password_hash="x", role="user")
    with pytest.raises(HTTPException) as exc:
        await require_admin(plain)
    assert exc.value.status_code == 403

    admin = User(email="admin@example.com", password_hash="x", role="admin")
    assert await require_admin(admin) is admin


async def test_conversation_of_another_user_is_404_not_403(db):
    owner = User(email="owner@example.com", password_hash=hash_password("pw123456"))
    other = User(email="other@example.com", password_hash=hash_password("pw123456"))
    db.add_all([owner, other])
    await db.flush()
    conversation = Conversation(user_id=owner.id)
    db.add(conversation)
    await db.commit()

    assert (await get_owned_conversation(db, conversation.id, owner)).id == conversation.id

    with pytest.raises(HTTPException) as not_owned:
        await get_owned_conversation(db, conversation.id, other)
    assert not_owned.value.status_code == 404

    with pytest.raises(HTTPException) as missing:
        await get_owned_conversation(db, uuid.uuid4(), owner)
    assert missing.value.status_code == 404


async def test_missing_document_is_404(db):
    with pytest.raises(HTTPException) as exc:
        await get_readable_document(db, uuid.uuid4())
    assert exc.value.status_code == 404


# --- user management (GET /api/users, PATCH /api/users/{id}) -------------------


async def _user_id(admin_client, email: str) -> str:
    listing = await admin_client.get("/api/users")
    assert listing.status_code == 200
    return next(u["id"] for u in listing.json() if u["email"] == email)


@pytest_asyncio.fixture
async def member_client(admin_client, app):
    """A second, non-admin account on its own cookie jar."""
    await admin_client.post(
        "/api/auth/register", json={"email": "member@example.com", "password": "pw123456"}
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post("/api/auth/login", json={"email": "member@example.com", "password": "pw123456"})
        yield ac


@pytest_asyncio.fixture
async def two_admins(admin_client):
    """admin@example.com plus a promoted second@example.com, so the self-guards
    are reachable without the last-admin guard firing first."""
    await admin_client.post(
        "/api/auth/register", json={"email": "second@example.com", "password": "pw123456"}
    )
    second_id = await _user_id(admin_client, "second@example.com")
    promoted = await admin_client.patch(f"/api/users/{second_id}", json={"role": "admin"})
    assert promoted.status_code == 200
    return admin_client


async def test_list_users_returns_the_admin_fields_sorted_by_created_at(admin_client):
    await admin_client.post(
        "/api/auth/register", json={"email": "later@example.com", "password": "pw123456"}
    )
    response = await admin_client.get("/api/users")
    assert response.status_code == 200
    body = response.json()
    assert [u["email"] for u in body] == ["admin@example.com", "later@example.com"]
    assert body[0]["role"] == "admin"
    assert body[1]["role"] == "user"
    assert all(u["is_active"] is True for u in body)
    assert set(body[0]) == {"id", "email", "role", "is_active", "created_at"}


async def test_user_management_is_admin_only(member_client, admin_client):
    member_id = await _user_id(admin_client, "member@example.com")
    assert (await member_client.get("/api/users")).status_code == 403
    patched = await member_client.patch(f"/api/users/{member_id}", json={"role": "admin"})
    assert patched.status_code == 403
    # The refusal must be real, not cosmetic.
    assert (await admin_client.get("/api/users")).json()[1]["role"] == "user"


async def test_unknown_user_id_is_404(admin_client):
    response = await admin_client.patch(f"/api/users/{MISSING_ID}", json={"role": "admin"})
    assert response.status_code == 404
    assert response.json()["detail"] == "사용자를 찾을 수 없습니다."


async def test_admin_can_promote_and_demote_another_user(two_admins):
    second_id = await _user_id(two_admins, "second@example.com")
    demoted = await two_admins.patch(f"/api/users/{second_id}", json={"role": "user"})
    assert demoted.status_code == 200
    assert demoted.json()["role"] == "user"
    assert demoted.json()["is_active"] is True


async def test_admin_cannot_demote_themselves(two_admins):
    """Two admins exist, so this is the self-guard and not the last-admin guard."""
    admin_id = await _user_id(two_admins, "admin@example.com")
    response = await two_admins.patch(f"/api/users/{admin_id}", json={"role": "user"})
    assert response.status_code == 409
    assert response.json()["detail"] == "자신의 권한은 변경할 수 없습니다. 다른 관리자에게 요청해 주세요."
    assert (await two_admins.get("/api/auth/me")).json()["role"] == "admin"


async def test_admin_cannot_deactivate_themselves(two_admins):
    admin_id = await _user_id(two_admins, "admin@example.com")
    response = await two_admins.patch(f"/api/users/{admin_id}", json={"is_active": False})
    assert response.status_code == 409
    assert response.json()["detail"] == "자신의 계정은 비활성화할 수 없습니다."
    assert (await two_admins.get("/api/auth/me")).status_code == 200


@pytest.mark.parametrize("change", [{"role": "user"}, {"is_active": False}])
async def test_the_last_active_admin_cannot_lose_admin(admin_client, change):
    """Only one admin exists here. The check runs before the self-guard because
    "promote someone else first" is the actionable cause."""
    admin_id = await _user_id(admin_client, "admin@example.com")
    response = await admin_client.patch(f"/api/users/{admin_id}", json=change)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "마지막 관리자입니다. 다른 사용자를 관리자로 지정한 뒤에 변경해 주세요."
    )
    assert (await admin_client.get("/api/auth/me")).json()["role"] == "admin"


async def test_a_deactivated_admin_does_not_count_towards_the_last_admin_check(two_admins):
    """Two admin rows, one inactive, is still ONE admin - counting rows by role
    alone would let the remaining one demote themselves."""
    second_id = await _user_id(two_admins, "second@example.com")
    deactivated = await two_admins.patch(f"/api/users/{second_id}", json={"is_active": False})
    assert deactivated.status_code == 200

    admin_id = await _user_id(two_admins, "admin@example.com")
    response = await two_admins.patch(f"/api/users/{admin_id}", json={"role": "user"})
    assert response.status_code == 409
    assert response.json()["detail"].startswith("마지막 관리자입니다.")


async def test_deactivating_a_user_kills_their_live_session(member_client, admin_client, fake_redis):
    member_id = await _user_id(admin_client, "member@example.com")
    session_id = member_client.cookies["mopan_session"]
    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is not None
    assert (await member_client.get("/api/auth/me")).status_code == 200

    response = await admin_client.patch(f"/api/users/{member_id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    # The Redis key is gone, not merely shadowed by the is_active check in
    # get_current_user: a 24-hour session that survives deactivation is the bug.
    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is None
    assert (await member_client.get("/api/auth/me")).status_code == 401


async def test_a_session_that_predates_deactivation_is_rejected_by_get_current_user(
    member_client, admin_client, fake_redis, db
):
    """The second half of the guard. Deactivate WITHOUT going through the router,
    so the Redis session survives - exactly the state a session created before
    migration 0002 ran is in."""
    member_id = await _user_id(admin_client, "member@example.com")
    session_id = member_client.cookies["mopan_session"]
    member = await db.get(User, uuid.UUID(member_id))
    member.is_active = False
    await db.commit()

    assert await fake_redis.get(f"{SESSION_KEY_PREFIX}{session_id}") is not None
    response = await member_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "비활성화된 계정입니다. 관리자에게 문의해 주세요."


async def test_a_deactivated_user_cannot_log_in_and_the_message_hides_the_account(
    member_client, admin_client
):
    member_id = await _user_id(admin_client, "member@example.com")
    await admin_client.patch(f"/api/users/{member_id}", json={"is_active": False})

    response = await member_client.post(
        "/api/auth/login", json={"email": "member@example.com", "password": "pw123456"}
    )
    assert response.status_code == 401
    # Byte-identical to the unknown-email response: a "deactivated account"
    # message would confirm that this address is registered.
    assert response.json() == {"detail": "이메일 또는 비밀번호가 올바르지 않습니다."}


async def test_reactivating_a_user_lets_them_log_in_again(member_client, admin_client):
    member_id = await _user_id(admin_client, "member@example.com")
    await admin_client.patch(f"/api/users/{member_id}", json={"is_active": False})
    reactivated = await admin_client.patch(f"/api/users/{member_id}", json={"is_active": True})
    assert reactivated.status_code == 200

    login = await member_client.post(
        "/api/auth/login", json={"email": "member@example.com", "password": "pw123456"}
    )
    assert login.status_code == 200
```

- [ ] **Step 3: Write `backend/tests/test_documents_api.py`**

Assertions account for the `일반` collection `register_user` seeds for the bootstrap admin — the fixture's `General` is never the only row.

```python
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.models.chunk import EMBEDDING_DIM, Chunk

MISSING_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def admin_client(client, app):
    await client.post("/api/auth/register", json={"email": "admin@example.com", "password": "pw123456"})
    await client.post("/api/auth/login", json={"email": "admin@example.com", "password": "pw123456"})
    return client


@pytest_asyncio.fixture
async def collection_id(admin_client):
    response = await admin_client.post("/api/collections", json={"name": "General"})
    return response.json()["id"]


@pytest_asyncio.fixture
async def member_client(admin_client, app):
    """A second, non-admin account on its own cookie jar."""
    await admin_client.post(
        "/api/auth/register", json={"email": "member@example.com", "password": "pw123456"}
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.post("/api/auth/login", json={"email": "member@example.com", "password": "pw123456"})
        yield ac


async def test_upload_creates_row_and_enqueues_job(admin_client, app, collection_id):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["filename"] == "note.txt"
    assert body["status"] == "uploaded"
    assert body["uploader_email"] == "admin@example.com"
    assert body["collection_name"] == "General"
    app.state.arq_pool.enqueue_job.assert_awaited_once_with("process_document", body["id"])


async def test_upload_requires_admin(member_client, collection_id):
    response = await member_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 403


async def test_create_collection_requires_admin(member_client):
    assert (await member_client.post("/api/collections", json={"name": "X"})).status_code == 403


async def test_delete_document_requires_admin(member_client, admin_client, collection_id):
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = upload.json()["id"]
    assert (await member_client.delete(f"/api/documents/{document_id}")).status_code == 403
    # The refusal must be real, not cosmetic: the row is still there afterwards.
    assert (await admin_client.get(f"/api/documents/{document_id}")).status_code == 200


async def test_admin_delete_removes_the_row_and_the_stored_file(admin_client, app, collection_id):
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = upload.json()["id"]
    stored = Path(app.state.settings.upload_dir) / document_id
    assert stored.exists()

    assert (await admin_client.delete(f"/api/documents/{document_id}")).status_code == 204
    assert (await admin_client.get(f"/api/documents/{document_id}")).status_code == 404
    assert not stored.exists()


async def test_enqueue_failure_marks_the_document_failed_and_drops_the_file(admin_client, app, collection_id):
    """A dropped job must not leave the row at "uploaded" forever, nor leak the file."""
    app.state.arq_pool.enqueue_job.side_effect = RuntimeError("redis down")

    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"]

    reread = await admin_client.get(f"/api/documents/{body['id']}")
    assert reread.json()["status"] == "failed"
    assert not (Path(app.state.settings.upload_dir) / body["id"]).exists()


async def test_members_can_read_the_shared_corpus(member_client, admin_client, collection_id):
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = upload.json()["id"]
    assert (await member_client.get("/api/collections")).status_code == 200
    assert (await member_client.get("/api/documents")).status_code == 200
    assert (await member_client.get(f"/api/documents/{document_id}")).status_code == 200
    assert (await member_client.get(f"/api/documents/{document_id}/chunks")).status_code == 200


async def test_upload_rejects_a_bad_extension(admin_client, collection_id):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("virus.exe", b"bad", "application/octet-stream")},
    )
    assert response.status_code == 400


async def test_upload_rejects_html_renamed_as_pdf(admin_client, collection_id):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("fake.pdf", b"<html><body>hi</body></html>", "application/pdf")},
    )
    assert response.status_code == 400


async def test_traversal_filename_stays_inside_the_upload_root(admin_client, app, collection_id):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("../../evil.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 202
    document_id = response.json()["id"]

    upload_root = Path(app.state.settings.upload_dir).resolve()
    stored = upload_root / document_id / "source.txt"
    assert stored.exists()
    assert stored.resolve().is_relative_to(upload_root)


async def test_upload_rejects_an_unknown_collection(admin_client):
    response = await admin_client.post(
        "/api/documents",
        data={"collection_id": str(uuid.uuid4())},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 404


async def test_list_documents_requires_auth(client):
    assert (await client.get("/api/documents")).status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/collections"),
        ("GET", "/api/collections"),
        ("POST", "/api/documents"),
        ("GET", "/api/documents"),
        ("GET", f"/api/documents/{MISSING_ID}"),
        ("DELETE", f"/api/documents/{MISSING_ID}"),
        ("GET", f"/api/documents/{MISSING_ID}/chunks"),
        ("GET", f"/api/documents/{MISSING_ID}/structure"),
        ("GET", f"/api/chunks/{MISSING_ID}"),
    ],
)
async def test_every_route_requires_authentication(client, method, path):
    """401 before anything else - no route may answer an anonymous caller, and
    none may leak existence through a 404/422 on the way to the auth check."""
    assert (await client.request(method, path)).status_code == 401


async def test_get_unknown_chunk_returns_404(admin_client):
    response = await admin_client.get(f"/api/chunks/{uuid.uuid4()}")
    assert response.status_code == 404
    # The detail is user-facing: it renders in the chat citation modal, labelled
    # 출처, when a cited chunk's document has been deleted. 청크 is internal
    # vocabulary the chat surface uses nowhere else.
    assert response.json()["detail"] == "출처 내용을 불러올 수 없습니다."


async def test_chunk_response_reports_embedding_state(admin_client, db, collection_id):
    """`embedded` is derived from the embedding column, not stored: the vector
    itself is 1536 floats and never goes on the wire."""
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])
    db.add_all(
        [
            Chunk(
                document_id=document_id,
                chunk_index=0,
                content="embedded chunk",
                token_count=2,
                char_count=14,
                chunk_metadata={"strategy": "semantic"},
                embedding=[0.0] * EMBEDDING_DIM,
            ),
            Chunk(
                document_id=document_id,
                chunk_index=1,
                content="unembedded chunk",
                token_count=2,
                char_count=16,
                chunk_metadata={},
                embedding=None,
            ),
        ]
    )
    await db.commit()

    response = await admin_client.get(f"/api/documents/{document_id}/chunks")
    assert response.status_code == 200
    body = response.json()
    assert [c["embedded"] for c in body] == [True, False]
    assert body[0]["chunk_metadata"] == {"strategy": "semantic"}
    assert "embedding" not in body[0]

    single = await admin_client.get(f"/api/chunks/{body[0]['id']}")
    assert single.json()["embedded"] is True


async def test_document_structure_returns_parsed_blocks(admin_client, collection_id):
    upload = await admin_client.post(
        "/api/documents",
        data={"collection_id": collection_id},
        files={"file": ("doc.md", b"# Title\n\nA paragraph.\n", "text/markdown")},
    )
    document_id = upload.json()["id"]
    response = await admin_client.get(f"/api/documents/{document_id}/structure")
    assert response.status_code == 200
    blocks = response.json()
    assert blocks[0]["block_type"] == "heading"
    assert blocks[0]["text"] == "Title"


# --- collection CRUD ----------------------------------------------------------


async def test_rename_collection_and_clear_its_description(admin_client, collection_id):
    renamed = await admin_client.patch(
        f"/api/collections/{collection_id}", json={"name": "사규", "description": "인사 규정"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "사규"
    assert renamed.json()["description"] == "인사 규정"

    # An OMITTED field must not be touched; an explicit null must clear it. A
    # plain model_dump() cannot tell those apart and would wipe the name here.
    cleared = await admin_client.patch(
        f"/api/collections/{collection_id}", json={"description": None}
    )
    assert cleared.status_code == 200
    assert cleared.json() == {**renamed.json(), "description": None}


async def test_collection_name_is_stripped_and_a_blank_name_is_rejected(admin_client, collection_id):
    stripped = await admin_client.patch(f"/api/collections/{collection_id}", json={"name": "  사규  "})
    assert stripped.json()["name"] == "사규"
    blank = await admin_client.patch(f"/api/collections/{collection_id}", json={"name": "   "})
    assert blank.status_code == 422
    # collections.name is NOT NULL, so an explicit null is a 422 and not a 409
    # blaming a duplicate name that does not exist.
    null = await admin_client.patch(f"/api/collections/{collection_id}", json={"name": None})
    assert null.status_code == 422


async def test_duplicate_collection_name_is_refused_on_create_and_rename(admin_client, collection_id):
    duplicate = await admin_client.post("/api/collections", json={"name": "General"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "같은 이름의 분류가 이미 있습니다. 다른 이름을 입력해 주세요."

    other = await admin_client.post("/api/collections", json={"name": "Other"})
    assert other.status_code == 200
    renamed = await admin_client.patch(f"/api/collections/{other.json()['id']}", json={"name": "General"})
    assert renamed.status_code == 409
    assert renamed.json()["detail"] == "같은 이름의 분류가 이미 있습니다. 다른 이름을 입력해 주세요."


async def test_delete_empty_collection(admin_client, collection_id):
    assert (await admin_client.delete(f"/api/collections/{collection_id}")).status_code == 204
    remaining = (await admin_client.get("/api/collections")).json()
    # Only 일반 is left - the one register_user seeds for the bootstrap admin.
    assert [c["name"] for c in remaining] == ["일반"]


async def test_the_last_collection_is_deletable(admin_client, collection_id):
    """Deliberate: an admin left with none creates one, which is the same click
    the upload form's empty state already offers. A floor of one would instead
    make a single mis-named collection permanent, and renaming is a PATCH away."""
    for collection in (await admin_client.get("/api/collections")).json():
        assert (await admin_client.delete(f"/api/collections/{collection['id']}")).status_code == 204
    assert (await admin_client.get("/api/collections")).json() == []
    assert (await admin_client.post("/api/collections", json={"name": "다시"})).status_code == 200


async def test_delete_refuses_while_the_collection_holds_documents(admin_client, app, collection_id):
    for name in ("a.txt", "b.txt"):
        await admin_client.post(
            "/api/documents",
            data={"collection_id": collection_id},
            files={"file": (name, b"hello", "text/plain")},
        )

    response = await admin_client.delete(f"/api/collections/{collection_id}")
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "문서 2개가 들어 있는 분류는 삭제할 수 없습니다. 먼저 문서를 삭제해 주세요."
    )
    # documents.collection_id is ON DELETE CASCADE and chunks cascade from
    # documents, so without the guard this call destroys both rows silently and
    # orphans their files under upload_dir.
    assert len((await admin_client.get("/api/documents")).json()) == 2
    names = [c["name"] for c in (await admin_client.get("/api/collections")).json()]
    assert "General" in names

    for document in (await admin_client.get("/api/documents")).json():
        await admin_client.delete(f"/api/documents/{document['id']}")
    assert (await admin_client.delete(f"/api/collections/{collection_id}")).status_code == 204


async def test_collection_writes_are_admin_only(member_client, admin_client, collection_id):
    renamed = await member_client.patch(f"/api/collections/{collection_id}", json={"name": "X"})
    assert renamed.status_code == 403
    assert (await member_client.delete(f"/api/collections/{collection_id}")).status_code == 403
    # The refusal must be real, not cosmetic: the row is unchanged and still there.
    survivors = (await admin_client.get("/api/collections")).json()
    assert [c["name"] for c in survivors if c["id"] == collection_id] == ["General"]


async def test_unknown_collection_id_is_404(admin_client):
    patched = await admin_client.patch(f"/api/collections/{MISSING_ID}", json={"name": "X"})
    assert patched.status_code == 404
    assert patched.json()["detail"] == "분류를 찾을 수 없습니다."
    deleted = await admin_client.delete(f"/api/collections/{MISSING_ID}")
    assert deleted.status_code == 404
    assert deleted.json()["detail"] == "분류를 찾을 수 없습니다."
```

---

### Task 6: Verification

- [ ] **Step 1: Run the suite, serially**

```bash
cd backend && python -m pytest
```

Expected: `388 passed`. It was 365 before this plan.

- [ ] **Step 2: Run the linter**

```bash
cd backend && python -m ruff check .
```

- [ ] **Step 3: Check this plan against disk**

```bash
python scripts/check_plan_parity.py docs/superpowers/plans/2026-08-30-management-screens.md
```

- [ ] **Step 4: Rebuild the stack and drive the endpoints by hand**

The backend image `COPY`s the source; there is no bind mount, so `docker compose restart backend` runs the OLD code.

```bash
docker compose up -d --build backend worker
```

---

## Frontend: the management screens

The API contract above is the whole input to this half. Nothing below changes a
backend file; every screen is built against the endpoints Tasks 3 and 4 ship and
against the Korean `detail=` strings they return.

**Decisions.**

**The 409s are the feature.** Both screens exist to put a backend refusal in
front of an admin at the moment they act. So every one of them renders where the
click happened - the duplicate-name message under the create form, a rename
conflict inside the row being renamed, the document-count refusal inside the
delete dialog - and never as a page-level banner far from the button.

**No optimistic UI.** Every mutation either refetches the list or applies the
object the server returned. A role change that paints as done and was refused is
worse than a slow one: the `<select>` is controlled by state the failure does not
touch, so it snaps back to the role the backend still holds.

**One list request for the document count.** `CollectionResponse` carries no
count and this plan does not add one. The screen derives it from a single
`GET /api/documents` - the same call the documents screen already makes - and
tallies by `collection_id` in the browser, rather than issuing one request per
row. If that request fails the column reads `-`, which is not the same as `0`.

**`<dialog>` + `showModal()` for both destructive actions.** The focus trap,
Escape, the inert background and top-layer stacking are all native. A hand-rolled
overlay has to reimplement four things, and the citation modal already proved
which ones get forgotten.

---

### Task 7: Types and the confirmation dialog

**Files:**
- Modify: `frontend/lib/types.ts`
- Create: `frontend/components/ui/ConfirmDialog.tsx`

**Interfaces:**
- Produces: `ManagedUser`, and a `ConfirmDialog` that runs the action itself so a
  409 lands inside the open dialog instead of behind it.
- Consumed by: Tasks 8 and 9.

- [ ] **Step 1: Write `frontend/lib/types.ts`**

`Collection` already mirrors `CollectionResponse` field for field, so only the
user type is new. It extends `User` rather than restating it: `AdminUserResponse`
extends `UserResponse` on the backend for the same reason.

```typescript
export interface User {
  id: string;
  email: string;
  role: "admin" | "user";
}

/** GET /api/users and PATCH /api/users/{id} - the backend's AdminUserResponse.
 * Kept separate from User, which is what /api/auth/me returns to every logged-in
 * user: is_active and created_at are for the management screen only. */
export interface ManagedUser extends User {
  is_active: boolean;
  created_at: string;
}

/** CollectionResponse. It carries no document count - the management screen
 * derives one from a single GET /api/documents rather than asking per row. */
export interface Collection {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
}

export type DocumentStatus =
  | "uploaded"
  | "parsing"
  | "chunking"
  | "embedding"
  | "indexed"
  | "failed";

export interface DocumentItem {
  id: string;
  collection_id: string;
  collection_name: string | null;
  filename: string;
  file_type: string;
  size_bytes: number;
  status: DocumentStatus;
  error_message: string | null;
  uploader_email: string | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface Chunk {
  id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
  char_count: number;
  page: number | null;
  section: string | null;
  chunk_metadata: Record<string, unknown>;
  // Derived server-side from the chunk's embedding column; see ChunkResponse.
  embedded: boolean;
}

export interface Block {
  text: string;
  block_type: "heading" | "paragraph" | "list_item" | "table_cell";
  page: number | null;
  section: string | null;
}

export interface Citation {
  index: number;
  // source_type and ref are the only two fields every citation carries - the
  // five below come from Evidence.metadata and a Slice 2/3 MCP citation has
  // none of them. See _citations_from in backend/app/chat/service.py.
  source_type: "rag" | "mcp";
  ref: string;
  chunk_id: string | null;
  document_id: string | null;
  filename: string | null;
  page: number | null;
  section: string | null;
  snippet: string;
  score: number | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  created_at: string;
}

/** SSE payloads from POST /api/chat. `token` is reserved for Slice 3. */
export type ChatEvent =
  | { type: "status"; status: "searching" | "answering" }
  | { type: "token"; text: string }
  | { type: "citations"; citations: Citation[] }
  | { type: "done"; conversation_id: string; content: string; citations: Citation[] }
  | { type: "error"; detail: string };
```

- [ ] **Step 2: Create `frontend/components/ui/ConfirmDialog.tsx`**

It takes `onConfirm: () => Promise<void>` and awaits it rather than handing the
click back and closing. That is what lets the delete refusal - which arrives
after the click - render under the button that was pressed.

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";

/** Confirmation for a destructive action. Native <dialog> + showModal(), the
 * same pattern as CitationBadge: focus trap, Escape, an inert background and
 * top-layer stacking all come with it, and none of them have to be written
 * here. Mounted only while a target is chosen, so mount means open.
 *
 * It runs `onConfirm` itself rather than handing the click back and closing,
 * because the 409 these screens exist to surface - "문서 N개가 들어 있는
 * 분류는..." - arrives AFTER the click. Closing first would put that message on
 * a page the user is no longer looking at; instead the dialog stays open and
 * renders it under the button that was pressed, and closes only on success. */
export default function ConfirmDialog({
  title,
  message,
  confirmLabel,
  onConfirm,
  onClose,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      dialogRef.current?.close();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="confirm-title"
      // Escape closes a <dialog> natively without firing any click handler, so
      // this is what keeps the parent's `target` state in step with the DOM.
      onClose={onClose}
      className="w-full max-w-md rounded border border-gray-200 bg-white p-0 text-gray-900 backdrop:bg-black/30"
    >
      <div className="p-4">
        <h2 id="confirm-title" className="text-sm font-semibold">
          {title}
        </h2>
        <p className="mt-2 text-sm text-gray-700">{message}</p>
        <div className="mt-2">
          <ErrorBanner message={error} />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => dialogRef.current?.close()}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-100"
          >
            취소
          </button>
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={busy}
            className="rounded border border-red-300 bg-red-50 px-3 py-1.5 text-sm text-red-700 hover:bg-red-100 disabled:opacity-50"
          >
            {busy ? "처리 중..." : confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}
```

---

### Task 8: `/collections`

**Files:**
- Create: `frontend/app/(app)/collections/page.tsx`

**Interfaces:**
- Consumes: `GET|POST /api/collections`, `PATCH|DELETE /api/collections/{id}`,
  `GET /api/documents` (for the count), `GET /api/auth/me` (to keep buttons that
  cannot work off a non-admin's screen).

- [ ] **Step 1: Create `frontend/app/(app)/collections/page.tsx`**

An empty 설명 is sent as an explicit `null`, which is the only way to clear it -
`CollectionUpdate` treats an omitted field as "leave alone". The saved row is
built from the response, not from the form, because the backend trims the name.

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Collection, DocumentItem, User } from "@/lib/types";

export default function CollectionsPage() {
  const [user, setUser] = useState<User | null>(null);
  // null is "not loaded yet", not "none" - the same distinction the documents
  // page draws, so 분류가 없습니다. never flashes at an admin who has some.
  const [collections, setCollections] = useState<Collection[] | null>(null);
  // null is "the count is unknown", which is not 0. See load().
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [saving, setSaving] = useState(false);
  // One row acts at a time, so one slot rather than a map keyed by id. It is
  // rendered inside the row that produced it: a rename refused with 같은 이름의
  // 분류가 이미 있습니다. has to appear beside the field holding that name.
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Collection | null>(null);

  // The document count comes from ONE list request, not one per row:
  // CollectionResponse does not carry a count, and /api/documents is the same
  // call the documents page already makes. allSettled, not all, because the
  // count is decoration while the list is the page - a failing /api/documents
  // must not blank out the 분류 table. A rejected count leaves `counts` null and
  // the column reads "-" rather than a wrong 0.
  const load = useCallback(async () => {
    const [cols, docs] = await Promise.allSettled([
      apiFetch<Collection[]>("/api/collections"),
      apiFetch<DocumentItem[]>("/api/documents"),
    ]);
    if (cols.status === "fulfilled") setCollections(cols.value);
    if (docs.status === "fulfilled") {
      const tally: Record<string, number> = {};
      for (const doc of docs.value) {
        tally[doc.collection_id] = (tally[doc.collection_id] ?? 0) + 1;
      }
      setCounts(tally);
    }
    setLoadError(cols.status === "rejected" ? errorMessage(cols.reason) : null);
  }, []);

  useEffect(() => {
    apiFetch<User>("/api/auth/me")
      .then(setUser)
      .catch((err) => setLoadError(errorMessage(err)));
    void load();
  }, [load]);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError(null);
    try {
      await apiFetch<Collection>("/api/collections", {
        method: "POST",
        body: JSON.stringify({ name, description: description.trim() || null }),
      });
      setName("");
      setDescription("");
      // Refetch rather than pushing the returned row onto the list: another
      // admin's collection created since this page loaded would otherwise stay
      // invisible until a reload, and its document count would be missing.
      await load();
    } catch (err) {
      setCreateError(errorMessage(err));
    } finally {
      setCreating(false);
    }
  }

  function startEdit(collection: Collection) {
    setEditingId(collection.id);
    setEditName(collection.name);
    setEditDescription(collection.description ?? "");
    setRowError(null);
  }

  async function handleSave(id: string) {
    setSaving(true);
    setRowError(null);
    try {
      // An empty 설명 is sent as an explicit null, which is the only way to
      // clear it - PATCH treats an OMITTED field as "leave this alone".
      const updated = await apiFetch<Collection>(`/api/collections/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: editName, description: editDescription.trim() || null }),
      });
      // The server's object, not the form's. The backend trims the name, so the
      // row has to show what was stored and not what was typed.
      setCollections((prev) => (prev ?? []).map((c) => (c.id === id ? updated : c)));
      setEditingId(null);
    } catch (err) {
      setRowError({ id, message: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <h1 className="text-lg font-semibold">분류 관리</h1>
      <ErrorBanner message={loadError} />

      {/* `user === null` is "not loaded yet", not "not an admin" - branching on
          the role alone tells every admin they lack permission for the length
          of the /api/auth/me round trip. The endpoints answer a non-admin with
          403 관리자 권한이 필요합니다. regardless; this only keeps buttons that
          cannot work off the screen. */}
      {user !== null && user.role !== "admin" ? (
        <p className="text-sm text-gray-500">분류 관리는 관리자만 할 수 있습니다.</p>
      ) : (
        <>
          {user !== null && (
            <form onSubmit={handleCreate} className="space-y-2 rounded border border-gray-200 p-4">
              <div className="flex flex-wrap items-end gap-2">
                <div className="flex flex-col gap-1">
                  <label htmlFor="new-collection-name" className="text-sm text-gray-500">
                    분류 이름
                  </label>
                  <input
                    id="new-collection-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                    maxLength={255}
                    className="rounded border border-gray-300 px-3 py-2 text-sm"
                  />
                </div>
                <div className="flex flex-1 flex-col gap-1">
                  <label htmlFor="new-collection-description" className="text-sm text-gray-500">
                    설명 (선택)
                  </label>
                  <input
                    id="new-collection-description"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className="w-full rounded border border-gray-300 px-3 py-2 text-sm"
                  />
                </div>
                <button
                  type="submit"
                  disabled={creating}
                  className="rounded border border-gray-300 px-3 py-2 text-sm hover:bg-gray-100 disabled:opacity-50"
                >
                  {creating ? "추가 중..." : "분류 추가"}
                </button>
              </div>
              {/* Under the form, not at the top of the page: 같은 이름의 분류가
                  이미 있습니다. is about the name in the field right above it. */}
              <ErrorBanner message={createError} />
            </form>
          )}

          {collections === null ? (
            <p className="py-8 text-center text-sm text-gray-400">불러오는 중...</p>
          ) : collections.length === 0 ? (
            <p className="py-8 text-center text-sm text-gray-400">분류가 없습니다.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-gray-200 text-gray-500">
                    <th scope="col" className="py-2 pr-3">분류 이름</th>
                    <th scope="col" className="py-2 pr-3">설명</th>
                    <th scope="col" className="py-2 pr-3 text-right">문서 수</th>
                    <th scope="col" className="py-2 pr-3">등록일</th>
                    <th scope="col" className="py-2">관리</th>
                  </tr>
                </thead>
                <tbody>
                  {collections.map((c) => {
                    const editing = editingId === c.id;
                    return (
                      <tr key={c.id} className="border-b border-gray-100 align-top">
                        <td className="py-2 pr-3">
                          {editing ? (
                            <input
                              value={editName}
                              onChange={(e) => setEditName(e.target.value)}
                              maxLength={255}
                              aria-label={`${c.name} 분류 이름`}
                              className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                            />
                          ) : (
                            c.name
                          )}
                        </td>
                        <td className="py-2 pr-3 text-gray-500">
                          {editing ? (
                            <input
                              value={editDescription}
                              onChange={(e) => setEditDescription(e.target.value)}
                              aria-label={`${c.name} 설명`}
                              className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
                            />
                          ) : (
                            (c.description ?? "-")
                          )}
                        </td>
                        <td className="py-2 pr-3 text-right text-gray-500">
                          {counts === null ? "-" : (counts[c.id] ?? 0)}
                        </td>
                        <td className="py-2 pr-3 text-gray-500">
                          {new Date(c.created_at).toLocaleDateString()}
                        </td>
                        <td className="py-2">
                          <div className="flex gap-2">
                            {editing ? (
                              <>
                                <button
                                  type="button"
                                  onClick={() => void handleSave(c.id)}
                                  disabled={saving}
                                  className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-100 disabled:opacity-50"
                                >
                                  저장
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setEditingId(null);
                                    setRowError(null);
                                  }}
                                  className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-100"
                                >
                                  취소
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  onClick={() => startEdit(c)}
                                  className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-100"
                                >
                                  수정
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setRowError(null);
                                    setDeleteTarget(c);
                                  }}
                                  className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                                >
                                  삭제
                                </button>
                              </>
                            )}
                          </div>
                          {rowError?.id === c.id && (
                            <div className="mt-2 max-w-sm">
                              <ErrorBanner message={rowError.message} />
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {/* The delete 409 - 문서 N개가 들어 있는 분류는... - arrives after the
          click, so ConfirmDialog runs the request itself and renders the message
          inside the dialog rather than closing first. */}
      {deleteTarget && (
        <ConfirmDialog
          title="분류 삭제"
          message={`'${deleteTarget.name}' 분류를 삭제할까요? 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onClose={() => setDeleteTarget(null)}
          onConfirm={async () => {
            await apiFetch(`/api/collections/${deleteTarget.id}`, { method: "DELETE" });
            await load();
          }}
        />
      )}
    </div>
  );
}
```

---

### Task 9: `/users`

**Files:**
- Create: `frontend/app/(app)/users/page.tsx`

**Interfaces:**
- Consumes: `GET /api/users`, `PATCH /api/users/{id}`, `GET /api/auth/me` (only
  to mark which row is the acting admin).

- [ ] **Step 1: Create `frontend/app/(app)/users/page.tsx`**

No role branch of its own: `GET /api/users` answers a non-admin with 403 관리자
권한이 필요합니다., which is exactly the message to show, and there is nothing to
render either way. Marking the acting admin's own row `(나)` is what makes
자신의 권한은 변경할 수 없습니다. read as an explanation rather than a riddle.

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { ManagedUser, User } from "@/lib/types";

const ROLE_LABEL: Record<ManagedUser["role"], string> = {
  admin: "관리자",
  user: "일반",
};

export default function UsersPage() {
  const [me, setMe] = useState<User | null>(null);
  // null is "not loaded yet". GET /api/users answers a non-admin with 403
  // 관리자 권한이 필요합니다., which lands in loadError - so this page needs no
  // role branch of its own; there is nothing to render either way.
  const [users, setUsers] = useState<ManagedUser[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // One row acts at a time. The 409s this screen exists to show - 마지막
  // 관리자입니다..., 자신의 권한은 변경할 수 없습니다... - name the row that was
  // touched, so they render in it rather than in a banner at the top.
  const [rowError, setRowError] = useState<{ id: string; message: string } | null>(null);
  const [deactivateTarget, setDeactivateTarget] = useState<ManagedUser | null>(null);

  const load = useCallback(async () => {
    try {
      setUsers(await apiFetch<ManagedUser[]>("/api/users"));
      setLoadError(null);
    } catch (err) {
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    apiFetch<User>("/api/auth/me").then(setMe).catch(() => undefined);
    void load();
  }, [load]);

  /** Applies the server's returned user, never the value that was submitted.
   * A role change that renders as done but was refused is worse than a slow
   * one: `users` is untouched on failure, so the <select> - controlled by that
   * state - snaps back to the role the backend still holds.
   *
   * `inline` is false for the call the confirmation dialog makes: it renders
   * the failure itself, and setting the row error too would print the same 409
   * twice, once of them behind the open modal. It always rethrows, which is how
   * the dialog knows to stay open. */
  async function patch(
    id: string,
    body: { role?: string; is_active?: boolean },
    inline = true,
  ) {
    setBusyId(id);
    setRowError(null);
    try {
      const updated = await apiFetch<ManagedUser>(`/api/users/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setUsers((prev) => (prev ?? []).map((u) => (u.id === id ? updated : u)));
    } catch (err) {
      if (inline) setRowError({ id, message: errorMessage(err) });
      throw err;
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <h1 className="text-lg font-semibold">사용자 관리</h1>
      <ErrorBanner message={loadError} />

      {users === null ? (
        !loadError && <p className="py-8 text-center text-sm text-gray-400">불러오는 중...</p>
      ) : users.length === 0 ? (
        <p className="py-8 text-center text-sm text-gray-400">사용자가 없습니다.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-gray-500">
                <th scope="col" className="py-2 pr-3">이메일</th>
                <th scope="col" className="py-2 pr-3">권한</th>
                <th scope="col" className="py-2 pr-3">상태</th>
                <th scope="col" className="py-2 pr-3">가입일</th>
                <th scope="col" className="py-2">관리</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-gray-100 align-top">
                  <td className="py-2 pr-3">
                    {u.email}
                    {/* Which row is you is what makes 자신의 권한은 변경할 수
                        없습니다. readable as an explanation instead of a riddle. */}
                    {me?.id === u.id && <span className="ml-1 text-xs text-gray-400">(나)</span>}
                  </td>
                  <td className="py-2 pr-3">
                    <select
                      value={u.role}
                      disabled={busyId === u.id}
                      // No visible <label> per row - the column header is the
                      // label a sighted user reads, and repeating it 40 times
                      // would say nothing about WHICH user. The email does.
                      aria-label={`${u.email} 권한`}
                      onChange={(e) => {
                        void patch(u.id, { role: e.target.value }).catch(() => undefined);
                      }}
                      className="rounded border border-gray-300 px-2 py-1 text-sm disabled:opacity-50"
                    >
                      {Object.entries(ROLE_LABEL).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="py-2 pr-3">
                    <span className={u.is_active ? "text-gray-700" : "text-red-600"}>
                      {u.is_active ? "활성" : "비활성"}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-gray-500">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="py-2">
                    {u.is_active ? (
                      <button
                        type="button"
                        onClick={() => {
                          setRowError(null);
                          setDeactivateTarget(u);
                        }}
                        className="rounded border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                      >
                        비활성화
                      </button>
                    ) : (
                      // Reactivating takes nothing away, so it needs no
                      // confirmation step - only the deactivation does.
                      <button
                        type="button"
                        disabled={busyId === u.id}
                        onClick={() => {
                          void patch(u.id, { is_active: true }).catch(() => undefined);
                        }}
                        className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-100 disabled:opacity-50"
                      >
                        활성화
                      </button>
                    )}
                    {rowError?.id === u.id && (
                      <div className="mt-2 max-w-sm">
                        <ErrorBanner message={rowError.message} />
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {deactivateTarget && (
        <ConfirmDialog
          title="사용자 비활성화"
          message={`${deactivateTarget.email} 계정을 비활성화할까요? 로그인할 수 없게 되고, 사용 중인 세션도 즉시 끊깁니다.`}
          confirmLabel="비활성화"
          onClose={() => setDeactivateTarget(null)}
          onConfirm={() => patch(deactivateTarget.id, { is_active: false }, false)}
        />
      )}
    </div>
  );
}
```

---

### Task 10: The 관리 section in the sidebar

**Files:**
- Modify: `frontend/components/layout/Sidebar.tsx`

**Interfaces:**
- Consumes: the `User` the sidebar already fetches from `/api/auth/me`.

- [ ] **Step 1: Write `frontend/components/layout/Sidebar.tsx`**

The link markup is factored into one `navLink` helper rather than copied: the
active styling and `aria-current` are the pair a second copy would eventually get
wrong. `user` is null until `/api/auth/me` lands, so a non-admin never sees the
section appear and then vanish.

```tsx
"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, errorMessage } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import type { Conversation, User } from "@/lib/types";

// The trap has to enumerate everything focusable inside the drawer, not just
// what happens to be in it today: with "a, button" the first <input> added to
// the sidebar (a history filter) becomes an element the trap does not know
// about, so `last` stops being the real last stop and Tab escapes the dialog.
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  // null means "not loaded yet", which is not the same as an empty list. With
  // [] as the initial value every page load flashes "아직 대화가 없습니다."
  // for the length of the fetch, including for users who do have conversations.
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Separate from `error` on purpose. The history region is scrollable, so an
  // ErrorBanner rendered at its top is off-screen for anyone with enough
  // conversations to have scrolled - measured 0 visible pixels at 1280x800
  // with 31 conversations. A logout failure has to report next to the button
  // that was clicked. It is also cleared per attempt rather than by load().
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  // allSettled, not all: these two requests are independent and Promise.all
  // rejects the pair on the first failure, so a 500 from /api/conversations
  // threw away a perfectly good /api/auth/me and left the footer showing the
  // blank U+00A0 placeholder - a transient history-list error made the user
  // look logged out. Each result is now applied on its own. The list is still set
  // in the same tick as the user, so `conversations` stays null - never [] -
  // until the fetch resolves, which is what keeps the empty state from
  // flashing. The conversations failure is checked first because the banner
  // renders in the history region, where its own error belongs.
  const load = useCallback(async () => {
    const [me, list] = await Promise.allSettled([
      apiFetch<User>("/api/auth/me"),
      apiFetch<Conversation[]>("/api/conversations"),
    ]);
    if (me.status === "fulfilled") setUser(me.value);
    if (list.status === "fulfilled") setConversations(list.value);
    const failed = [list, me].find(
      (result): result is PromiseRejectedResult => result.status === "rejected",
    );
    setError(failed ? errorMessage(failed.reason) : null);
  }, []);

  // pathname is a dependency on purpose: the chat page creates a conversation
  // and then router.replace()s to /chat/{id}, and the new title has to reach
  // this list. Measured on `next start`, that particular navigation is a full
  // document load, which remounts this component and reloads the list anyway;
  // the dependency is what covers the soft navigations - every click between
  // conversations - and what would still cover the replace if it became one.
  useEffect(() => {
    void load();
  }, [load, pathname]);

  // Without these the drawer is only technically keyboard-usable: nothing moves
  // focus into it on open, so dismissing it means tabbing past every history
  // link to reach the closing overlay - ~34 presses with 30 conversations.
  // Escape closes it, and focus returns to the toggle that opened it.
  useEffect(() => {
    if (!open) return;
    drawerRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    // aria-modal="true" is a promise that nothing outside the dialog is
    // reachable; `inert` is what makes it true for the DOM rather than only
    // for AT that honours the attribute - measured with the drawer open,
    // <main> still held 4 focusable elements. It is set from here with
    // setAttribute rather than as a JSX prop because `open` lives in this
    // client component while <main> is rendered by (app)/layout.tsx, which is
    // a server component. The body lock is the pointer half of the same bug:
    // the drawer is `fixed`, so without it the page behind scrolls on touch.
    const main = document.getElementById("app-main");
    main?.setAttribute("inert", "");
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        return;
      }
      // The drawer covers the page but does not remove it from the tab order,
      // so without this Tab walks into content hidden behind the overlay.
      if (event.key !== "Tab") return;
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      main?.removeAttribute("inert");
      document.body.style.overflow = previousOverflow;
      toggleRef.current?.focus();
    };
  }, [open]);

  // `md:hidden` only stops the drawer from being *displayed* above 768px; the
  // state stays true, so resizing 390 -> 1280 -> 390 with it open brought the
  // drawer back on the way down without the user reopening it. 768px is
  // Tailwind's `md`, the same breakpoint the classes use.
  useEffect(() => {
    const docked = window.matchMedia("(min-width: 768px)");
    const onChange = () => {
      if (docked.matches) setOpen(false);
    };
    docked.addEventListener("change", onChange);
    return () => docked.removeEventListener("change", onChange);
  }, []);

  async function handleLogout() {
    // Navigate only on success. A `finally` here lands the user on /login after
    // a failed request - with mopan_session still in the browser and the Redis
    // session still valid, because neither delete_cookie nor delete_session
    // ran. "Logged out" with a live session is the worst outcome available, so
    // a failure stays put and says so next to the button that was clicked.
    setLogoutError(null);
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch (err) {
      setLogoutError(errorMessage(err));
      return;
    }
    router.push("/login");
    // The App Router caches rendered segments client-side. Without this the
    // authenticated pages stay in that cache after the cookie is gone.
    router.refresh();
  }

  const navLinks = [
    { href: "/chat", label: "새 대화" },
    { href: "/documents", label: "문서" },
  ];

  // Rendered only for an admin. Both screens are admin-only on the server too -
  // every endpoint behind them answers 403 관리자 권한이 필요합니다. - so this is
  // about not offering a link that leads to a refusal, not about access.
  const adminLinks = [
    { href: "/collections", label: "분류 관리" },
    { href: "/users", label: "사용자 관리" },
  ];

  // Same markup for both groups: the active styling and aria-current below are
  // the one thing a second copy would eventually get wrong.
  const navLink = (link: { href: string; label: string }) => {
    const active = pathname === link.href;
    return (
      <Link
        key={link.href}
        href={link.href}
        onClick={() => setOpen(false)}
        // The background alone said "you are here" to sighted users only.
        aria-current={active ? "page" : undefined}
        className={`rounded px-3 py-2 text-sm hover:bg-gray-200 ${
          active ? "bg-gray-200 font-medium" : ""
        }`}
      >
        {link.label}
      </Link>
    );
  };

  const content = (
    // aria-label because the MOPAN line above is a <div>, so the landmark had
    // no accessible name and announced as a bare "navigation". 대화 기록 stays
    // a <div> rather than becoming a heading: the sidebar precedes <main> in
    // the DOM, so a heading here would sit above every page's <h1>.
    <nav
      aria-label="주 메뉴"
      className="flex h-full w-64 flex-col border-r border-gray-200 bg-gray-50 p-3"
    >
      <div className="mb-4 px-3 text-sm font-semibold text-gray-500">MOPAN</div>
      {navLinks.map(navLink)}

      {/* `user` is null until /api/auth/me lands, so a non-admin never sees this
          appear and then vanish. flex-col on the wrapper because the links are
          <a> elements: as direct children of this flex column they stack on
          their own, but inside a plain div they would run side by side. */}
      {user?.role === "admin" && (
        <div className="mt-4">
          <div className="mb-1 px-3 text-xs tracking-wide text-gray-400">관리</div>
          <div className="flex flex-col">{adminLinks.map(navLink)}</div>
        </div>
      )}

      <div className="mt-4 flex-1 overflow-y-auto">
        <div className="mb-1 px-3 text-xs tracking-wide text-gray-400">대화 기록</div>
        {error && <ErrorBanner message={error} />}
        {!error && conversations?.length === 0 && (
          <p className="px-3 py-2 text-xs text-gray-400">아직 대화가 없습니다.</p>
        )}
        {/* Which conversation you are in is the one piece of state a history
            list exists to convey, and the links carried only a hover style:
            measured at /chat/c3, every link's computed background was
            rgba(0,0,0,0). Same treatment as the nav links above. */}
        {conversations?.map((c) => {
          const active = pathname === `/chat/${c.id}`;
          return (
            <Link
              key={c.id}
              href={`/chat/${c.id}`}
              onClick={() => setOpen(false)}
              aria-current={active ? "page" : undefined}
              className={`block truncate rounded px-3 py-2 text-sm hover:bg-gray-200 ${
                active ? "bg-gray-200 font-medium" : ""
              }`}
            >
              {c.title}
            </Link>
          );
        })}
      </div>

      <div className="mt-3 border-t border-gray-200 pt-3">
        {/* The placeholder is U+00A0, not an ASCII space: a plain space is
            collapsible, so the line gets no line box and is 0px tall until
            /api/auth/me lands - at which point it grows 16px and shoves
            로그아웃 down under the pointer already resting on it. */}
        <div className="truncate px-3 text-xs text-gray-500">
          {user ? `${user.email}${user.role === "admin" ? " · 관리자" : ""}` : "\u00a0"}
        </div>
        {logoutError && <ErrorBanner message={logoutError} />}
        {/* type="button" on every button in this file: the default is
            "submit", which is a live bug the moment one of them ends up
            inside a <form>. */}
        <button
          type="button"
          onClick={() => void handleLogout()}
          className="mt-2 w-full rounded border border-gray-300 px-3 py-2 text-sm hover:bg-gray-200"
        >
          로그아웃
        </button>
      </div>
    </nav>
  );

  return (
    <>
      {/* Not rendered while the drawer is open: at z-20 it sits *under* the
          z-30 drawer, so a pointer user cannot reach it while a keyboard user
          can still focus it and press it for nothing. No aria-expanded either
          - it only opens; the drawer closes via its overlay or Escape. */}
      {!open && (
        <button
          ref={toggleRef}
          type="button"
          aria-label="메뉴 열기"
          aria-controls="sidebar-drawer"
          className="fixed left-2 top-2 z-20 rounded border border-gray-300 bg-white px-2 py-1 text-sm md:hidden"
          onClick={() => setOpen(true)}
        >
          ☰
        </button>
      )}
      <div className="hidden md:block">{content}</div>
      {open && (
        <div
          id="sidebar-drawer"
          ref={drawerRef}
          role="dialog"
          aria-modal="true"
          aria-label="메뉴"
          className="fixed inset-0 z-30 flex md:hidden"
        >
          <div className="relative">{content}</div>
          {/* A button, not a div: this overlay is the only way to close the
              drawer, and as a div it is unreachable without a pointer. */}
          <button
            type="button"
            aria-label="메뉴 닫기"
            className="flex-1 bg-black/30"
            onClick={() => setOpen(false)}
          />
        </div>
      )}
    </>
  );
}
```

---

### Task 11: Frontend verification

- [ ] **Step 1: Typecheck, build and test**

```bash
cd frontend && npx tsc --noEmit && npm run build && npm test
```

A build against a stale `.next` can fail once with
`PageNotFoundError: Cannot find module for page: /_not-found`; delete `.next` and
rerun.

- [ ] **Step 2: Confirm no second visual style crept in**

The screens are flat, bordered, `rounded`, grayscale plus red. A full redesign is
a separate pass; this work only has to be consistent with what Slice 1 ships.

```bash
grep -rniE "gradient|shadow|blur|animate|transition|duration-|hover:scale|ring-" \
  "frontend/app/(app)/collections/page.tsx" "frontend/app/(app)/users/page.tsx" \
  frontend/components/ui/ConfirmDialog.tsx frontend/components/layout/Sidebar.tsx
```

Expected: no matches (exit 1).

- [ ] **Step 3: Drive the screens in a real browser**

`docker compose up -d --build frontend` (the container serves a BUILT image, so a
restart alone shows the old bundle), or run a dev server on a spare port against
the same backend:

```bash
cd frontend && API_INTERNAL_URL=http://127.0.0.1:8000 npx next dev -p 3100
```

What has to be seen, not inferred: a 분류 created; the same name refused with
같은 이름의 분류가 이미 있습니다. under the form; an empty 분류 deleted; a
non-empty one refused with 문서 2개가 들어 있는 분류는 삭제할 수 없습니다.
inside the dialog; a role change that survives a reload; the last-admin 409; and
the 관리 section absent from a non-admin's sidebar.
