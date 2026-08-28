import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db_session, make_engine, make_sessionmaker
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.redis import get_redis, make_redis

logger = logging.getLogger("mopan.app")

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
    try:
        yield
    finally:
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

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/health/ready")
    async def ready(
        db: AsyncSession = Depends(get_db_session),
        redis: Redis = Depends(get_redis),
    ) -> dict[str, str]:
        try:
            await db.execute(text("SELECT 1"))
            await redis.ping()
            deployed_dim = await db.scalar(text(EMBEDDING_DIM_SQL))
        except Exception as exc:
            logger.exception("readiness check failed")
            raise HTTPException(status_code=503, detail="dependencies unavailable") from exc

        configured = get_settings().embedding_dim
        if deployed_dim is not None and deployed_dim != configured:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"EMBEDDING_DIM={configured} does not match the deployed "
                    f"chunks.embedding width ({deployed_dim}). Run a migration and re-index."
                ),
            )
        return {"status": "ready"}

    return app


app = create_app()
