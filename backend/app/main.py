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
                detail=(
                    f"EMBEDDING_DIM={configured} does not match the deployed "
                    f"chunks.embedding width ({deployed_dim}). Run a migration and re-index."
                ),
            )
        return {"status": "ready"}

    from app.auth.router import router as auth_router
    from app.chat.router import router as chat_router
    from app.documents.router import router as documents_router

    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(documents_router)

    return app


app = create_app()
