from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The repo-root .env, resolved from this file's location (…/src/repo_assistant/core/).
# Anchoring it absolutely means `ra` finds secrets no matter which directory it is
# launched from — a relative ".env" only loads when the CWD happens to be the root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    # Load a CWD-local .env if present, then the repo-root one (later wins); real
    # environment variables still take precedence over both, and a missing file is
    # simply ignored — so deployments that inject env vars are unaffected.
    model_config = SettingsConfigDict(
        env_file=(".env", str(_PROJECT_ROOT / ".env")),
        env_prefix="RA_",
        extra="ignore",
    )

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

    # Auth + rate limiting (docs/adr/0016)
    require_api_key: bool = True
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    # Browser UI origins allowed by CORS (comma-separated in the env var).
    cors_allow_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # GitHub webhook HMAC secret (docs/adr/0018). Unset -> the webhook rejects all.
    github_webhook_secret: str | None = None

    # Observability (docs/adr/0019)
    metrics_enabled: bool = True  # Prometheus /metrics endpoint + instrumentation
    otel_enabled: bool = False  # OTLP trace export (needs a collector/backend)
    otel_exporter_endpoint: str = "http://localhost:4318"  # OTLP/HTTP base URL
    otel_service_name: str = "repo-assistant"

    # Private repos via a GitHub App (docs/adr/0020). All optional — unset means
    # only public repos are supported.
    token_encryption_key: str | None = None  # Fernet key; encrypts stored tokens at rest
    github_app_id: str | None = None
    github_app_private_key: str | None = None  # PEM contents (or a path to a .pem)

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        # Accept a plain comma-separated env string as well as a JSON list.
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
