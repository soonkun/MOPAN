from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"

    database_url: str = "postgresql+asyncpg://mopan:mopan@localhost:5432/mopan"
    redis_url: str = "redis://localhost:6379/0"

    session_ttl_seconds: int = 86400

    openai_api_key: str = ""
    answer_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"

    rrf_k: int = 60

    upload_dir: str = "./data/uploads"
    max_upload_size_mb: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()
