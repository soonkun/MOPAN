from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from app.core.config import EMBEDDING_INPUT_TOKEN_LIMIT, EMBEDDING_MAX_BATCH_SIZE, REPO_ROOT, Settings


def test_env_file_is_anchored_to_the_repo_root():
    # The previous implementation used a bare ".env", resolved against the process
    # CWD. Every documented command runs from backend/, where no .env exists, so it
    # silently loaded nothing and booted on defaults with an empty API key.
    assert Settings.model_config["env_file"] == (
        REPO_ROOT / ".env",
        REPO_ROOT / "backend" / ".env",
    )


def test_values_are_read_from_the_env_file(tmp_path, monkeypatch):
    # Guards the same defect from the other side: the asserted value is neither a
    # code default nor an environment variable, so it can only come from the file.
    monkeypatch.delenv("ANSWER_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ANSWER_MODEL=model-from-file\n", encoding="utf-8")

    class FileSettings(Settings):
        model_config = SettingsConfigDict(env_file=env_file, env_file_encoding="utf-8", extra="ignore")

    assert FileSettings().answer_model == "model-from-file"


def test_defaults_cover_binding_requirements():
    settings = Settings()
    assert settings.rrf_k == 60
    assert settings.embedding_dim == 1536
    assert settings.chunking_strategy == "semantic"
    assert settings.max_upload_size_mb == 50


def test_environment_variable_overrides_file(monkeypatch):
    monkeypatch.setenv("ANSWER_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    settings = Settings()
    assert settings.answer_model == "gpt-4o-mini"
    assert settings.openai_api_key == "sk-from-env"


def test_relative_upload_dir_is_absolutised_against_repo_root():
    settings = Settings(upload_dir=Path("./data/uploads"))
    assert settings.upload_dir.is_absolute()
    assert settings.upload_dir == (REPO_ROOT / "data/uploads").resolve()


def test_absolute_upload_dir_is_left_alone(tmp_path):
    assert Settings(upload_dir=tmp_path).upload_dir == tmp_path


def test_production_requires_api_key():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings(environment="production", openai_api_key="")


def test_production_rejects_default_database_password():
    with pytest.raises(ValueError, match="default database password"):
        Settings(
            environment="production",
            openai_api_key="sk-test",
            database_url="postgresql+asyncpg://mopan:mopan@db:5432/mopan",
        )


def test_self_registration_defaults_off_in_production():
    """What the environment IMPLIES when the operator has set nothing.

    allow_self_registration=None is passed explicitly on both sides, because
    pydantic-settings fills an unspecified field from the real .env: with
    ALLOW_SELF_REGISTRATION=false in that file the development case read false
    and failed, while the production case still passed - for the wrong reason,
    since it was reading the operator's value rather than the derivation this
    test is named after."""
    prod = Settings(
        environment="production",
        allow_self_registration=None,
        openai_api_key="sk-test",
        database_url="postgresql+asyncpg://mopan:s3cret@db:5432/mopan",
    )
    assert prod.allow_self_registration is False
    dev = Settings(environment="development", allow_self_registration=None)
    assert dev.allow_self_registration is True

    # And an explicit value still wins over the derivation, in both directions.
    assert Settings(environment="development", allow_self_registration=False).allow_self_registration is False
    assert (
        Settings(
            environment="production",
            allow_self_registration=True,
            openai_api_key="sk-test",
            database_url="postgresql+asyncpg://mopan:s3cret@db:5432/mopan",
        ).allow_self_registration
        is True
    )


def test_invalid_chunk_overlap_is_rejected():
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        Settings(chunk_size=100, chunk_overlap=100)


@pytest.mark.parametrize("value", [0, EMBEDDING_INPUT_TOKEN_LIMIT])
def test_out_of_range_max_chunk_tokens_is_rejected(value):
    # 0 reaches split_to_token_limit as a crash; a value near the embedding
    # ceiling leaves no headroom for the newline accounting's rare 2-token join.
    with pytest.raises(ValueError, match="MAX_CHUNK_TOKENS"):
        Settings(max_chunk_tokens=value)


@pytest.mark.parametrize("value", [0, -5, EMBEDDING_MAX_BATCH_SIZE + 1])
def test_out_of_range_embedding_batch_size_is_rejected(value):
    # 0 or negative degrades to one embedding request per chunk with no error -
    # pure cost and latency; above 2048 the endpoint rejects the array
    # mid-document, after the parse and chunk work is already paid for.
    with pytest.raises(ValueError, match="EMBEDDING_BATCH_SIZE"):
        Settings(embedding_batch_size=value)


@pytest.mark.parametrize("value", [0, -1])
def test_out_of_range_embedding_batch_chars_is_rejected(value):
    with pytest.raises(ValueError, match="EMBEDDING_BATCH_CHARS"):
        Settings(embedding_batch_chars=value)


@pytest.mark.parametrize("value", [-1, -60])
def test_negative_rrf_k_is_rejected(value):
    # reciprocal_rank_fusion raises on k < 0. Without this guard the typo boots
    # fine and surfaces as a 500 on the first query that reaches fusion.
    with pytest.raises(ValueError, match="RRF_K"):
        Settings(rrf_k=value)


@pytest.mark.parametrize(
    "field",
    ["retrieval_top_n", "retrieval_candidate_limit", "answer_context_token_budget"],
)
@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_retrieval_limits_are_rejected(field, value):
    """No knob here raises at query time, each just returns less: top_n=-1 boots
    cleanly and silently drops the last evidence item off every answer, and a
    non-positive context budget degrades into one below-the-floor log per request
    forever."""
    with pytest.raises(ValueError, match=field.upper()):
        Settings(**{field: value})


def test_rrf_k_zero_is_accepted():
    # k=0 is pure reciprocal rank, the most top-heavy legal setting.
    assert Settings(rrf_k=0).rrf_k == 0


@pytest.mark.parametrize("value", [1.5, -1.01])
def test_out_of_range_similarity_threshold_is_rejected(value):
    # Cosine similarity is bounded to [-1, 1]. Outside it the semantic strategy
    # silently degrades to "always merge" (below -1) or "never merge" (above 1),
    # which looks like working chunking right up to the retrieval quality report.
    with pytest.raises(ValueError, match="SEMANTIC_SIMILARITY_THRESHOLD"):
        Settings(semantic_similarity_threshold=value)


def test_invalid_environment_value_is_rejected(monkeypatch):
    # ENVIRONMENT=Production must not silently disable every "production" check
    # (admin bootstrap gate, cookie secure flag, API-key and DB-password refusals).
    monkeypatch.setenv("ENVIRONMENT", "Production")
    # match=: without it this passes on a ValidationError from any unrelated
    # field, so it would not notice the Literal being loosened back to str.
    with pytest.raises(ValidationError, match="environment"):
        Settings()


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o", True),
        ("gpt-4o-mini", True),
        ("gpt-4.1", True),
        # Conservative on purpose: the o-series -mini members are text-only, so the
        # whole family is left to the explicit override rather than guessed at. A
        # false negative costs one env var; a false positive is the opaque provider
        # 400 this setting exists to prevent.
        ("o1-mini", False),
        ("llama-3-8b-instruct", False),
    ],
)
def test_vision_support_is_derived_from_the_answer_model(model, expected):
    assert Settings(answer_model=model).answer_model_supports_vision is expected


def test_an_explicit_vision_setting_overrides_the_derivation():
    """The escape hatch for a vision-capable model the allowlist has not heard of,
    and for pinning a listed model off."""
    assert Settings(
        answer_model="my-local-vlm", answer_model_supports_vision=True
    ).answer_model_supports_vision
    assert (
        Settings(answer_model="gpt-4o", answer_model_supports_vision=False).answer_model_supports_vision
        is False
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("max_attachment_size_mb", 0), ("max_attachments_per_message", 0)],
)
def test_non_positive_attachment_limits_are_rejected(field, value):
    # Neither errors when it goes non-positive, it just makes every attachment
    # upload impossible with a message that blames the user's file.
    with pytest.raises(ValueError, match=field.upper()):
        Settings(**{field: value})
