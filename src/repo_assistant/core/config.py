from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RA_", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    postgres_dsn: str = (
        "postgresql+asyncpg://repo_assistant:repo_assistant@localhost:5432/repo_assistant"
    )
    redis_dsn: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"

    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None
    github_token: str | None = None

    generation_model: str = "claude-opus-4-8"
    router_model: str = "claude-haiku-4-5"
    enrichment_model: str = "claude-haiku-4-5"
    embedding_model: str = "voyage-code-3"
    embedding_dimensions: int = 1024
    reranker_model: str = "rerank-2.5"

    agent_tool_call_budget: int = 8

    # Conversation memory (docs/adr/0015)
    # Keep this many recent turns verbatim; older turns roll into the session summary.
    history_window_messages: int = 6
    # Condense a follow-up into a standalone query once a session has prior turns.
    condense_followups: bool = True

    # API service
    job_stream_poll_seconds: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
