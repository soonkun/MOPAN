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

# There is no capability query on the chat endpoint - a model that cannot see
# images answers an image part with an opaque 400 - so vision support has to be
# asserted, not discovered. Deliberately a short, conservative PREFIX allowlist:
# a false negative refuses an image upload with a Korean message naming the model,
# which an operator fixes with one env var (ANSWER_MODEL_SUPPORTS_VISION=true),
# while a false positive is the raw provider error this exists to prevent. Note
# what is NOT here: the o1/o3/o4 reasoning families, whose -mini members are
# text-only, so the whole family is left to the override.
VISION_CAPABLE_MODEL_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-4-vision", "gpt-5", "chatgpt-4o")


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

    # 127.0.0.1, not localhost: on Windows localhost resolves to ::1 first and
    # every connect pays a failed IPv6 attempt first (2076ms vs 31ms). See the
    # note in .env.example.
    database_url: str = "postgresql+asyncpg://mopan:mopan@127.0.0.1:5432/mopan"
    redis_url: str = "redis://127.0.0.1:6379/0"
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
        if self.answer_model_supports_vision is None:
            model = self.answer_model.lower()
            self.answer_model_supports_vision = model.startswith(VISION_CAPABLE_MODEL_PREFIXES)
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
        # Neither knob errors when it goes non-positive, it just quietly returns
        # less: RETRIEVAL_TOP_N=-1 drops the last evidence item off every answer,
        # and CANDIDATE_LIMIT=0 empties the candidate set before the reranker is
        # ever asked to score it. Boot failure beats a silently smaller corpus.
        if self.retrieval_top_n < 1:
            raise ValueError("RETRIEVAL_TOP_N must be >= 1")
        if self.retrieval_candidate_limit < 1:
            raise ValueError("RETRIEVAL_CANDIDATE_LIMIT must be >= 1")
        # Same shape: a negative budget boots fine and then degrades into one
        # below-the-floor log per request forever, never an error.
        if self.answer_context_token_budget < 1:
            raise ValueError("ANSWER_CONTEXT_TOKEN_BUDGET must be >= 1")
        # Same shape as the retrieval knobs: neither errors when it goes
        # non-positive, it just makes every attachment upload or every attached
        # message impossible with a message that blames the user's file.
        if self.max_attachment_size_mb < 1:
            raise ValueError("MAX_ATTACHMENT_SIZE_MB must be >= 1")
        if self.max_attachments_per_message < 1:
            raise ValueError("MAX_ATTACHMENTS_PER_MESSAGE must be >= 1")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_settings(request: Request) -> Settings:
    """Request-path dependency. get_settings() is lru_cached, so a route that
    depends on it ignores the live Settings the lifespan put on app.state (and
    the one tests swap in there). Same rule as get_db_session/get_redis."""
    return request.app.state.settings
