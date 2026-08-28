"""Seed an admin user (and a default collection) without going through the API.

Usage (from the repo root):
    python scripts/create_admin.py admin@example.com

Password comes from MOPAN_ADMIN_PASSWORD or an interactive prompt. There is no
default password: an unattended run with neither set exits non-zero rather than
creating a guessable production account.
Pure Python: no shell, no OS-specific paths, identical on Windows and Linux.
"""
import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import make_engine  # noqa: E402
from app.core.security import MAX_PASSWORD_BYTES, MIN_PASSWORD_LENGTH, hash_password  # noqa: E402
from app.models.collection import Collection  # noqa: E402
from app.models.user import User  # noqa: E402

DEFAULT_COLLECTION_NAME = "일반"


async def main(email: str, password: str) -> int:
    engine = make_engine(get_settings())
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as db:
            email = email.strip().lower()
            # Idempotent: re-running never overwrites or duplicates an account.
            if await db.scalar(select(User).where(User.email == email)):
                print(f"user {email} already exists")
                return 1
            user = User(email=email, password_hash=hash_password(password), role="admin")
            db.add(user)
            await db.flush()
            if not await db.scalar(select(func.count()).select_from(Collection)):
                db.add(Collection(name=DEFAULT_COLLECTION_NAME, created_by=user.id))
            await db.commit()
            print(f"created admin {email}")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/create_admin.py <email>")
        raise SystemExit(2)
    pw = os.getenv("MOPAN_ADMIN_PASSWORD") or getpass.getpass("password: ")
    if len(pw) < MIN_PASSWORD_LENGTH or len(pw.encode("utf-8")) > MAX_PASSWORD_BYTES:
        print(
            f"password must be {MIN_PASSWORD_LENGTH}+ characters "
            f"and at most {MAX_PASSWORD_BYTES} bytes"
        )
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1], pw)))
