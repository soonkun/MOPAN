from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Request
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] == repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_DB_PASSWORDS = ("mopan", "postgres", "password")

# Per-input token ceiling for OpenAI's text-embedding-3-* models.
EMBEDDING_INPUT_TOKEN_LIMIT = 8191
# Element ceiling for one embeddings request's input array.
EMBEDDING_MAX_BATCH_SIZE = 2048


class Settings(BaseSettings):
    # env_file is anchored to the repo root. Resolving it against the process CWD
    # means every documented command (run from backend/) silently loads zero
    # settings and boots on defaults.
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Literal, not str: a typo like ENVIRONMENT=Production would otherwise
    # silently disable every production safeguard that compares against
    # "production". Fail at startup instead.
    environment: Literal["development", "production"] = "development"

    database_url: str = "postgresql+asyncpg://mopan:mopan@localhost:5432/mopan"
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 10

    cors_origins: list[str] = ["http://localhost:3000"]

    session_ttl_seconds: int = 86400
    allow_self_registration: bool | None = None  # None -> enabled outside production

    openai_api_key: str = ""
    answer_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embedding_batch_size: int = 128
    embedding_batch_chars: int = 200_000
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3

    rrf_k: int = 60
    retrieval_top_n: int = 6
    retrieval_candidate_limit: int = 20

    chunking_strategy: str = "semantic"
    chunk_size: int = 800
    chunk_overlap: int = 100
    max_chunk_tokens: int = 500
    semantic_similarity_threshold: float = 0.75
    answer_context_token_budget: int = 6000

    upload_dir: Path = Path("./data/uploads")
    max_upload_size_mb: int = 50

    @field_validator("upload_dir")
    @classmethod
    def _absolutize_upload_dir(cls, value: Path) -> Path:
        # A relative UPLOAD_DIR resolves differently for the API (run from backend/)
        # and the worker. Anchor it so both processes agree.
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @model_validator(mode="after")
    def _finalise(self) -> "Settings":
        if self.allow_self_registration is None:
            self.allow_self_registration = self.environment != "production"
        if self.environment == "production":
            if not self.openai_api_key:
                raise ValueError("OPENAI_API_KEY must be set when ENVIRONMENT=production")
            if any(f":{pw}@" in self.database_url for pw in DEFAULT_DB_PASSWORDS):
                raise ValueError("refusing to start in production with a default database password")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must satisfy 0 <= overlap < CHUNK_SIZE")
        # The size pass treats a joining newline as one token; a rare punctuation
        # tail makes it two, so a candidate can run a few percent over. Capping at
        # half the embedding ceiling keeps that overrun harmless instead of
        # turning it into a rejected embedding call.
        if not 1 <= self.max_chunk_tokens <= EMBEDDING_INPUT_TOKEN_LIMIT // 2:
            raise ValueError(
                f"MAX_CHUNK_TOKENS must satisfy 1 <= value <= {EMBEDDING_INPUT_TOKEN_LIMIT // 2}"
            )
        # Cosine similarity is bounded to [-1, 1]. A value outside it silently
        # turns the semantic strategy into "always merge" or "never merge".
        if not -1.0 <= self.semantic_similarity_threshold <= 1.0:
            raise ValueError("SEMANTIC_SIMILARITY_THRESHOLD must satisfy -1.0 <= value <= 1.0")
        # Zero or negative degrades to one embedding request per chunk with no
        # error - just cost and latency; above 2048 the endpoint rejects the
        # array mid-document.
        if not 1 <= self.embedding_batch_size <= EMBEDDING_MAX_BATCH_SIZE:
            raise ValueError(f"EMBEDDING_BATCH_SIZE must satisfy 1 <= value <= {EMBEDDING_MAX_BATCH_SIZE}")
        if self.embedding_batch_chars < 1:
            raise ValueError("EMBEDDING_BATCH_CHARS must be at least 1")
        # reciprocal_rank_fusion rejects k < 0 (ZeroDivisionError at rank -k, and
        # negative scores that invert the ranking before it gets there). Checking
        # it here turns an operator's typo into a boot failure instead of a 500 on
        # the first query that reaches fusion.
        if self.rrf_k < 0:
            raise ValueError("RRF_K must be >= 0")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_settings(request: Request) -> Settings:
    """Request-path dependency. get_settings() is lru_cached, so a route that
    depends on it ignores the live Settings the lifespan put on app.state (and
    the one tests swap in there). Same rule as get_db_session/get_redis."""
    return request.app.state.settings
