"""Service settings via pydantic-settings. Profile chosen by ``KHARCHA_ENV`` (local|test|prod).

Values come from environment variables prefixed ``KHARCHA_`` or a ``.env`` file.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
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
    llm_timeout_s: float = 60.0

    # Parser (§10.4-10.5): share of rule-parsed events also sent to the teacher, and synthesis.
    parser_shadow_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    rule_synthesis_enabled: bool = True

    # Phase 1 auth: static per-user API keys, stored as {sha256(key) hex: user_id}.
    # Generate with `uv run kharcha-admin create-user`. Replaced by Firebase in W9.
    api_keys: dict[str, str] = {}
    # W9: Firebase project id; when set, Bearer JWTs are verified as Firebase ID tokens.
    firebase_project_id: str | None = None

    metrics_port: int = 8001

    # Telegram (§25). Local: long polling. Token from BotFather, never committed.
    telegram_bot_token: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    daily_summary_time: str = "21:30"  # IST, before default quiet hours
    coach_llm_enabled: bool = True

    @model_validator(mode="after")
    def _prod_needs_explicit_config(self) -> Self:
        if self.env == "prod" and self.database_url == _LOCAL_DATABASE_URL:
            raise ValueError("KHARCHA_DATABASE_URL must be set explicitly in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
