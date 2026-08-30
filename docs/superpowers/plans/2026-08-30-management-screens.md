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
  // "attachment" is a citation of a file the user attached to their own turn:
  // `filename` is set and chunk_id/document_id/page/section are all null, so
  // CitationBadge must not try to fetch a chunk for one.
  source_type: "rag" | "mcp" | "attachment";
  ref: string;
  chunk_id: string | null;
  document_id: string | null;
  filename: string | null;
  page: number | null;
  section: string | null;
  snippet: string;
  score: number | null;
}

/** POST /api/attachments, and the `attachments` array on a user MessageResponse.
 * `has_text` is whether the parser got anything out of a document; the text
 * itself is never sent to the browser. */
export interface Attachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  kind: "image" | "document";
  has_text: boolean;
  created_at: string;
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
  // Populated on user turns only, and only for a turn that carried files.
  attachments: Attachment[];
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
      // §4: a dialog is one of exactly two things in this app allowed a
      // box-shadow, because it genuinely floats above the page.
      className="w-full max-w-md rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
    >
      <div className="p-6">
        <h2 id="confirm-title" className="text-title font-medium">
          {title}
        </h2>
        <p className="mt-3 text-body text-on-surface-variant">{message}</p>
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" onClick={() => dialogRef.current?.close()} className="btn-text">
            취소
          </button>
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={busy}
            className="btn-danger"
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
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">분류 관리</h1>
      <ErrorBanner message={loadError} />

      {/* `user === null` is "not loaded yet", not "not an admin" - branching on
          the role alone tells every admin they lack permission for the length
          of the /api/auth/me round trip. The endpoints answer a non-admin with
          403 관리자 권한이 필요합니다. regardless; this only keeps buttons that
          cannot work off the screen. */}
      {user !== null && user.role !== "admin" ? (
        <p className="text-body text-on-surface-variant">분류 관리는 관리자만 할 수 있습니다.</p>
      ) : (
        <>
          {user !== null && (
            <form onSubmit={handleCreate} className="space-y-3 rounded-md bg-surface-container-low p-4">
              <div className="flex flex-wrap items-end gap-2">
                <div className="flex flex-col gap-1">
                  <label htmlFor="new-collection-name" className="text-body text-on-surface-variant">
                    분류 이름
                  </label>
                  <input
                    id="new-collection-name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                    maxLength={255}
                    className="field"
                  />
                </div>
                <div className="flex flex-1 flex-col gap-1">
                  <label htmlFor="new-collection-description" className="text-body text-on-surface-variant">
                    설명 (선택)
                  </label>
                  <input
                    id="new-collection-description"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    className="field w-full"
                  />
                </div>
                <button
                  type="submit"
                  disabled={creating}
                  className="btn-tonal"
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
            <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
          ) : collections.length === 0 ? (
            <p className="py-8 text-center text-body text-on-surface-variant">분류가 없습니다.</p>
          ) : (
            <div className="overflow-x-auto rounded-sm">
              <table className="w-full text-left text-body">
                <thead>
                  <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                    <th scope="col" className="px-3 py-3">분류 이름</th>
                    <th scope="col" className="px-3 py-3">설명</th>
                    <th scope="col" className="px-3 py-3 text-right">문서 수</th>
                    <th scope="col" className="px-3 py-3">등록일</th>
                    <th scope="col" className="px-3 py-3">관리</th>
                  </tr>
                </thead>
                <tbody>
                  {collections.map((c) => {
                    const editing = editingId === c.id;
                    return (
                      <tr key={c.id} className="border-b border-outline-variant align-top">
                        <td className="px-3 py-3">
                          {editing ? (
                            <input
                              value={editName}
                              onChange={(e) => setEditName(e.target.value)}
                              maxLength={255}
                              aria-label={`${c.name} 분류 이름`}
                              className="field h-8 w-full px-2 text-caption"
                            />
                          ) : (
                            c.name
                          )}
                        </td>
                        <td className="px-3 py-3 text-on-surface-variant">
                          {editing ? (
                            <input
                              value={editDescription}
                              onChange={(e) => setEditDescription(e.target.value)}
                              aria-label={`${c.name} 설명`}
                              className="field h-8 w-full px-2 text-caption"
                            />
                          ) : (
                            (c.description ?? "-")
                          )}
                        </td>
                        <td className="px-3 py-3 text-right text-on-surface-variant">
                          {counts === null ? "-" : (counts[c.id] ?? 0)}
                        </td>
                        <td className="px-3 py-3 text-on-surface-variant">
                          {new Date(c.created_at).toLocaleDateString()}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex gap-2">
                            {editing ? (
                              <>
                                <button
                                  type="button"
                                  onClick={() => void handleSave(c.id)}
                                  disabled={saving}
                                  className="btn-tonal btn-compact"
                                >
                                  저장
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setEditingId(null);
                                    setRowError(null);
                                  }}
                                  className="btn-tonal btn-compact"
                                >
                                  취소
                                </button>
                              </>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  onClick={() => startEdit(c)}
                                  className="btn-tonal btn-compact"
                                >
                                  수정
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setRowError(null);
                                    setDeleteTarget(c);
                                  }}
                                  className="btn-danger btn-compact"
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
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6">
      <h1 className="text-headline font-medium">사용자 관리</h1>
      <ErrorBanner message={loadError} />

      {users === null ? (
        !loadError && <p className="py-8 text-center text-body text-on-surface-variant">불러오는 중...</p>
      ) : users.length === 0 ? (
        <p className="py-8 text-center text-body text-on-surface-variant">사용자가 없습니다.</p>
      ) : (
        <div className="overflow-x-auto rounded-sm">
          <table className="w-full text-left text-body">
            <thead>
              <tr className="bg-surface-container-low text-label font-medium text-on-surface-variant">
                <th scope="col" className="px-3 py-3">이메일</th>
                <th scope="col" className="px-3 py-3">권한</th>
                <th scope="col" className="px-3 py-3">상태</th>
                <th scope="col" className="px-3 py-3">가입일</th>
                <th scope="col" className="px-3 py-3">관리</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-outline-variant align-top">
                  <td className="px-3 py-3">
                    {u.email}
                    {/* Which row is you is what makes 자신의 권한은 변경할 수
                        없습니다. readable as an explanation instead of a riddle. */}
                    {me?.id === u.id && <span className="ml-1 text-caption text-on-surface-variant">(나)</span>}
                  </td>
                  <td className="px-3 py-3">
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
                      className="field h-8 px-2 text-caption disabled:opacity-50"
                    >
                      {Object.entries(ROLE_LABEL).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-3">
                    <span className={u.is_active ? "text-on-surface" : "text-error"}>
                      {u.is_active ? "활성" : "비활성"}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-on-surface-variant">
                    {new Date(u.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-3 py-3">
                    {u.is_active ? (
                      <button
                        type="button"
                        onClick={() => {
                          setRowError(null);
                          setDeactivateTarget(u);
                        }}
                        className="btn-danger btn-compact"
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
                        className="btn-tonal btn-compact"
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
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import ErrorBanner from "@/components/ui/ErrorBanner";
import ThemeToggle from "@/components/ui/ThemeToggle";
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
  // Which row's ⋯ menu is open, which row is being renamed, and which row the
  // confirmation dialog is about. Three separate ids rather than one union,
  // because a rename and a delete are never in flight at once but the menu that
  // opened either of them has already closed by then.
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  // Escape discards a rename; a click away saves it. Both unmount the input, so
  // this is what a blur fired during that removal is checked against.
  const cancelRenameRef = useRef(false);

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

  async function commitRename(id: string) {
    const title = renameValue.trim();
    setRenamingId(null);
    // Nothing to save, and the server would answer 422 for a blank title. The
    // row keeps the name it had.
    if (!title) return;
    try {
      await apiFetch(`/api/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
    } catch (err) {
      setError(errorMessage(err));
      return;
    }
    // Reload rather than patching the array in place: PATCH bumps updated_at,
    // and this list is ordered by it, so the renamed row moves.
    await load();
  }

  async function confirmDelete(conversation: Conversation) {
    await apiFetch(`/api/conversations/${conversation.id}`, { method: "DELETE" });
    // Off the conversation that no longer exists, before the list reloads:
    // staying put would leave /chat/{id} rendering a 404 banner over an empty
    // transcript. push, not replace - Back should return to where they were.
    if (pathname === `/chat/${conversation.id}`) router.push("/chat");
    await load();
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
        className={`rounded-full px-4 py-2 text-label transition-colors duration-150 ${
          active
            ? "bg-primary-container font-medium text-on-primary-container"
            : "text-on-surface-variant hover:bg-surface-container-high"
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
    // No border-r. The sidebar separates from the page by tone -
    // surface-container-low against surface - which is the whole §1 principle
    // in one class. 280px per §6.
    <nav
      aria-label="주 메뉴"
      className="flex h-full w-sidebar flex-col gap-1 bg-surface-container-low p-3"
    >
      {/* §2: the gradient is allowed on the wordmark and nowhere else on this
          screen. */}
      <div className="mb-4 px-4 pt-2 text-title font-medium">
        <span className="text-gradient-brand">MOPAN</span>
      </div>
      {navLinks.map(navLink)}

      {/* `user` is null until /api/auth/me lands, so a non-admin never sees this
          appear and then vanish. flex-col on the wrapper because the links are
          <a> elements: as direct children of this flex column they stack on
          their own, but inside a plain div they would run side by side. */}
      {user?.role === "admin" && (
        <div className="mt-4">
          <div className="mb-1 px-4 text-caption tracking-wide text-on-surface-variant">관리</div>
          <div className="flex flex-col gap-1">{adminLinks.map(navLink)}</div>
        </div>
      )}

      <div className="mt-6 flex-1 overflow-y-auto">
        <div className="mb-1 px-4 text-caption tracking-wide text-on-surface-variant">
          대화 기록
        </div>
        {error && <ErrorBanner message={error} />}
        {!error && conversations?.length === 0 && (
          <p className="px-4 py-2 text-caption text-on-surface-variant">아직 대화가 없습니다.</p>
        )}
        {/* Which conversation you are in is the one piece of state a history
            list exists to convey, and the links carried only a hover style:
            measured at /chat/c3, every link's computed background was
            rgba(0,0,0,0). Same treatment as the nav links above. */}
        {conversations?.map((c) => {
          const active = pathname === `/chat/${c.id}`;

          // The rename is an inline field in the row rather than a third
          // dialog: the row is where the name is read, and a dialog to change
          // one string would be two more focus transitions for the same edit.
          if (renamingId === c.id) {
            return (
              <form
                key={c.id}
                onSubmit={(e) => {
                  e.preventDefault();
                  void commitRename(c.id);
                }}
                className="px-1 py-1"
              >
                <input
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Escape") return;
                    // stopPropagation, or the drawer's document-level Escape
                    // handler closes the whole sidebar behind the cancel.
                    e.stopPropagation();
                    cancelRenameRef.current = true;
                    setRenamingId(null);
                  }}
                  // Click-away saves. Escape is the only way to discard, and
                  // the ref is what tells the two apart if a browser fires
                  // blur while removing the focused input.
                  onBlur={() => {
                    if (cancelRenameRef.current) {
                      cancelRenameRef.current = false;
                      return;
                    }
                    void commitRename(c.id);
                  }}
                  aria-label={`대화 이름: ${c.title}`}
                  maxLength={200}
                  className="field w-full"
                />
              </form>
            );
          }

          return (
            <div
              key={c.id}
              // One blur handler for the row AND its menu: with it on the menu
              // alone, clicking the toggle to close fired blur first, closed
              // the menu, and the click then reopened it.
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setMenuFor(null);
              }}
              onKeyDown={(e) => {
                if (e.key !== "Escape" || menuFor !== c.id) return;
                e.stopPropagation();
                setMenuFor(null);
              }}
            >
              <div
                className={`flex items-center rounded-full transition-colors duration-150 ${
                  active ? "bg-primary-container" : "hover:bg-surface-container-high"
                }`}
              >
                <Link
                  href={`/chat/${c.id}`}
                  onClick={() => setOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={`min-w-0 flex-1 truncate rounded-full px-4 py-2 text-label ${
                    active ? "font-medium text-on-primary-container" : "text-on-surface-variant"
                  }`}
                >
                  {c.title}
                </Link>
                <button
                  type="button"
                  aria-label={`대화 메뉴: ${c.title}`}
                  aria-expanded={menuFor === c.id}
                  onClick={() => setMenuFor(menuFor === c.id ? null : c.id)}
                  className="mr-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-on-surface-variant transition-colors duration-150 hover:bg-surface-container-highest"
                >
                  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="currentColor">
                    <circle cx="12" cy="5" r="1.6" />
                    <circle cx="12" cy="12" r="1.6" />
                    <circle cx="12" cy="19" r="1.6" />
                  </svg>
                </button>
              </div>
              {menuFor === c.id && (
                // Inline, not an absolutely positioned popover: this list is
                // the sidebar's `overflow-y-auto` region, which CLIPS an
                // absolutely positioned child, so a floating menu on the last
                // visible row would be cut in half.
                <div className="my-1 flex flex-col rounded-md bg-surface-container py-1">
                  <button
                    type="button"
                    onClick={() => {
                      setMenuFor(null);
                      setRenameValue(c.title);
                      setRenamingId(c.id);
                    }}
                    className="px-4 py-2 text-left text-label text-on-surface transition-colors duration-150 hover:bg-surface-container-high"
                  >
                    이름 변경
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setMenuFor(null);
                      setDeleteTarget(c);
                    }}
                    className="px-4 py-2 text-left text-label text-error transition-colors duration-150 hover:bg-surface-container-high"
                  >
                    삭제
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* The one surviving divider in the sidebar: it separates the account
          block from a scrolling list, where a tonal step alone would read as
          "the list continues". §1 - borders where they carry meaning. */}
      <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant pt-3">
        {/* The placeholder is U+00A0, not an ASCII space: a plain space is
            collapsible, so the line gets no line box and is 0px tall until
            /api/auth/me lands - at which point it grows 16px and shoves
            로그아웃 down under the pointer already resting on it. */}
        <div className="truncate px-4 text-caption text-on-surface-variant">
          {user ? `${user.email}${user.role === "admin" ? " · 관리자" : ""}` : "\u00a0"}
        </div>
        <ThemeToggle />
        {logoutError && <ErrorBanner message={logoutError} />}
        {/* type="button" on every button in this file: the default is
            "submit", which is a live bug the moment one of them ends up
            inside a <form>. */}
        <button type="button" onClick={() => void handleLogout()} className="btn-tonal w-full">
          로그아웃
        </button>
      </div>
    </nav>
  );

  return (
    <>
      {/* Outside `content`, which is rendered TWICE - once docked, once in the
          drawer. Inside it, one showModal() call would open two dialogs and
          only the second would be reachable. */}
      {deleteTarget && (
        <ConfirmDialog
          title="대화 삭제"
          message={`"${deleteTarget.title}" 대화와 그 안의 모든 메시지가 삭제됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={() => confirmDelete(deleteTarget)}
          onClose={() => setDeleteTarget(null)}
        />
      )}
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
          className="icon-btn fixed left-2 top-2 z-20 bg-surface-container text-title md:hidden"
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
            className="flex-1 bg-scrim"
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

---

### Task 12: Chat attachments

**Files:**
- Create: `backend/app/models/attachment.py`, `backend/alembic/versions/0003_chat_attachments.py`, `backend/app/attachments/service.py`, `backend/app/attachments/router.py`
- Modify: the call sites listed step by step below.

**Decisions:**

**Attachments are per-message and per-user, not part of the shared RAG corpus.** That distinction is the whole permission model: `POST /api/documents` is admin-only precisely because those documents become the evidence base for every other user's answers, so an upload there is a corpus-poisoning vector. An attachment can only influence its own owner's answer, so any authenticated user may create one.

**Two steps, because the UI must show a thumbnail before the message is sent.** `POST /api/attachments` stores the file and returns an id; `POST /api/chat` gains `attachment_ids` and claims them onto the user message it creates. An attachment uploaded and never referenced is an orphan, identified by `message_id IS NULL` - no `expires_at` column, because `created_at` plus a TTL already answers the same question without a second migration.

**Extracted attachment text is UNTRUSTED, exactly like RAG evidence.** It is carried as `Evidence` so that it lands inside the same per-request nonce fence, goes through the same `_strip_fence_markers`, and competes for the same `ANSWER_CONTEXT_TOKEN_BUDGET` - never a second budget added on top.

**404, not 403, on someone else's attachment**, matching `get_owned_conversation`: a 403 would confirm the id exists.

- [ ] **Step 1: Write `backend/app/models/attachment.py`**

The one nullable FK in the schema, and the whole orphan story. The composer has to show a thumbnail before the message exists, so the row is written at upload and claimed onto its message afterwards; `message_id IS NULL AND created_at < now() - <ttl>` is then already a complete cleanup predicate, which is why there is no `expires_at` column and no second migration when a cleanup job is finally written.

```python
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

ATTACHMENT_KINDS = ("image", "document")


class Attachment(Base):
    """A file attached to ONE user's chat turn - deliberately not part of the
    shared RAG corpus. That is why any authenticated user may create one while
    POST /api/documents is admin-only: a corpus document becomes the evidence base
    for every other user's answers, so writing there is a corpus-poisoning vector,
    whereas an attachment can only ever influence its own owner's answer."""

    __tablename__ = "attachments"
    __table_args__ = (CheckConstraint("kind in ('image', 'document')", name="ck_attachments_kind_valid"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The only nullable FK in this schema, and the orphan story rests on it: the
    # composer has to show a thumbnail before the message exists, so a file is
    # stored first and claimed onto its message afterwards. NULL therefore means
    # "uploaded, never sent", and `message_id IS NULL AND created_at < now() -
    # <ttl>` is already a complete cleanup predicate - so no expires_at column and
    # no second migration when a cleanup job is finally written.
    # tests/test_schema.py:test_every_foreign_key_is_indexed_and_not_null carries
    # this one pair as its single documented exception.
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    # Extracted at UPLOAD time, not at answer time: the user is already waiting on
    # the model then, and a 40-page PDF parse is seconds of that wait. NULL for
    # kind 'image' - those reach the model as image parts, not as text.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 2: Write `backend/alembic/versions/0003_chat_attachments.py`**

`0002` is the previous head. Both directions must work: `tests/conftest.py:migrated_database` runs `downgrade base` at the start of every pytest session, so a broken `downgrade()` breaks the whole suite. The indexes belong to the table and go with it, so `downgrade` drops only the table.

```python
"""chat attachments

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable on purpose: the row exists from the moment the file is stored,
        # and is claimed onto its message only when the turn is persisted. NULL is
        # what makes an orphan findable later without another migration.
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_attachments"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_attachments_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name="fk_attachments_message_id_messages",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("kind in ('image', 'document')", name="ck_attachments_kind_valid"),
    )
    op.create_index("ix_attachments_user_id", "attachments", ["user_id"])
    op.create_index("ix_attachments_message_id", "attachments", ["message_id"])


def downgrade() -> None:
    # No explicit drop_index: they belong to the table and go with it. Every
    # pytest session starts with `downgrade base`, so this path runs constantly.
    op.drop_table("attachments")
```

- [ ] **Step 3: Write `backend/app/attachments/service.py`**

The part `/api/chat` needs, kept out of the router so `app.chat` does not import from another router. `to_evidence` is the security decision of this whole task: attachment text enters the prompt as ordinary `Evidence`, so it inherits the per-request nonce fence, `_strip_fence_markers` and the single `ANSWER_CONTEXT_TOKEN_BUDGET` for free instead of getting a parallel, more lenient path.

```python
import base64
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.storage import read_upload
from app.documents.validation import IMAGE_MIME, extension_of
from app.models.attachment import Attachment
from app.models.user import User
from app.retrieval.evidence import Evidence

# 404 for missing, for someone else's, and for already-claimed alike - the same
# rule get_owned_conversation follows, for the same reason: a 403 or a 409 here
# would let an id probe confirm that an attachment exists.
NOT_FOUND_MESSAGE = "첨부파일을 찾을 수 없습니다."
FILE_GONE_MESSAGE = "첨부파일의 원본을 더 이상 찾을 수 없습니다."


def attachment_root(upload_dir: Path) -> Path:
    """A subdirectory of UPLOAD_DIR, reusing the documents per-id layout wholesale
    (app/documents/storage.py). "attachments" can never collide with a document's
    directory name because that name is always a UUID."""
    return Path(upload_dir) / "attachments"


async def get_owned_attachment(db: AsyncSession, attachment_id: uuid.UUID, user: User) -> Attachment:
    attachment = await db.get(Attachment, attachment_id)
    if attachment is None or attachment.user_id != user.id:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE)
    return attachment


async def load_claimable(
    db: AsyncSession, attachment_ids: list[uuid.UUID], user: User
) -> list[Attachment]:
    """Every id must resolve to an unclaimed attachment owned by this user, or the
    whole request is refused. Called BEFORE /api/chat creates its conversation, so
    a bad id cannot leave a titled, empty conversation in the sidebar."""
    if not attachment_ids:
        return []
    unique = list(dict.fromkeys(attachment_ids))
    rows = (
        await db.scalars(
            select(Attachment).where(
                Attachment.id.in_(unique),
                Attachment.user_id == user.id,
                Attachment.message_id.is_(None),
            )
        )
    ).all()
    if len(rows) != len(unique):
        raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE)
    by_id = {row.id: row for row in rows}
    return [by_id[attachment_id] for attachment_id in unique]


async def claim(
    db: AsyncSession, attachment_ids: list[uuid.UUID], user_id: uuid.UUID, message_id: uuid.UUID
) -> None:
    """One conditional UPDATE, not a read-then-write: `message_id IS NULL` in the
    WHERE clause is what makes a double claim lose a race instead of quietly
    re-pointing an attachment that is already part of another turn."""
    if not attachment_ids:
        return
    result = await db.execute(
        update(Attachment)
        .where(
            Attachment.id.in_(attachment_ids),
            Attachment.user_id == user_id,
            Attachment.message_id.is_(None),
        )
        .values(message_id=message_id)
    )
    if result.rowcount != len(set(attachment_ids)):
        raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE)


def to_evidence(attachments: list[Attachment]) -> list[Evidence]:
    """Attachment text enters the prompt as ordinary Evidence, which is the whole
    security argument: it lands inside the same nonce fence, goes through the same
    _strip_fence_markers, and competes for the same token budget as corpus text. A
    user pasting "ignore previous instructions" in a PDF therefore gets exactly as
    far as an admin pasting it into a corpus document - nowhere."""
    return [
        Evidence(
            source_type="attachment",
            ref=f"attachment:{a.id}",
            content=a.extracted_text,
            # No retrieval score exists: nothing ranked this, the user chose it.
            score=None,
            metadata={"attachment_id": str(a.id), "filename": a.filename},
        )
        for a in attachments
        if a.kind == "document" and a.extracted_text
    ]


async def to_image_urls(attachments: list[Attachment]) -> list[str]:
    """data: URLs, not file paths or a public URL: the provider would have to be
    able to reach this host to fetch a URL, and these files are owner-scoped."""
    urls = []
    for attachment in attachments:
        if attachment.kind != "image":
            continue
        try:
            raw = await read_upload(attachment.storage_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=FILE_GONE_MESSAGE) from exc
        mime = IMAGE_MIME.get(extension_of(attachment.filename), attachment.content_type)
        urls.append(f"data:{mime};base64,{base64.b64encode(raw).decode()}")
    return urls
```

- [ ] **Step 4: Write `backend/app/attachments/router.py`**

Any authenticated user may attach, unlike `POST /api/documents`. The admin gate there exists because a corpus document becomes the evidence base for every other user's answers; an attachment is scoped to one conversation owned by one user, so the corpus-poisoning argument does not apply to it. Validation reuses `app/documents/validation.py` with an `allowed=` set rather than a second validator.

```python
import logging
import uuid
from urllib.parse import quote

from anyio import to_thread
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments.service import attachment_root, get_owned_attachment
from app.auth.dependencies import get_current_user
from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.core.logging import log_event
from app.documents.storage import delete_document_files, save_upload_stream
from app.documents.validation import (
    ATTACHMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    MAGIC_SNIFF_BYTES,
    UploadTooLarge,
    UploadValidationError,
    validate_magic_bytes,
    validate_upload_metadata,
)
from app.models.attachment import Attachment
from app.models.user import User
from app.rag.parsers import get_parser
from app.schemas.chat import AttachmentResponse

logger = logging.getLogger("mopan.attachments")
router = APIRouter(prefix="/api", tags=["attachments"])

UNREADABLE_MESSAGE = "첨부파일을 읽지 못했습니다. 파일이 손상되었는지 확인해 주세요."
NO_TEXT_MESSAGE = "첨부한 문서에서 읽을 수 있는 텍스트를 찾지 못했습니다. 다른 파일을 첨부해 주세요."
ALREADY_SENT_MESSAGE = "이미 전송된 첨부파일은 삭제할 수 없습니다."


def _no_vision_message(model: str) -> str:
    return (
        f"현재 답변 모델({model})은 이미지를 읽을 수 없습니다. "
        "이미지 대신 문서 파일을 첨부하거나 관리자에게 문의해 주세요."
    )


@router.post("/attachments", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """Any authenticated user, unlike POST /api/documents. The admin gate there
    exists because a corpus document becomes evidence for everybody's answers; an
    attachment is scoped to one conversation owned by one user, so the poisoning
    argument does not apply to it."""
    filename = (file.filename or "").strip()
    try:
        extension = validate_upload_metadata(
            filename,
            file.content_type or "",
            file.size or 0,
            settings.max_attachment_size_mb,
            allowed=ATTACHMENT_EXTENSIONS,
        )
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    kind = "image" if extension in IMAGE_EXTENSIONS else "document"
    # Refused here rather than at answer time. Storing an image the model can
    # never look at would let the user compose a whole message around a thumbnail
    # and only then be told it was ignored - and it is the one check that makes it
    # impossible for an image part to reach a text-only model at all.
    if kind == "image" and not settings.answer_model_supports_vision:
        raise HTTPException(status_code=400, detail=_no_vision_message(settings.answer_model))

    head = await file.read(MAGIC_SNIFF_BYTES)
    try:
        validate_magic_bytes(extension, head)
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await file.seek(0)

    attachment = Attachment(
        user_id=user.id,
        filename=filename[:500],
        content_type=(file.content_type or "application/octet-stream")[:255],
        size_bytes=0,
        storage_path="",
        kind=kind,
    )
    db.add(attachment)
    await db.flush()

    root = attachment_root(settings.upload_dir)
    try:
        path, size = await save_upload_stream(
            root,
            str(attachment.id),
            extension,
            file,
            max_bytes=settings.max_attachment_size_mb * 1024 * 1024,
        )
    except UploadTooLarge as exc:
        await db.rollback()
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    attachment.storage_path = str(path)
    attachment.size_bytes = size

    if kind == "document":
        parser = get_parser(extension)
        try:
            parsed = await to_thread.run_sync(parser.parse, str(path))
        except Exception as exc:
            logger.exception("attachment parse failed")
            await db.rollback()
            await delete_document_files(root, str(attachment.id))
            raise HTTPException(status_code=400, detail=UNREADABLE_MESSAGE) from exc
        text = "\n".join(block.text for block in parsed.blocks if block.text.strip())
        if not text.strip():
            # A scanned PDF parses fine and yields nothing. Accepting it would put
            # an attachment chip on screen that contributes literally nothing to
            # the answer, with no way for the user to tell.
            await db.rollback()
            await delete_document_files(root, str(attachment.id))
            raise HTTPException(status_code=400, detail=NO_TEXT_MESSAGE)
        attachment.extracted_text = text

    await db.commit()
    log_event(logger, "attachment_uploaded", attachment_id=str(attachment.id), kind=kind, size_bytes=size)
    return attachment


@router.get("/attachments/{attachment_id}", response_model=AttachmentResponse)
async def get_attachment(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    return await get_owned_attachment(db, attachment_id, user)


@router.get("/attachments/{attachment_id}/content")
async def get_attachment_content(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Backs the composer thumbnail and the attachment chips on a reloaded
    transcript."""
    attachment = await get_owned_attachment(db, attachment_id, user)
    # .html is an accepted attachment type and /api/* is proxied same-origin by
    # Next, so serving a stored file back under its own Content-Type would be
    # stored XSS on the app's own origin. Only the four image types - none of them
    # script-bearing, SVG is deliberately not in IMAGE_MIME - render inline;
    # everything else is an octet-stream download. nosniff stops a browser
    # second-guessing either one.
    inline = attachment.kind == "image"
    disposition = "inline" if inline else "attachment"
    return FileResponse(
        attachment.storage_path,
        media_type=attachment.content_type if inline else "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(attachment.filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
):
    """The composer's X button. Without it every removed file would sit as an
    orphan until a cleanup job that does not exist yet."""
    attachment = await get_owned_attachment(db, attachment_id, user)
    if attachment.message_id is not None:
        # 409, not the 404 an unowned id gets: this row is the caller's own and
        # they can see it, so there is nothing to conceal - only a transcript to
        # avoid rewriting.
        raise HTTPException(status_code=409, detail=ALREADY_SENT_MESSAGE)
    await db.delete(attachment)
    await db.commit()
    await delete_document_files(attachment_root(settings.upload_dir), str(attachment_id))
```

- [ ] **Step 5: Modify `backend/app/models/__init__.py`**

`alembic/env.py` imports `app.models`, so a model absent from here is invisible to `compare_metadata` and to `test_orm_matches_migrated_schema`.

```python
from app.models.attachment import ATTACHMENT_KINDS, Attachment
```

- [ ] **Step 6: Modify `backend/app/models/message.py`**

`MessageResponse` serialises this so a reloaded transcript can show what was attached. `viewonly` because the claim is a conditional UPDATE whose `message_id IS NULL` predicate is the double-claim guard, and a writable relationship would offer a second path around it.

```python
    # viewonly: the claim is a single conditional UPDATE (app/attachments/service.py)
    # whose `message_id IS NULL` predicate is the double-claim guard, and a writable
    # relationship would offer a second path that skips it. lazy="selectin" because
    # MessageResponse serialises this and the session is async, where a lazy load
    # at attribute access raises MissingGreenlet.
    attachments: Mapped[list[Attachment]] = relationship(
        lazy="selectin", order_by=Attachment.created_at, viewonly=True
    )
```

- [ ] **Step 7: Modify `backend/app/core/config.py`**

A separate ceiling from `MAX_UPLOAD_SIZE_MB`, because the two files are spent differently: a corpus document is chunked and reaches the model a few hundred tokens at a time, an attachment reaches it whole in one request.

```python
    # 10MB, a fifth of a corpus document's 50MB, because the two files are spent
    # differently. A corpus document is chunked and only ever reaches the model a
    # few hundred tokens at a time; an attachment reaches it whole, in ONE request
    # - an image base64-encoded (+33%, so 10MB of PNG is ~13.3MB on the wire,
    # inside OpenAI's documented 20MB-per-image ceiling) and a document as text
    # competing with the RAG evidence for ANSWER_CONTEXT_TOKEN_BUDGET.
    max_attachment_size_mb: int = 10
    max_attachments_per_message: int = 5
    # None -> derived from ANSWER_MODEL via VISION_CAPABLE_MODEL_PREFIXES.
    answer_model_supports_vision: bool | None = None
```

- [ ] **Step 8: Modify `backend/app/core/config.py`**

Vision support has to be asserted, not discovered - there is no capability query, and a text-only model answers an image part with an opaque 400. Conservative on purpose: a false negative costs the operator one env var, a false positive is the raw provider error this exists to prevent.

```python
# There is no capability query on the chat endpoint - a model that cannot see
# images answers an image part with an opaque 400 - so vision support has to be
# asserted, not discovered. Deliberately a short, conservative PREFIX allowlist:
# a false negative refuses an image upload with a Korean message naming the model,
# which an operator fixes with one env var (ANSWER_MODEL_SUPPORTS_VISION=true),
# while a false positive is the raw provider error this exists to prevent. Note
# what is NOT here: the o1/o3/o4 reasoning families, whose -mini members are
# text-only, so the whole family is left to the override.
VISION_CAPABLE_MODEL_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4-vision", "gpt-5", "chatgpt-4o")
```

- [ ] **Step 9: Modify `backend/app/documents/validation.py`**

Image types are kept out of `ALLOWED_EXTENSIONS` deliberately: that set gates `/api/documents`, and `app/rag/parsers` has no parser for an image, so an image in the corpus would pass upload and then fail in the worker with no route back.

```python
# Chat attachments only. Kept OUT of ALLOWED_EXTENSIONS on purpose: that set gates
# /api/documents, and app/rag/parsers has no parser for an image, so an image in
# the corpus would be accepted at upload and then fail in the worker with no
# route back. Callers opt in by passing `allowed=` below.
IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}
IMAGE_EXTENSIONS = set(IMAGE_MIME)
ATTACHMENT_EXTENSIONS = ALLOWED_EXTENSIONS | IMAGE_EXTENSIONS
```

- [ ] **Step 10: Modify `backend/app/schemas/chat.py`**

`extracted_text` is never returned - it is prompt input, sometimes megabytes. The composer only needs to know whether the file gave anything up.

```python
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


class ConversationResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 11: Modify `backend/app/llm/base.py`**

A plain string `content` stays a plain string on the wire, so every existing call site is untouched; only a message that actually carries an image becomes an OpenAI content array.

```python
    # data: URLs for chat attachments of kind 'image'. A plain string `content`
    # stays a plain string on the wire, so every existing call site is untouched;
    # only a message that actually carries an image becomes a content array.
    images: list[str] | None = None

    def to_openai(self) -> dict:
        payload: dict = {"role": self.role, "content": self.content}
        if self.images:
            payload["content"] = [{"type": "text", "text": self.content}] + [
                {"type": "image_url", "image_url": {"url": url}} for url in self.images
            ]
        if self.name is not None:
            payload["name"] = self.name
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        return payload
```

- [ ] **Step 12: Modify `backend/app/retrieval/evidence.py`**

Widening the Literal rather than adding a parallel channel is what makes the fence, the stripping and the budget apply to attachment text automatically.

```python
# "attachment" is text the user attached to this one turn. It rides the Evidence
# type rather than a parallel channel so that it inherits, for free, every defence
# build_prompt already applies to corpus text: the per-request nonce fence,
# _strip_fence_markers, and one shared token budget instead of a second one added
# on top.
SourceType = Literal["rag", "mcp", "attachment"]
```

- [ ] **Step 13: Modify `backend/app/chat/prompt.py`**

Images are not charged against `token_budget`: an image's cost is the provider's tile arithmetic on dimensions this layer never sees. `MAX_ATTACHMENTS_PER_MESSAGE` x `MAX_ATTACHMENT_SIZE_MB` is what bounds them.

```python
    # Images ride the question's own message. They are NOT charged against
    # token_budget: an image's cost is the provider's own tile arithmetic on
    # dimensions this layer never sees, and tiktoken cannot count it. What bounds
    # them instead is MAX_ATTACHMENTS_PER_MESSAGE x MAX_ATTACHMENT_SIZE_MB.
    #
    # RESIDUAL RISK, stated because it has no defence here: text rendered INSIDE an
    # image cannot be fenced, and ANSWER_SYSTEM_PROMPT deliberately says nothing
    # about it - measured, the shortest usable warning is 12 tokens and the system
    # prompt is already 190 of a 6000-token budget whose mandatory floor four
    # calibrated tests sit just under. Extracted DOCUMENT text has no such gap: it
    # arrives as Evidence and is fenced, stripped and budgeted like corpus text.
    messages.append(ChatMessage(role="user", content=question, images=images or None))
```

- [ ] **Step 14: Modify `backend/app/chat/service.py`**

The flush is not optional. `Message.id`'s `default=uuid.uuid4` is a *flush-time* default, so before it `user_message.id` is None - and the claim would then write `message_id = NULL`, match its own `IS NULL` predicate and report a rowcount that looks like success. This was a real defect, found by `test_an_attachment_is_claimed_onto_the_user_message`.

```python
    if attachment_ids:
        # AFTER both adds, so they still flush as one executemany and keep their
        # distinct clock_timestamp()s. The flush is not optional: Message.id's
        # `default=uuid.uuid4` is a *flush-time* default, so before it
        # user_message.id is None - and the claim below would then quietly write
        # message_id = NULL, match its own `IS NULL` predicate, and report a
        # rowcount that looks like success.
        await db.flush()
        await claim_attachments(db, attachment_ids, conversation.user_id, user_message.id)
```

- [ ] **Step 15: Modify `backend/app/chat/router.py`**

Before the `Conversation` is added, for the same reason the ownership check is: a bad attachment id must not leave a titled, empty conversation in the sidebar for the user to delete by hand.

```python
    # Before the conversation is created, for the same reason the ownership check
    # is: a bad attachment id must not leave a titled, empty conversation in the
    # sidebar that the user then has to delete by hand.
    attachment_ids = payload.attachment_ids or []
    if len(attachment_ids) > settings.max_attachments_per_message:
        raise HTTPException(
            status_code=400,
            detail=f"첨부파일은 한 번에 최대 {settings.max_attachments_per_message}개까지 보낼 수 있습니다.",
        )
    attachments = await load_claimable(db, attachment_ids, user)
    # Read off disk here, not inside the generator: a missing file is then a real
    # 404 with a Korean detail rather than an error frame inside a 200.
    images = await to_image_urls(attachments)
    attachment_evidence = to_evidence(attachments)
```

- [ ] **Step 16: Modify `backend/app/main.py`**

Amends Task 4's whole-file block with the new router.

```python
    from app.attachments.router import router as attachments_router
    from app.auth.router import router as auth_router
    from app.chat.router import router as chat_router
    from app.documents.router import router as documents_router
    from app.users.router import router as users_router

    app.include_router(attachments_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(documents_router)
    app.include_router(users_router)
```

- [ ] **Step 17: Modify `backend/tests/conftest.py`**

`attachments` first: it references `messages`, and the TRUNCATE runs CASCADE but the order is what documents the dependency.

```python
TABLES_IN_DELETE_ORDER = (
    "attachments",
    "messages",
    "conversations",
    "chunks",
    "documents",
    "collections",
    "users",
```

- [ ] **Step 18: Modify `backend/tests/test_schema.py`**

The exception is spelled out rather than dropped from the query, so adding a second nullable FK requires the same argument.

```python
# The single deliberate exception, spelled out rather than dropped from the query:
# attachments.message_id is NULL for a file that has been uploaded but not yet
# sent, which is the state the two-step attach flow exists to represent and the
# predicate a cleanup job will use. Adding a row here should require the same
# argument. NOT NULL is still enforced on the other half of the pair
# (attachments.user_id), so an attachment always has an owner.
NULLABLE_FK_EXCEPTIONS = {("attachments", "message_id")}
```

- [ ] **Step 19: Modify `backend/tests/test_documents_api.py`**

Amends Task 5's whole-file block. Upload, validation, authorization and content-serving tests live beside the document upload tests they reuse the machinery of.

```python
# --- Chat attachments --------------------------------------------------------
#
# Same upload machinery as a corpus document (app/documents/validation.py,
# app/documents/storage.py) and a deliberately DIFFERENT permission rule: writing
# to /api/documents is admin-only because those documents become the evidence base
# for everybody's answers, while an attachment can only ever influence its own
# owner's answer. member_client below is a plain non-admin user throughout, and is
# 403 on /api/documents in test_upload_requires_admin.

# A real 1x1 PNG: `filetype` sniffs the signature, so a made-up byte string would
# be rejected by validate_magic_bytes before any of these tests measured anything.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def upload_attachment(client, name="note.txt", data=b"hello", content_type="text/plain"):
    return await client.post("/api/attachments", files={"file": (name, data, content_type)})
```

- [ ] **Step 20: Modify `backend/tests/test_chat.py`**

The end-to-end half: 404s, the fence, the shared budget, the claim and the conversation-delete sweep.

```python
async def test_an_unknown_attachment_id_creates_no_conversation(logged_in, db):
    """The check runs before the Conversation is added, so a bad id cannot leave a
    titled, empty conversation in the sidebar for the user to clean up."""
    response = await logged_in.post(
        "/api/chat", json={"message": "hi", "attachment_ids": [str(uuid.uuid4())]}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "첨부파일을 찾을 수 없습니다."
    assert (await logged_in.get("/api/conversations")).json() == []
```

- [ ] **Step 21: Modify `backend/tests/test_prompt.py`**

The prompt-layer half of the security argument: a user pasting a PDF that says "ignore previous instructions" must not get one step further than an admin pasting the same sentence into a corpus document.

```python
async def test_a_fence_marker_in_attachment_text_is_neutralised(hostile):
    """A user pasting a PDF that says "ignore previous instructions" must not get
    one step further than an admin pasting the same sentence into a corpus
    document. Same assertion shape as the chunk-content case above."""
    template = await get_prompt("answer_agent")
    messages, _ = build_prompt(
        "question", [], [_attachment(hostile)], prompt=template, nonce=NONCE, token_budget=4000
    )
    fenced = fenced_message(messages).content

    assert fenced.count(f"<<EVIDENCE {NONCE}>>") == 1
    assert fenced.count(f"<<END EVIDENCE {NONCE}>>") == 1
    # Twice total: the nonce leaks nowhere else, so nothing inside the fence can
    # have reproduced it.
    assert fenced.count(NONCE) == 2
    # The instruction itself survives as text - it must, it may be the thing the
    # user is asking about - but it is inside the fence the system prompt tells
    # the model to treat as data, and it cannot close it.
    assert fenced.index("<<EVIDENCE") < fenced.index("<<END EVIDENCE")
```

- [ ] **Step 22: Modify `backend/tests/test_llm_provider.py`**

Paired with `test_chat_omits_the_tools_key_when_none_are_passed`, which asserts the text-only shape is unchanged.

```python
async def test_a_message_with_images_becomes_an_openai_content_array():
    """Chat attachments of kind 'image'. The array form is the only way OpenAI
    accepts an image, and the assertion in
    test_chat_omits_the_tools_key_when_none_are_passed above is the other half of
    this pair: a text-only message must stay a plain string, or every existing
    call site changes shape for a feature it does not use."""
    provider = _provider()
    message = MagicMock(content="ok", tool_calls=None)
    provider.client.chat.completions.create = AsyncMock(
        return_value=MagicMock(choices=[MagicMock(message=message)], usage=None, model="gpt-4o")
    )

    await provider.chat(
        [
            ChatMessage(role="system", content="rules"),
            ChatMessage(role="user", content="what is this?", images=["data:image/png;base64,AAAA"]),
        ]
    )

    sent = provider.client.chat.completions.create.await_args.kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "rules"}
    assert sent[1] == {
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }


async def test_an_empty_image_list_leaves_the_content_a_plain_string():
    """`images=[]` must not produce a one-element content array: an array with no
    image is a shape change for nothing, and `[]` is what a turn with only document
    attachments produces."""
    assert ChatMessage(role="user", content="hi", images=[]).to_openai() == {
        "role": "user",
        "content": "hi",
    }
```

- [ ] **Step 23: Modify `backend/tests/test_settings.py`**

The allowlist is the one piece of guesswork in this task, so it is pinned.

```python
def test_vision_support_is_derived_from_the_answer_model(model, expected):
    assert Settings(answer_model=model).answer_model_supports_vision is expected
```

- [ ] **Step 24: Modify `backend/tests/test_chat_service.py`**

`images` is data, like `evidence`: no session and no retrieval collaborator, which is the property that test is actually about.

```python
    # `images` is data, like `evidence`: chat attachments of kind 'image', already
    # read off disk by the caller. It carries no session and no retrieval
    # collaborator, which is the property this test is actually about.
    assert params == ["llm_provider", "question", "history", "evidence", "settings", "images"]
```

---

### Task 13: Attachment verification

- [ ] **Step 1: One pytest session, then ruff**

```bash
cd backend && python -m pytest && python -m ruff check .
```

ONE session, never `-n auto`: `migrated_database` runs `downgrade base`, and
concurrent sessions corrupt `mopan_test`.

- [ ] **Step 2: Stage each guard as a failure**

Write the guard, comment it out, watch the named test fail, restore it. The guards
staged this way: the ownership check in `get_owned_attachment`; the
`message_id IS NULL` predicate in `load_claimable`; `_strip_fence_markers` on
attachment text; merging attachment text into the evidence list rather than the
question; the Content-Type/extension check; the vision gate; the pre-conversation
attachment check; inline content serving; and the conversation-delete file sweep.

- [ ] **Step 3: Drive the endpoints against the live stack**

```bash
docker compose up -d --build backend worker
```

`--build`, not `restart`: the image COPYs the source, there is no bind mount, so a
restart runs the OLD code.

---

## Chat experience: attachments in the composer, markdown answers, and the rest

### Task 14: The composer, markdown rendering and conversation management

**Files:**
- Create: `frontend/components/chat/Composer.tsx`, `frontend/components/chat/AttachmentChip.tsx`, `frontend/components/chat/Markdown.tsx`
- Modify: `frontend/components/chat/ChatWindow.tsx`, `frontend/components/chat/MessageBubble.tsx`, `frontend/components/layout/Sidebar.tsx`, `frontend/lib/types.ts`, `frontend/lib/api.ts`, `frontend/app/globals.css`, `frontend/components/documents/DocumentTable.tsx`, `frontend/package.json`, `backend/app/chat/router.py`, `backend/tests/test_chat.py`

**Interfaces:**
- Consumes: Task 12's attachment API (`POST /api/attachments`, `GET /api/attachments/{id}/content`, `DELETE /api/attachments/{id}`, `attachment_ids` on `POST /api/chat`, `attachments` on `MessageResponse`).
- Produces: `PATCH /api/conversations/{id}` — the only backend addition here.

## Decisions

**Markdown comes back, and its configuration IS the security argument.** Slice 1
shipped no renderer at all and a reviewer praised that the XSS surface had been
"designed away rather than configured away". Reopening it costs three specific
commitments: no `rehype-raw` and no `dangerouslySetInnerHTML` anywhere, so
react-markdown rewrites every `raw` hast node into a TEXT node and
`<img src=x onerror=alert(1)>` in an answer is characters on screen; hrefs go
through react-markdown's `defaultUrlTransform`, whose allowlist
`/^(https?|ircs?|mailto|xmpp)$/i` empties a `javascript:` href; and the `[n]`
badge pass runs on hast TEXT nodes rather than as a regex over rendered HTML.

**The citation pass keeps its old failure mode exactly.** `renderContent`'s one
security-load-bearing line was that an unresolvable `[9]` is skipped WITHOUT
advancing the cursor, so a forged marker survives as inert text. `splitMarkers`
below is that same loop; markdown only adds `inCode`, which leaves a `[1]` under
`<code>` or `<pre>` alone so a fenced block is never linkified.

**Uploads start on selection, not on send.** A refusal — `지원하지 않는 파일
형식입니다: .mp4`, the 413, the no-vision 400 — has to be on screen while the
user is still writing the question. It renders on the chip it belongs to and not
in the page banner, because with five files in the row a banner cannot say which
one it is about.

**중지 puts the question back in the composer.** An abort lands before the
router's phase 3, so `persist_turn` never runs and the backend has nothing. That
is the same state an `error` frame leaves, and the existing code already removes
the pending bubble there for the same reason: a question on screen that no reload
can reproduce is worse than an empty composer.

**The conversation menu is inline, not a popover.** The history list is the
sidebar's `overflow-y-auto` region, which CLIPS an absolutely positioned child —
a floating menu on the last visible row would be cut in half.

**Rename is `PATCH`, and it bumps `updated_at`.** The list is ordered by that
column, so a renamed conversation moves to the top. That is an update to the row
the list is showing, not a bug.


- [ ] **Step 1: Modify `backend/app/chat/router.py`**

The one backend addition. 404 for a missing id and for someone else's alike — `get_owned_conversation`'s rule unchanged, so a rename cannot probe for ids the way a 403 would let it.

```python
class ConversationUpdate(BaseModel):
    """The rename body. Local to this router rather than app/schemas/chat.py
    because `title` is the whole thing and nothing else consumes it.

    500 is the column width; 200 is the bound offered to a human. A sidebar row
    truncates at ~30 characters, so anything past that is invisible in the one
    place the title is read - and the auto-generated title is `message[:80]`, so
    200 is already generous against the only other writer of this field."""

    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def _stripped_and_not_blank(cls, value: str) -> str:
        # min_length runs before this, so "   " gets past it. A whitespace-only
        # title renders as an unclickable-looking blank row in the history list.
        stripped = value.strip()
        if not stripped:
            raise ValueError("대화 제목을 입력해 주세요.")
        return stripped
```


```python
@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """The sidebar's 이름 변경. Auto-titling takes `message[:80]` of the first
    question, which is a sentence fragment more often than it is a name.

    404 for a missing id and for someone else's alike - get_owned_conversation's
    rule, unchanged, so a rename cannot be used to probe for ids the way a 403
    would let it. Note this bumps `updated_at`, so a renamed conversation moves to
    the top of the history list: that list is ordered by updated_at, and a rename
    is an update to the row the list is showing."""
    conversation = await get_owned_conversation(db, conversation_id, user)
    conversation.title = payload.title
    await db.commit()
    # `updated_at` is `onupdate=func.now()`, a SERVER-side expression, so the
    # UPDATE leaves that one attribute expired whatever expire_on_commit says -
    # and response serialisation then touches it outside the greenlet and raises
    # MissingGreenlet. This is the load that makes the value real.
    await db.refresh(conversation)
    return conversation
```


- [ ] **Step 2: Modify `backend/tests/test_chat.py`**

Three tests, plus the `PATCH` line added to the two route-enumerating auth tests above them. A whitespace-only title passes `min_length` and would render as an empty, unclickable-looking row in the history list.

```python
async def test_renaming_a_conversation_changes_the_title_in_the_list(logged_in):
    """Auto-titling takes message[:80], which is a sentence fragment more often
    than it is a name - the rename is the only way to fix that."""
    conversation_id = await start_conversation(logged_in, "hello")

    response = await logged_in.patch(
        f"/api/conversations/{conversation_id}", json={"title": "  분기 보고서  "}
    )

    assert response.status_code == 200
    # Stripped, not stored verbatim: the sidebar row is `truncate`d, so leading
    # whitespace is invisible padding the user cannot see or delete.
    assert response.json()["title"] == "분기 보고서"
    assert (await logged_in.get("/api/conversations")).json()[0]["title"] == "분기 보고서"


async def test_renaming_an_unknown_conversation_is_404(logged_in):
    """Indistinguishable from someone else's id, the same rule every other
    conversation route follows."""
    response = await logged_in.patch(f"/api/conversations/{uuid.uuid4()}", json={"title": "x"})
    assert response.status_code == 404
    assert response.json()["detail"] == "대화를 찾을 수 없습니다."


@pytest.mark.parametrize("title", ["", "   ", "가" * 201])
async def test_a_blank_or_overlong_title_is_refused(logged_in, title):
    """A whitespace-only title passes min_length and would render as an empty,
    unclickable-looking row in the history list."""
    conversation_id = await start_conversation(logged_in)

    response = await logged_in.patch(f"/api/conversations/{conversation_id}", json={"title": title})

    assert response.status_code == 422
    assert (await logged_in.get("/api/conversations")).json()[0]["title"] == "hello"
```


- [ ] **Step 3: Modify `frontend/lib/types.ts`**

`source_type` gains the third source Task 12 shipped, and `Message` gains the array that a reloaded transcript renders its attachment chips from.

```typescript
export interface Attachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  kind: "image" | "document";
  has_text: boolean;
  created_at: string;
}
```


```typescript
  source_type: "rag" | "mcp" | "attachment";
```


```typescript
  // Populated on user turns only, and only for a turn that carried files.
  attachments: Attachment[];
```


- [ ] **Step 4: Modify `frontend/lib/api.ts`**

`streamChat`'s body type only. Nothing else in the module changes.

```typescript
    conversation_id?: string | null;
    message: string;
    collection_ids?: string[];
    attachment_ids?: string[];
```


- [ ] **Step 5: Write `frontend/components/chat/Markdown.tsx`**

`react-markdown` + `remark-gfm`, and nothing else. The `citation` tag is produced only by the plugin in this file, so nothing an answer contains can reach that component override.

```tsx
"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import CitationBadge from "@/components/chat/CitationBadge";
import type { Citation } from "@/lib/types";

// Slice 1 shipped no markdown renderer at all, and a security reviewer praised
// that the XSS surface had been "designed away rather than configured away".
// This file puts it back, so the configuration IS the security argument:
//
//   - NO `rehype-raw` and NO dangerouslySetInnerHTML anywhere. Without
//     rehype-raw, react-markdown rewrites every `raw` hast node into a TEXT
//     node before rendering (react-markdown/lib/index.js:355), so
//     `<img src=x onerror=alert(1)>` in an answer is characters on screen, not
//     an element in the DOM.
//   - Link hrefs go through react-markdown's `defaultUrlTransform`, whose
//     protocol allowlist is /^(https?|ircs?|mailto|xmpp)$/i - a `javascript:`
//     href is emptied before it ever reaches the anchor.
//   - The `[n]` -> badge pass runs on the hast TEXT nodes below, never as a
//     regex over rendered HTML, and it resolves each marker against the
//     message's own citations array before emitting anything.
const MARKER = /\[(\d{1,2})\]/g;

/** The subset of hast this file touches. Declared locally rather than imported
 * from @types/hast, which is only here as react-markdown's transitive dep. */
type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

/** The security-load-bearing half of MessageBubble's old renderContent, moved
 * onto the markdown tree and otherwise unchanged in behaviour:
 *
 *   - a marker whose number is not in `byIndex` is skipped WITHOUT advancing the
 *     cursor, so a forged "[9]" in an answer survives as literal text rather
 *     than becoming a badge pointing at nothing;
 *   - the badge is built from the resolved Citation object, so there is no path
 *     from attacker-chosen text to a link target.
 *
 * `inCode` is the part markdown adds: a text node under <code> or <pre> is left
 * alone, so `[1]` inside inline code or a fenced block stays visible source. */
function splitMarkers(value: string, byIndex: Map<number, Citation>): HastNode[] {
  const nodes: HastNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  MARKER.lastIndex = 0;

  while ((match = MARKER.exec(value)) !== null) {
    if (!byIndex.has(Number(match[1]))) continue;
    if (match.index > cursor) nodes.push({ type: "text", value: value.slice(cursor, match.index) });
    nodes.push({
      type: "element",
      tagName: "citation",
      properties: { dataIndex: match[1] },
      children: [],
    });
    cursor = match.index + match[0].length;
  }
  if (nodes.length === 0) return [{ type: "text", value }];
  if (cursor < value.length) nodes.push({ type: "text", value: value.slice(cursor) });
  return nodes;
}

function citationMarkers(citations: Citation[]) {
  const byIndex = new Map(citations.map((c) => [c.index, c]));

  function walk(node: HastNode, inCode: boolean): void {
    if (!node.children) return;
    const next: HastNode[] = [];
    for (const child of node.children) {
      if (child.type === "element") {
        // `pre` as well as `code`: a fenced block is <pre><code>, and a `pre`
        // with no `code` inside it is still preformatted source.
        walk(child, inCode || child.tagName === "code" || child.tagName === "pre");
        next.push(child);
      } else if (child.type === "text" && !inCode && typeof child.value === "string") {
        next.push(...splitMarkers(child.value, byIndex));
      } else {
        // `raw` nodes land here. They are rewritten to text by react-markdown
        // AFTER every rehype plugin has run, so a `[1]` inside a would-be HTML
        // tag is never linkified - the safe direction.
        next.push(child);
      }
    }
    node.children = next;
  }

  return () => (tree: HastNode) => walk(tree, false);
}

export default function Markdown({
  content,
  citations,
}: {
  content: string;
  citations: Citation[];
}) {
  const byIndex = new Map(citations.map((c) => [c.index, c]));
  // Cast: `citation` is not an HTML tag name, and hast-util-to-jsx-runtime's
  // Components type is keyed on JSX.IntrinsicElements. The tag is produced only
  // by the plugin above, so nothing else can reach this component.
  const components = {
    citation({ node }: { node?: HastNode }) {
      const citation = byIndex.get(Number((node?.properties as { dataIndex?: string })?.dataIndex));
      return citation ? <CitationBadge citation={citation} /> : null;
    },
    // rel on every link: these hrefs come out of a model answer, so an answer
    // must not be able to hand a target window a reference back to this one.
    a({ href, children }: { href?: string; children?: React.ReactNode }) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer nofollow">
          {children}
        </a>
      );
    },
    // The one wrapper markdown cannot express: a GFM table wider than the 768px
    // reading column has to scroll inside itself, or it scrolls the page (§9.8).
    table({ children }: { children?: React.ReactNode }) {
      return (
        <div className="my-3 overflow-x-auto">
          <table>{children}</table>
        </div>
      );
    },
  } as Components;

  return (
    <div className="markdown text-body-lg text-on-surface">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[citationMarkers(citations)]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
```


- [ ] **Step 6: Write `frontend/components/chat/AttachmentChip.tsx`**

One chip, used by the composer and by a sent user turn. The composer passes a `blob:` preview and an `onRemove`; a transcript passes `/api/attachments/{id}/content` and no remove. The filename IS the `alt` text.

```tsx
import { formatSize } from "@/components/documents/DocumentTable";

/** One attached file, in the composer and on a sent user turn alike. The two
 * differ only in what they pass: the composer passes a blob: preview and an
 * onRemove, a transcript passes /api/attachments/{id}/content and no remove. */
export default function AttachmentChip({
  filename,
  sizeBytes,
  kind,
  src,
  status,
  error,
  onRemove,
}: {
  filename: string;
  sizeBytes: number;
  kind: "image" | "document";
  src?: string | null;
  status?: "uploading" | "ready";
  error?: string | null;
  onRemove?: () => void;
}) {
  return (
    <div
      className={`flex max-w-[17rem] items-center gap-2 rounded-md px-2 py-1.5 ${
        error ? "bg-error-container text-on-error-container" : "bg-surface-container-high"
      }`}
    >
      {kind === "image" && src && !error ? (
        // The filename IS the alt text: "이미지" would tell a screen-reader user
        // nothing they could act on, and the filename is the only thing
        // distinguishing one attachment from the next in this row.
        <img
          src={src}
          alt={filename}
          className="h-10 w-10 shrink-0 rounded-xs object-cover"
        />
      ) : (
        <span
          aria-hidden="true"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xs bg-surface-container text-on-surface-variant"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
            <path d="M14 3v5h5" />
          </svg>
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-caption font-medium text-on-surface" title={filename}>
          {filename}
        </div>
        {/* The refusal renders HERE, on the attachment it belongs to, not as a
            page-level banner: with five chips in the row a banner cannot say
            which file it is about. A reason is the one line that must NOT be
            truncated - "지원하지 않는 파일 형식입…" tells the user nothing they
            can act on - so it wraps while a size stays on one line. */}
        <div
          className={
            error ? "break-keep text-caption" : "truncate text-caption text-on-surface-variant"
          }
        >
          {error ?? (status === "uploading" ? "업로드 중…" : formatSize(sizeBytes))}
        </div>
      </div>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`첨부 삭제: ${filename}`}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-on-surface-variant transition-colors duration-150 hover:bg-surface-container-highest"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      )}
    </div>
  );
}
```


- [ ] **Step 7: Write `frontend/components/chat/Composer.tsx`**

§8's composer block. The IME guard is three checks because no single one is portable: `isComposing` is the standard, `keyCode === 229` is what the engines that predate it report, and the ref covers an engine that fires `compositionend` late. A Korean user pressing Enter to confirm a Hangul candidate must not send.

```tsx
"use client";

import { useEffect, useRef } from "react";
import AttachmentChip from "@/components/chat/AttachmentChip";
import type { Attachment } from "@/lib/types";

/** One file the user has chosen. It exists on screen from the moment it is
 * picked, before POST /api/attachments has answered, so that a refusal can be
 * rendered on the chip it belongs to rather than as a page-level banner. */
export type PendingAttachment = {
  /** Local, and NOT the attachment id: a chip exists before the server has
   * given it one, and an upload that is refused never gets one at all. */
  key: string;
  filename: string;
  sizeBytes: number;
  kind: "image" | "document";
  /** A blob: URL made from the File, so an image thumbnail appears immediately
   * and without a second round trip. Revoked by ChatWindow. */
  previewUrl: string | null;
  status: "uploading" | "ready" | "error";
  /** The server's row, once POST /api/attachments has answered. It is what the
   * send carries (its id) and what the sent user turn renders from. */
  attachment: Attachment | null;
  error: string | null;
};

// backend/app/documents/validation.py: ALLOWED_EXTENSIONS | IMAGE_EXTENSIONS.
export const ATTACHMENT_EXTENSIONS = [
  "pdf",
  "docx",
  "txt",
  "md",
  "html",
  "png",
  "jpg",
  "jpeg",
  "webp",
  "gif",
];
const ACCEPT = ATTACHMENT_EXTENSIONS.map((ext) => `.${ext}`).join(",");

// 8 rows of body-lg (26px) plus the textarea's own 8px padding top and bottom.
const MAX_HEIGHT = 8 * 26 + 16;

export default function Composer({
  value,
  onChange,
  onSubmit,
  onFiles,
  attachments,
  onRemove,
  sending,
  onStop,
  textareaRef,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onFiles: (files: File[]) => void;
  attachments: PendingAttachment[];
  onRemove: (key: string) => void;
  sending: boolean;
  onStop: () => void;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  // Chrome fires keydown(Enter) with isComposing=true while a Hangul syllable
  // is still being composed, but not every engine does; this ref is the second
  // half of the same guard, set from the composition events themselves.
  const composingRef = useRef(false);

  // Auto-grow, 1 to 8 rows. height:auto first, or scrollHeight only ever
  // reports the height it already has and the box can never shrink again after
  // the user deletes a line.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
  }, [value, textareaRef]);

  function take(list: FileList | null) {
    if (!list?.length) return;
    onFiles(Array.from(list));
  }

  return (
    // §8: one surface-container block at --radius-xl, no border at rest, a 2px
    // primary outline on focus-within, thumbnails in a row above the textarea
    // and inside the same block.
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="rounded-xl bg-surface-container p-2 outline-primary transition-colors duration-150 focus-within:outline focus-within:outline-2"
    >
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 p-1 pb-2">
          {attachments.map((a) => (
            <AttachmentChip
              key={a.key}
              filename={a.filename}
              sizeBytes={a.sizeBytes}
              kind={a.kind}
              src={a.previewUrl}
              status={a.status === "uploading" ? "uploading" : "ready"}
              error={a.error}
              onRemove={() => onRemove(a.key)}
            />
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        {/* The keyboard-reachable equivalent of dropping a file on the
            transcript, and the only one: a drop target cannot be focused or
            activated from the keyboard at all. */}
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          aria-label="파일 첨부"
          className="icon-btn"
        >
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          // Cleared after every pick, or choosing the SAME file twice in a row
          // fires no change event and the second attachment never appears.
          onChange={(e) => {
            take(e.target.files);
            e.target.value = "";
          }}
        />

        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey) return;
            // A Korean user pressing Enter to CONFIRM a Hangul candidate must
            // not send the message - that Enter belongs to the IME. Three
            // checks because no single one is portable: `isComposing` is the
            // standard, keyCode 229 is what the engines that predate it report,
            // and the ref covers an engine that fires compositionend late.
            if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229 || composingRef.current) {
              return;
            }
            // Shift+Enter is handled by the early return above: it falls
            // through to the textarea's own newline insertion.
            e.preventDefault();
            onSubmit();
          }}
          onPaste={(e) => {
            // ONLY when the clipboard actually carries a file. Pasting text has
            // to keep working, so an empty `files` list is left entirely alone -
            // no preventDefault, no interception.
            if (e.clipboardData.files.length === 0) return;
            e.preventDefault();
            take(e.clipboardData.files);
          }}
          placeholder="질문을 입력하세요"
          // A placeholder is not an accessible name: it is dropped the moment
          // the field has text, and some screen readers never announce it.
          aria-label="질문"
          style={{ maxHeight: MAX_HEIGHT }}
          className="min-w-0 flex-1 resize-none bg-transparent px-2 py-2 text-body-lg text-on-surface placeholder:text-on-surface-variant focus:outline-none"
        />

        {/* The two `key`s are load-bearing, and this was measured. Without them
            React reuses ONE <button> DOM node across the branch and only
            rewrites its `type`. A click's activation behaviour reads `type`
            AFTER the listeners have run, so pressing 중지 went: click ->
            onStop -> abort -> the rejection's setState flushes -> the very same
            node is now type="submit" -> the browser submits the form -> the
            question that 중지 had just restored to the composer was sent
            straight back out. Observed as a second `sending` render with no
            second click, and the 중지 button never going away. Distinct keys
            make React replace the node instead, and a detached button has no
            form owner to submit. */}
        {sending ? (
          // The AbortController ChatWindow already held for unmount, surfaced.
          <button
            key="stop"
            type="button"
            onClick={onStop}
            aria-label="답변 생성 중지"
            className="h-10 shrink-0 rounded-full bg-surface-container-high px-5 text-label font-medium text-on-surface transition-colors duration-150 hover:bg-surface-container-highest"
          >
            중지
          </button>
        ) : (
          // Filled when there is something to send, tonal when there is not -
          // the button says whether the composer is ready without a word.
          <button
            key="send"
            type="submit"
            className={`h-10 shrink-0 rounded-full px-5 text-label font-medium transition-colors duration-150 ${
              value.trim()
                ? "bg-primary text-on-primary"
                : "bg-surface-container-high text-on-surface-variant"
            }`}
          >
            전송
          </button>
        )}
      </div>
    </form>
  );
}
```


- [ ] **Step 8: Write `frontend/components/chat/ChatWindow.tsx`**

Attachment state, drag-and-drop with a depth counter, 중지, the §8 empty state and its suggestion chips. `pointer-events-none` on the drop overlay is load-bearing: an overlay that takes the pointer fires `dragleave` the instant it appears.

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, errorMessage, streamChat } from "@/lib/api";
import ErrorBanner from "@/components/ui/ErrorBanner";
import Composer, {
  ATTACHMENT_EXTENSIONS,
  type PendingAttachment,
} from "@/components/chat/Composer";
import MessageBubble from "@/components/chat/MessageBubble";
import type { Attachment, Message } from "@/lib/types";

const STATUS_LABEL: Record<string, string> = {
  searching: "문서 검색 중…",
  answering: "답변 생성 중…",
};

// settings.max_attachments_per_message and settings.max_attachment_size_mb. The
// server is the real boundary and refuses both in Korean; these only spare the
// user an upload that ends in a 400 or a 413, and they are worded identically to
// the server's own refusals so the two can never read as different rules.
const MAX_ATTACHMENTS = 5;
const MAX_ATTACHMENT_MB = 10;

// §8: 3-4 chips that fill the composer when clicked. Deliberately about
// documents and not about the corpus that happens to be loaded - this is a
// document-QA product and the corpus is incidental to it.
const SUGGESTIONS = [
  "이 문서의 핵심 내용을 세 줄로 요약해 주세요",
  "첨부한 파일에서 주요 수치를 표로 정리해 주세요",
  "두 문서의 내용이 어긋나는 부분을 찾아 주세요",
  "규정에서 담당자의 의무가 무엇인지 알려 주세요",
];

function rejection(file: File): string | null {
  // Same rule as validation.py's extension_of: no dot means no extension, not
  // "the whole name is the extension".
  const extension = file.name.includes(".") ? file.name.split(".").pop()!.toLowerCase() : "";
  if (!ATTACHMENT_EXTENSIONS.includes(extension)) {
    return `지원하지 않는 파일 형식입니다: .${extension}`;
  }
  if (file.size > MAX_ATTACHMENT_MB * 1024 * 1024) {
    return `파일이 최대 크기 ${MAX_ATTACHMENT_MB}MB를 초과했습니다.`;
  }
  return null;
}

export default function ChatWindow({
  initialConversationId,
}: {
  initialConversationId: string | null;
}) {
  const router = useRouter();
  const [conversationId, setConversationId] = useState<string | null>(initialConversationId);
  const [messages, setMessages] = useState<Message[]>([]);
  // "no messages", "not loaded yet" and "the load failed" are three states, and
  // the empty-state line below belongs to only the first - the same distinction
  // the Sidebar draws for its conversation list, its `!error &&` included.
  // Without `loaded`, every arrival at /chat/{id} flashes the greeting before
  // the transcript lands: measured at 40ms over loopback, and it is a network
  // round trip, so it is only ever longer in front of a real user. Without
  // `!error`, a failed load shows that same invitation stacked on top of the
  // error banner, because setLoaded runs in finally() and a rejected fetch is
  // therefore loaded-and-empty.
  const [loaded, setLoaded] = useState(!initialConversationId);
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
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

  useEffect(() => {
    if (!initialConversationId) return;
    apiFetch<Message[]>(`/api/conversations/${initialConversationId}/messages`)
      .then(setMessages)
      .catch((err) => setError(errorMessage(err)))
      .finally(() => setLoaded(true));
  }, [initialConversationId]);

  useEffect(() => {
    // §7: under `reduce` the app must be fully usable with zero animation, and
    // a CSS override cannot reach a behavior passed to scrollIntoView. The jump
    // still lands on the same element - only the tween goes.
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    bottomRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth" });
  }, [messages, status]);

  async function upload(key: string, file: File) {
    const controller = new AbortController();
    uploadsRef.current.set(key, controller);
    const form = new FormData();
    form.append("file", file);
    try {
      // apiFetch handles FormData correctly: it only sets a JSON Content-Type
      // for string bodies, so the browser's multipart boundary survives.
      const created = await apiFetch<Attachment>("/api/attachments", {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
      setAttachments((prev) =>
        prev.map((a) =>
          a.key === key
            ? { ...a, status: "ready", attachment: created, sizeBytes: created.size_bytes }
            : a,
        ),
      );
      setNotice(`${created.filename} 첨부됨`);
    } catch (err) {
      // The chip was removed while this was in flight; there is nothing left to
      // report the failure on.
      if ((err as { name?: string } | null)?.name === "AbortError") return;
      const message = errorMessage(err);
      // Onto the chip, not into the page banner: with five files in the row a
      // banner cannot say which one 지원하지 않는 파일 형식입니다 is about.
      setAttachments((prev) =>
        prev.map((a) => (a.key === key ? { ...a, status: "error", error: message } : a)),
      );
      setNotice(`${file.name} 첨부 실패: ${message}`);
    } finally {
      uploadsRef.current.delete(key);
    }
  }

  function addFiles(files: File[]) {
    setError(null);
    const room = MAX_ATTACHMENTS - attachments.length;
    if (files.length > room) {
      // The one refusal that has no chip to live on, because the files it
      // refuses never become chips. Same sentence the server answers with.
      setError(`첨부파일은 한 번에 최대 ${MAX_ATTACHMENTS}개까지 보낼 수 있습니다.`);
    }
    for (const file of files.slice(0, Math.max(room, 0))) {
      const key = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const refusal = rejection(file);
      const isImage = file.type.startsWith("image/");
      // A blob: URL, not a FileReader data: URL: it is synchronous, so the
      // thumbnail is on screen in the same frame the file was chosen.
      const previewUrl = isImage && !refusal ? URL.createObjectURL(file) : null;
      if (previewUrl) previewUrlsRef.current.push(previewUrl);
      setAttachments((prev) => [
        ...prev,
        {
          key,
          filename: file.name,
          sizeBytes: file.size,
          kind: isImage ? "image" : "document",
          previewUrl,
          status: refusal ? "error" : "uploading",
          attachment: null,
          error: refusal,
        },
      ]);
      // The upload starts NOW, on selection, not on send: the thumbnail and any
      // refusal have to be on screen while the user is still writing the
      // question, not after they have pressed 전송.
      if (!refusal) void upload(key, file);
      else setNotice(`${file.name} 첨부 실패: ${refusal}`);
    }
  }

  function removeAttachment(key: string) {
    const entry = attachments.find((a) => a.key === key);
    if (!entry) return;
    uploadsRef.current.get(key)?.abort();
    if (entry.previewUrl) URL.revokeObjectURL(entry.previewUrl);
    setAttachments((prev) => prev.filter((a) => a.key !== key));
    setNotice(`${entry.filename} 첨부 삭제됨`);
    // Only a row that actually exists server-side. A refused file was never
    // stored, and DELETE on its (absent) id would answer 404 and put a banner
    // on screen for a removal that worked.
    if (entry.attachment) {
      apiFetch(`/api/attachments/${entry.attachment.id}`, { method: "DELETE" }).catch((err) =>
        setError(errorMessage(err)),
      );
    }
  }

  async function handleSend() {
    if (!input.trim() || sending) return;
    if (attachments.some((a) => a.status === "uploading")) {
      setError("첨부파일 업로드가 끝난 뒤에 보내 주세요.");
      return;
    }

    const question = input;
    const sent = attachments.filter((a) => a.attachment !== null).map((a) => a.attachment!);
    const pendingId = `temp-${Date.now()}`;
    const controller = new AbortController();
    abortRef.current = controller;
    stoppedRef.current = false;
    setInput("");
    // Cleared here rather than on `done`: these rows are claimed by the send,
    // so leaving the chips up would offer a 삭제 that now answers 409
    // 이미 전송된 첨부파일은 삭제할 수 없습니다.
    setAttachments([]);
    setError(null);
    setAnnouncement("");
    setSending(true);
    setMessages((prev) => [
      ...prev,
      {
        id: pendingId,
        role: "user",
        content: question,
        citations: [],
        attachments: sent,
        created_at: new Date().toISOString(),
      },
    ]);

    try {
      let newConversationId: string | null = null;
      // Neither `token` nor `citations` gets a branch, both deliberately: Slice 1's
      // answer() is a single non-streaming llm_provider.chat() call so `token` is
      // never emitted at all (it is Slice 3's), and the `citations` frame carries
      // the identical array that `done` carries one frame later.
      await streamChat(
        {
          conversation_id: conversationId,
          message: question,
          attachment_ids: sent.map((a) => a.id),
        },
        (event) => {
          if (event.type === "status") {
            setStatus(STATUS_LABEL[event.status] ?? null);
          } else if (event.type === "error") {
            setError(event.detail);
            // Take the question back off screen with it. An `error` frame means
            // the backend rolled the conversation back - a brand new one is
            // deleted, an existing one keeps neither message - so leaving the
            // bubble up shows a question that is not saved anywhere, and the
            // next reload silently loses it. Only this branch does it: a
            // truncated stream or a dropped connection throws instead, and
            // there the backend may well have committed the exchange.
            setMessages((prev) => prev.filter((m) => m.id !== pendingId));
          } else if (event.type === "done") {
            newConversationId = event.conversation_id;
            setAnnouncement(event.content);
            setMessages((prev) => [
              ...prev,
              {
                id: `assistant-${Date.now()}`,
                role: "assistant",
                content: event.content,
                citations: event.citations,
                attachments: [],
                created_at: new Date().toISOString(),
              },
            ]);
          }
        },
        controller.signal,
      );

      if (!conversationId && newConversationId) {
        setConversationId(newConversationId);
        // router.replace, and NOT window.history.replaceState. Both were
        // measured on `next start`, same clicks, only this line differing, with
        // POST /api/chat answered by a stubbed SSE body naming an existing
        // conversation.
        //
        // router.replace costs a full document load here (performance.timeOrigin
        // changes; /api/auth/me and /api/conversations are requested again), so
        // the answer that just rendered is off screen until the new page's
        // transcript fetch lands: rAF frames of the new document at 31, 53 and
        // 63ms hold no messages and the transcript is back at 80ms, ~76ms end to
        // end over loopback. Everything downstream is then correct - Back
        // re-requests /api/conversations/{id}/messages and restores the
        // conversation, Forward returns to the one clicked in the sidebar, and
        // reload matches both.
        //
        // window.history.replaceState removes that reload, and the Sidebar still
        // refetches because usePathname() still changes. It also corrupts the
        // history entry, which is worse. Next patches replaceState to re-run its
        // router restore with the tree it already has, so the entry keeps the
        // /chat (new-chat) tree while its URL becomes /chat/{id}. Measured: the
        // next sidebar click degrades to a full page load, and Back then restores
        // that entry making NO request at all - the transcript it showed was the
        // two messages left in memory where the conversation has four, and
        // nothing ever refetches it. 76ms of flicker is cosmetic; a history entry
        // whose page disagrees with its URL is not.
        router.replace(`/chat/${newConversationId}`);
      }
    } catch (err) {
      // An abort is this component's own doing, not a failure: either the user
      // pressed 중지, or they moved on and the unmount cleanup fired. Rendering
      // it would put a red banner on the conversation they just opened, about
      // the one they just left. Name check rather than `instanceof
      // DOMException` - fetch and the stream reader are free to reject with
      // either, and only the name is guaranteed.
      if ((err as { name?: string } | null)?.name !== "AbortError") {
        setError(errorMessage(err));
      } else if (stoppedRef.current) {
        // 중지 lands before phase 3, so the backend persisted nothing: the
        // client disconnect cancels the generator at a yield, and persist_turn
        // is downstream of that. Leaving the question in the transcript would
        // show a turn that no reload can reproduce - the same reasoning the
        // `error` frame above follows - so it goes back into the composer, where
        // the user can edit it and ask again.
        setMessages((prev) => prev.filter((m) => m.id !== pendingId));
        setInput(question);
        setNotice("답변 생성을 중지했습니다.");
      }
    } finally {
      setStatus(null);
      setSending(false);
    }
  }

  function fill(text: string) {
    setInput(text);
    textareaRef.current?.focus();
  }

  const hasFiles = (e: React.DragEvent) => e.dataTransfer.types.includes("Files");

  // h-full, not h-screen: this fills `main`, and the (app) layout's h-screen
  // wrapper is what bounds it. h-screen here would be 100vh whatever main
  // actually offers a child. Below md that is not the same number: main is a
  // flex item stretched to the wrapper's 100vh with pt-12 on it, so its content
  // box is 100vh - 3rem and a h-screen child overflows by exactly that padding.
  return (
    <div
      className="relative flex h-full flex-col"
      onDragEnter={(e) => {
        if (!hasFiles(e)) return;
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(e) => {
        // Without preventDefault on dragover the browser refuses the drop and
        // navigates to the file instead, which loses the whole conversation.
        if (hasFiles(e)) e.preventDefault();
      }}
      onDragLeave={() => {
        dragDepth.current -= 1;
        if (dragDepth.current <= 0) setDragging(false);
      }}
      onDrop={(e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        addFiles(Array.from(e.dataTransfer.files));
      }}
    >
      {dragging && (
        // pointer-events-none is load-bearing: an overlay that takes the
        // pointer fires dragleave on the container the instant it appears, so
        // the drop state would flicker and the drop itself would land on the
        // overlay instead of on the handler above.
        <div className="pointer-events-none absolute inset-4 z-10 flex items-center justify-center rounded-lg bg-surface outline-dashed outline-2 outline-primary">
          <p className="text-title text-primary">파일을 놓아 첨부하세요</p>
        </div>
      )}
      {/* The scroll container is full-bleed; the 768px reading column (§6) is
          the inner div. Putting max-width on the scroller instead would leave
          the scrollbar floating in the middle of the page. */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-transcript space-y-8 px-4 py-8 sm:px-6">
          {loaded && !error && messages.length === 0 && !sending && (
            // §8 empty state: centred, `display` size, the greeting in the
            // brand gradient, then 3-4 suggestion chips that fill the composer.
            // break-keep (word-break: keep-all) because at 36px in a 343px
            // column the browser's default breaks Korean between syllables -
            // measured at 375px, "무엇이든" split across two lines as 무 / 엇이든.
            // keep-all breaks at spaces instead, which is how the language
            // reads. Only needed at display size; at 14-16px it is invisible.
            <div className="mt-24">
              {/* 36px is the display size for a desktop column. On a 390px
                  phone the same string wraps to two lines and eats the top
                  half of the screen, so it steps down below md. */}
              <p className="break-keep text-center text-headline md:text-display">
                <span className="text-gradient-brand">등록된 문서에 대해 무엇이든 물어보세요.</span>
              </p>
              <div className="mt-10 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((text) => (
                  <button
                    key={text}
                    type="button"
                    onClick={() => fill(text)}
                    className="break-keep rounded-full bg-surface-container px-4 py-2 text-label text-on-surface-variant transition-colors duration-150 hover:bg-surface-container-high"
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} onNotify={setNotice} />
          ))}
          {/* aria-live, because this line is the only feedback between pressing
              전송 and the answer landing, and it is never focused. The sparkle
              is the streaming indicator - the one looping animation in the app
              (§7), and it exists only while `status` does. */}
          <p aria-live="polite" className="flex items-center gap-4 text-body text-on-surface-variant">
            {status && (
              <span aria-hidden="true" className="sparkle sparkle-pulsing h-5 w-5 shrink-0" />
            )}
            {status}
          </p>
        {/* The answer itself, off screen, because a screen reader was told
            문서 검색 중… and 답변 생성 중… and then nothing at all - the status
            line is emptied the moment the answer lands, so the one thing the
            user asked for was never announced.

            A separate region rather than role="log" on the transcript above:
            that container is populated by the transcript fetch AFTER mount, so
            a live region wrapping it re-announces every message in the history
            on arrival at /chat/{id}. This one only ever changes on `done`.

            Measured in headless Edge against a stub origin: asking inside an
            existing conversation leaves this region holding the answer while
            the status line is empty. The very FIRST answer of a brand new
            conversation is the exception - router.replace reloads the document
            ~76ms later (see below) and takes this region with it, the same
            reload the answer bubble itself survives only by being refetched. */}
          <p aria-live="polite" className="sr-only">
            {announcement}
          </p>
          <p aria-live="polite" className="sr-only">
            {notice}
          </p>
          <div ref={bottomRef} />
        </div>
      </div>
      {/* No border-t. The composer is a tonal block sitting on the page, and
          the transcript above it ends where the block begins. */}
      <div className="mx-auto w-full max-w-transcript space-y-3 px-4 pb-6 sm:px-6">
        <ErrorBanner message={error} />
        <Composer
          value={input}
          onChange={setInput}
          onSubmit={() => void handleSend()}
          onFiles={addFiles}
          attachments={attachments}
          onRemove={removeAttachment}
          sending={sending}
          onStop={() => {
            stoppedRef.current = true;
            abortRef.current?.abort();
          }}
          textareaRef={textareaRef}
        />
      </div>
    </div>
  );
}
```


- [ ] **Step 9: Write `frontend/components/chat/MessageBubble.tsx`**

`renderContent` is gone — its job moved into `Markdown.tsx` intact. The 복사 button copies the RAW markdown, and it is always in the DOM rather than revealed on `:hover`, which no keyboard or touch user can do.

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import AttachmentChip from "@/components/chat/AttachmentChip";
import Markdown from "@/components/chat/Markdown";
import type { Message } from "@/lib/types";

export default function MessageBubble({
  message,
  onNotify,
}: {
  message: Message;
  /** ChatWindow's shared live region. Announcing 복사됨 from a region inside
   * this component would put one live region per message on the page. */
  onNotify?: (text: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timerRef.current) clearTimeout(timerRef.current);
  }, []);

  async function copy() {
    try {
      // The RAW markdown, not the rendered text: what the user wants out of an
      // answer is the thing they can paste back into a document with its list
      // and its code fence intact.
      await navigator.clipboard.writeText(message.content);
    } catch {
      // writeText rejects on a non-secure origin and on a denied permission.
      onNotify?.("복사하지 못했습니다.");
      return;
    }
    setCopied(true);
    onNotify?.("복사됨");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 2000);
  }

  // §8, and the single biggest structural difference from a generic chat UI:
  // only the USER's message is bubbled. The assistant's answer renders flat on
  // the page surface at reading size with the gradient sparkle at its head, so
  // it reads as a document rather than as a text message. Two return paths
  // rather than one with ternaries everywhere, because the two are not the
  // same shape any more.
  if (message.role === "user") {
    return (
      <div className="flex flex-col items-end gap-2">
        {message.attachments.length > 0 && (
          // A reloaded transcript has no other record of what was sent with the
          // question, so these render from message.attachments and not only
          // from the live composer state.
          <div className="flex max-w-[75%] flex-wrap justify-end gap-2">
            {message.attachments.map((a) => (
              <AttachmentChip
                key={a.id}
                filename={a.filename}
                sizeBytes={a.size_bytes}
                kind={a.kind}
                // Owner-scoped and served `inline` with `nosniff` for images
                // only; a document id here would download, which is why only an
                // image gets a src.
                src={a.kind === "image" ? `/api/attachments/${a.id}/content` : null}
              />
            ))}
          </div>
        )}
        <div className="max-w-[75%] whitespace-pre-wrap rounded-md bg-surface-container px-4 py-3 text-body-lg text-on-surface">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="group flex gap-4">
      {/* aria-hidden: decorative. The role is already carried by the layout and
          by the off-screen live region in ChatWindow. */}
      <span aria-hidden="true" className="sparkle mt-1 h-5 w-5 shrink-0" />
      {/* min-w-0 so a long unbroken token wraps instead of widening the flex
          row past the transcript column. */}
      <div className="min-w-0 flex-1">
        {/* No whitespace-pre-wrap any more: markdown owns the block structure,
            and pre-wrap would double every blank line between paragraphs. */}
        <Markdown content={message.content} citations={message.citations} />
        {message.citations.length > 0 && (
          <div className="mt-4 border-t border-outline-variant pt-3 text-caption text-on-surface-variant">
            {message.citations.map((c) => (
              // index, not chunk_id: chunk_id is null for an MCP citation, and
              // two of them on one message would collide on a null key. index
              // is unique per message by construction - the backend assigns it
              // with enumerate(used, start=1).
              <div key={c.index} className="truncate">
                [{c.index}] {c.filename ?? "출처"}
                {c.page !== null ? `, ${c.page}쪽` : ""}
                {c.section ? `, ${c.section}` : ""}
              </div>
            ))}
          </div>
        )}
        {/* Always in the DOM, never revealed by hover alone: a control that
            appears only on :hover is unreachable by keyboard and invisible on
            touch. It is quiet at rest and darkens on hover instead. */}
        <div className="mt-2">
          <button
            type="button"
            onClick={() => void copy()}
            aria-label="답변 복사"
            className="inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-caption text-on-surface-variant transition-colors duration-150 hover:bg-surface-container"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="9" y="9" width="11" height="11" rx="2" />
              <path d="M5 15V5a2 2 0 0 1 2-2h10" />
            </svg>
            {copied ? "복사됨" : "복사"}
          </button>
        </div>
      </div>
    </div>
  );
}
```


- [ ] **Step 10: Modify `frontend/app/globals.css`**

Tailwind's preflight strips heading sizes and list markers, so every one of them is restated here. Scoped under `.markdown` so nothing outside an answer inherits it. Code blocks per §8: `surface-container-high`, `--radius-sm`, monospace, and no syntax highlighting — deliberately out of scope, so no colour appears inside a code block that is not a token.

```css
/* The assistant answer's markdown, rendered by components/chat/Markdown.tsx.
   These are static class lists per element, which is what this @layer is for -
   as ~20 `components` overrides in the TSX they would be the same strings with
   a JSX function around each one. Tailwind's preflight strips heading sizes and
   list markers, so every one of them has to be restated here.
   Scoped under .markdown so nothing outside an answer inherits it. */
@layer components {
  .markdown {
    @apply break-words;
  }
  .markdown > :first-child {
    @apply mt-0;
  }
  .markdown > :last-child {
    @apply mb-0;
  }
  .markdown p,
  .markdown ul,
  .markdown ol,
  .markdown blockquote,
  .markdown pre {
    @apply my-3;
  }
  .markdown h1 {
    @apply mb-2 mt-6 text-headline font-medium;
  }
  .markdown h2 {
    @apply mb-2 mt-6 text-title font-medium;
  }
  .markdown h3,
  .markdown h4,
  .markdown h5,
  .markdown h6 {
    @apply mb-1 mt-4 text-body-lg font-medium;
  }
  .markdown ul {
    @apply list-disc pl-6;
  }
  .markdown ol {
    @apply list-decimal pl-6;
  }
  .markdown li {
    @apply my-1;
  }
  .markdown li > ul,
  .markdown li > ol {
    @apply my-1;
  }
  .markdown a {
    @apply text-primary underline;
  }
  .markdown blockquote {
    @apply border-l-2 border-outline-variant pl-4 text-on-surface-variant;
  }
  .markdown hr {
    @apply my-6 border-t border-outline-variant;
  }
  /* §8: code blocks on surface-container-high at --radius-sm, monospace. No
     syntax highlighting - deliberately out of scope, so no colour appears
     inside a code block that is not a token. */
  .markdown code {
    @apply rounded-xs bg-surface-container-high px-1.5 py-0.5 text-body;
  }
  .markdown pre {
    @apply overflow-x-auto rounded-sm bg-surface-container-high p-4;
  }
  .markdown pre code {
    @apply rounded-none bg-transparent p-0;
  }
  /* remark-gfm tables. The scrolling wrapper is in Markdown.tsx - a max-width
     on the container is what `overflow-x-auto` needs to have something to
     overflow, and the transcript column supplies it. */
  .markdown table {
    @apply w-full text-left text-body;
  }
  .markdown th {
    @apply border-b border-outline-variant bg-surface-container-low px-3 py-2 text-label font-medium text-on-surface-variant;
  }
  .markdown td {
    @apply border-b border-outline-variant px-3 py-2 align-top;
  }
}
```


- [ ] **Step 11: Modify `frontend/components/documents/DocumentTable.tsx`**

One formatter, so 1.5 MB is never 1536.0 KB two screens over.

```tsx
// Exported for the chat composer's attachment chips, which show the same fact in
// the same units. One formatter, so 1.5 MB is never 1536.0 KB two screens over.
export function formatSize(bytes: number): string {
```


- [ ] **Step 12: Modify `frontend/components/layout/Sidebar.tsx`**

Rename inline in the row, delete through the existing `ConfirmDialog`. The dialog is rendered OUTSIDE `content`, which is rendered twice — docked and in the drawer — so one `showModal()` inside it would open two dialogs.

```tsx
  async function commitRename(id: string) {
    const title = renameValue.trim();
    setRenamingId(null);
    // Nothing to save, and the server would answer 422 for a blank title. The
    // row keeps the name it had.
    if (!title) return;
    try {
      await apiFetch(`/api/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
    } catch (err) {
      setError(errorMessage(err));
      return;
    }
    // Reload rather than patching the array in place: PATCH bumps updated_at,
    // and this list is ordered by it, so the renamed row moves.
    await load();
  }

  async function confirmDelete(conversation: Conversation) {
    await apiFetch(`/api/conversations/${conversation.id}`, { method: "DELETE" });
    // Off the conversation that no longer exists, before the list reloads:
    // staying put would leave /chat/{id} rendering a 404 banner over an empty
    // transcript. push, not replace - Back should return to where they were.
    if (pathname === `/chat/${conversation.id}`) router.push("/chat");
    await load();
  }
```


```tsx
      {deleteTarget && (
        <ConfirmDialog
          title="대화 삭제"
          message={`"${deleteTarget.title}" 대화와 그 안의 모든 메시지가 삭제됩니다. 되돌릴 수 없습니다.`}
          confirmLabel="삭제"
          onConfirm={() => confirmDelete(deleteTarget)}
          onClose={() => setDeleteTarget(null)}
        />
      )}
```


- [ ] **Step 13: Modify `frontend/package.json`**

Two dependencies, no more. `rehype-raw` is deliberately absent and must stay absent.

```json
    "react-markdown": "9.1.0",
    "remark-gfm": "4.0.1"
```


- [ ] **Step 14: Modify `frontend/components/chat/CitationBadge.tsx`**

The badge now renders INSIDE a markdown paragraph, and `<dialog>` - with its
`<div>`, `<h2>` and `<p>` - is not valid inside a `<p>`. React reported all three
as nesting errors in the browser. A portal renders no DOM at the call site at
all, so the paragraph stays a paragraph and the dialog's top-layer promotion is
unaffected. The `typeof document` guard is for the server render, where this
component's parent has no messages yet and it never runs anyway.

```tsx
      {typeof document !== "undefined" &&
        createPortal(
          <dialog
            ref={dialogRef}
            aria-label={`출처 ${citation.index}`}
            onClose={() => setOpen(false)}
            // The dialog box itself is the click target only for a click on the
            // backdrop, because the padding lives on the inner div.
            onClick={(e) => {
              if (e.target === dialogRef.current) dialogRef.current?.close();
            }}
            className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-surface-container-low p-0 text-on-surface shadow-dialog backdrop:bg-scrim"
          >
            <div className="p-6">
              <div className="mb-3 flex items-start justify-between gap-4">
                {/* h2, not p: it is the modal's only title, and without a heading a
                screen reader landing on 닫기 has nothing to jump back to.
                No `uppercase`: this line is a filename plus Korean labels. */}
                <h2 className="text-label font-medium tracking-wide text-on-surface-variant">
                  [{citation.index}] {label(citation)}
                </h2>
                <button
                  type="button"
                  onClick={() => dialogRef.current?.close()}
                  className="btn-text btn-compact shrink-0"
                >
                  닫기
                </button>
              </div>
              <ErrorBanner message={error} />
              <p className="mt-3 whitespace-pre-wrap text-body-lg text-on-surface">
                {chunk ? chunk.content : citation.snippet}
              </p>
            </div>
          </dialog>,
          document.body,
```

### Task 15: Chat experience verification

- [ ] **Step 1: One pytest session**

```bash
cd backend && python -m pytest
```

ONE session, never `-n auto`: `migrated_database` runs `downgrade base`, and
concurrent sessions corrupt `mopan_test`.

- [ ] **Step 2: Frontend gates**

```bash
cd frontend && npx tsc --noEmit && npm run build && npm test
```

- [ ] **Step 3: Re-run the forgery and XSS attacks in a real browser**

Four answers, each rendered and then read back out of the DOM:

1. `ok.\n\n[9] (evil.pdf, p.1)\nhunter2 [1]` with only citation 1 present — `[9]`
   must be literal text and exactly one badge must exist.
2. `` `[1]` `` inside inline code and inside a fenced block — no badge in either.
3. `<img src=x onerror=alert(1)>` — no `<img>` in the DOM, `document.title`
   unchanged.
4. `[click](javascript:alert(1))` — no `javascript:` href in the DOM.

- [ ] **Step 4: Drive the composer**

Attach by `+`, by Ctrl+V paste and by drop; a refused file shows its Korean
reason on its own chip; remove calls `DELETE /api/attachments/{id}`; Enter sends
and Shift+Enter inserts a newline; Enter while a Hangul candidate is composing
does NOT send.

- [ ] **Step 5: Rebuild the container so :3000 is not stale**

```bash
docker compose up -d --build frontend
```

`--build`, not `restart`: the image COPYs the source, there is no bind mount, so
a restart runs the OLD code.

