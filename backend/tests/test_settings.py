from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from app.core.config import REPO_ROOT, Settings


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
    prod = Settings(
        environment="production",
        openai_api_key="sk-test",
        database_url="postgresql+asyncpg://mopan:s3cret@db:5432/mopan",
    )
    assert prod.allow_self_registration is False
    assert Settings(environment="development").allow_self_registration is True


def test_invalid_chunk_overlap_is_rejected():
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        Settings(chunk_size=100, chunk_overlap=100)


def test_invalid_environment_value_is_rejected(monkeypatch):
    # ENVIRONMENT=Production must not silently disable every "production" check
    # (admin bootstrap gate, cookie secure flag, API-key and DB-password refusals).
    monkeypatch.setenv("ENVIRONMENT", "Production")
    with pytest.raises(ValidationError):
        Settings()
