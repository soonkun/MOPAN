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
    # The admin-controlled allowlist a user picks an answer model from. It is a
    # COST boundary as much as a correctness one - the operator pays per call and
    # gpt-4o is many times the price of gpt-4o-mini - so an arbitrary model string
    # from a client must never reach the provider. Read through
    # `selectable_models`, which always includes ANSWER_MODEL; leave this empty
    # and the picker offers exactly the default, which is the pre-existing
    # behaviour.
    answer_models: list[str] = []
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embedding_batch_size: int = 128
    embedding_batch_chars: int = 200_000
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3

    rrf_k: int = 60
    retrieval_top_n: int = 6
    retrieval_candidate_limit: int = 20
    # The sparse ranking's weight in RRF. Textbook RRF is 1.0 - every retriever a
    # peer - and that is the value this was measured against, on the real 854-page
    # Korean examination manual with the 20-question set in
    # scripts/eval_questions_ko.json:
    #
    #   dense only                    recall@6 0.950   relevant slots/6  2.25
    #   dense + sparse, weight 1.0    recall@6 0.900   relevant slots/6  2.10
    #   dense + sparse, weight 0.5    recall@6 0.950   relevant slots/6  2.30
    #
    # At 1.0 the sparse half is a net NEGATIVE: it loses a question the dense half
    # answers and spends 2.4 of the 6 evidence slots on chunks that are neither
    # relevant nor in the dense top 6. The arithmetic is structural, not bad luck.
    # At k=60 a sparse rank 1 scores 1/61 and a dense rank 6 scores 1/66, so ANY
    # sparse rank 1 is guaranteed a slot in the top 6 however irrelevant it is -
    # and on Korean it frequently is, because 'simple' is a whitespace tokenizer
    # and Korean is agglutinative (see keyword_search.py).
    #
    # Below ~0.92 that guarantee is gone: 0.5/61 is under the dense list's own
    # rank-20 score of 1/80, so the sparse half can promote a chunk the dense half
    # already found but can no longer seat one on its own. That is a deliberate
    # demotion from peer retriever to ranking signal, and it is why 0.5 and 0.7
    # measure identically - anything under the threshold behaves the same.
    #
    # THAT ENTIRE ANALYSIS WAS FITTED TO A BUG, and the default is back to 1.0.
    # It was measured against the corpus as pypdf had extracted it, where the
    # stored text was scrambled - digits and item markers carried out of the words
    # they belonged to. Keyword matching was therefore being done against garbage,
    # which is most of why the sparse half looked like a net negative. Re-measured
    # on the SAME 20 questions after the pdfplumber parser landed and the corpus
    # was re-ingested, the finding inverted: weight 1.0 gives recall@6 1.000 and
    # weight 0.5 gives 0.950, with dense alone at 0.950. The sparse half now earns
    # its peer status.
    #
    # The threshold arithmetic above is still true and still the reason a weight
    # below ~0.92 behaves as one setting rather than a curve. Keep it: it is what
    # to reach for if sparse ever regresses again.
    #
    # Still open, and now worth more than it was: BM25 over character bigrams
    # measured 0.400 precision at weight 1.0 against 0.358 for the shipped
    # to_tsquery, on equal recall. That is 5 slots in 120 on a 20-question set -
    # suggestive, not decisive. Grow the eval set before paying for the migration.
    # Reproduce with `python scripts/eval_retrieval.py --weights 1.0,0.5,0.0`.
    sparse_weight: float = 1.0

    chunking_strategy: str = "semantic"
    # Characters, for both strategies. Measured on the 1950 stored chunks of the
    # real Korean examination manual: 0.911 cl100k tokens per character (mean
    # 0.860, max 1.213 over a 400-chunk sample), so 1000 characters is ~903 tokens.
    # See .env.example for why each of the four numbers below is what it is.
    chunk_size: int = 1000
    chunk_overlap: int = 150
    # The GUARANTEE, where chunk_size is the target: 1000 chars x the 1.213
    # tokens/char worst case = 1213, rounded up for the separator residual.
    max_chunk_tokens: int = 1300
    semantic_similarity_threshold: float = 0.75
    # RETRIEVAL_TOP_N (6) x MAX_CHUNK_TOKENS (1300) = 7800, so the budget never
    # truncates a full evidence set.
    answer_context_token_budget: int = 8000

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

    # --- MCP -----------------------------------------------------------------
    # Discovery and tool calls fetch a URL an ADMIN typed, which makes the
    # backend an SSRF proxy for everything on the internal network unless
    # something says otherwise - starting with 169.254.169.254, which hands out
    # cloud instance credentials to whoever asks. Default false; the flag exists
    # because local development registers a server on 127.0.0.1 and there is no
    # honest way around that. app/mcp/client.py:check_url is the enforcement.
    mcp_allow_private_networks: bool = False
    # Shorter than LLM_TIMEOUT_SECONDS on purpose: an MCP call sits in front of
    # the model call rather than replacing it, so its budget is additive to a
    # question the user is already waiting on.
    mcp_timeout_seconds: float = 15.0
    # A manual turn names its own tools, so this is a bound on one request, not
    # on a planner. Slice 3's orchestrator gets its own ceiling.
    max_tool_calls_per_message: int = 3

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
        # reciprocal_rank_fusion rejects a negative weight for the same reason it
        # rejects a negative k: a ranking that subtracts is not a ranking, and the
        # 500 would land on the first chat request rather than at boot. 0 is legal
        # and means "dense only" - a documented way to switch the sparse half off
        # without deleting it.
        if self.sparse_weight < 0:
            raise ValueError("SPARSE_WEIGHT must be >= 0")
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
        # Same shape again: zero or negative does not error, it just makes every
        # manual tool call impossible with a message that blames the user's
        # request, and a non-positive timeout makes httpx raise on connect.
        if self.max_tool_calls_per_message < 1:
            raise ValueError("MAX_TOOL_CALLS_PER_MESSAGE must be >= 1")
        if self.mcp_timeout_seconds <= 0:
            raise ValueError("MCP_TIMEOUT_SECONDS must be > 0")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


async def get_app_settings(request: Request) -> Settings:
    """Request-path dependency. get_settings() is lru_cached, so a route that
    depends on it ignores the live Settings the lifespan put on app.state (and
    the one tests swap in there). Same rule as get_db_session/get_redis.

    On top of that it now applies the `app_settings` overrides, so a value an
    admin changes reaches the NEXT request with no restart. This is the single
    indirection every route already goes through, which is what makes "every
    setting keeps its .env value as the fallback" true without asking each
    caller to remember - an empty table returns exactly `app.state.settings`.

    The session comes from `app.core.db.current_sessionmaker`, set per request by
    RequestContextMiddleware, for the same reason `get_prompt` reads it from
    there: this must not become another parameter on `Settings`, and a
    `Depends(get_db_session)` here would put a second session on every request
    that already has one. Imported inside the function because `app.core.db`
    imports this module.
    """
    base: Settings = request.app.state.settings
    from app.core.db import current_sessionmaker
    from app.core.settings_store import effective_settings

    sessionmaker = current_sessionmaker.get()
    if sessionmaker is None:
        return base
    async with sessionmaker() as session:
        return await effective_settings(session, base)
