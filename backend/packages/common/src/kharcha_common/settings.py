"""Service settings via pydantic-settings. Profile chosen by ``KHARCHA_ENV`` (local|test|prod).

Values come from environment variables prefixed ``KHARCHA_`` or a ``.env`` file.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["local", "test", "prod"]

_LOCAL_DATABASE_URL = "postgresql+asyncpg://kharcha:kharcha@localhost:5432/kharcha"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KHARCHA_", env_file=".env", extra="ignore")

    env: Env = "local"
    log_level: str = "INFO"

    database_url: str = _LOCAL_DATABASE_URL
    kafka_bootstrap_servers: str = "localhost:9092"
    redis_url: str = "redis://localhost:6379/0"
    temporal_address: str = "localhost:7233"

    # LiteLLM model string; local default is a small Ollama model.
    llm_model: str = "ollama/qwen2.5:1.5b"
    ollama_api_base: str = "http://localhost:11434"

    @model_validator(mode="after")
    def _prod_needs_explicit_config(self) -> Self:
        if self.env == "prod" and self.database_url == _LOCAL_DATABASE_URL:
            raise ValueError("KHARCHA_DATABASE_URL must be set explicitly in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
