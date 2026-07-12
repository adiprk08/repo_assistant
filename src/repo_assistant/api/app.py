"""FastAPI application factory.

A thin shell over the library (CLAUDE.md): the app owns one composition
``Runtime`` and one ``IngestionQueue`` for its lifetime, and each router wraps a
library call. Streaming (SSE) is used for the two long/interactive surfaces —
ingestion-job progress and chat — per docs/ARCHITECTURE.md §2.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from repo_assistant.api.auth import secured
from repo_assistant.api.errors import register_error_handlers
from repo_assistant.api.ratelimit import NoopRateLimiter, RateLimiter, RedisRateLimiter
from repo_assistant.api.routers import chat, repos, search, sessions, webhooks
from repo_assistant.cli.runtime import Runtime, build_runtime
from repo_assistant.core.config import Settings, get_settings
from repo_assistant.core.logging import configure_logging
from repo_assistant.workers.queue import IngestionQueue


def _default_rate_limiter(settings: Settings) -> RateLimiter:
    if not settings.rate_limit_enabled:
        return NoopRateLimiter()
    return RedisRateLimiter(
        settings.redis_dsn,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


def create_app(
    *,
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    queue: IngestionQueue | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Build the API app.

    ``runtime``/``queue``/``rate_limiter`` are injectable so tests can supply
    fakes; in production they are composed from settings at startup and closed at
    shutdown.
    """
    settings = settings or get_settings()
    owns_runtime = runtime is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        app.state.settings = settings
        app.state.runtime = runtime or build_runtime(settings)
        app.state.queue = queue or IngestionQueue(settings.redis_dsn)
        app.state.rate_limiter = rate_limiter or _default_rate_limiter(settings)
        try:
            yield
        finally:
            await app.state.rate_limiter.aclose()
            await app.state.queue.aclose()
            if owns_runtime:
                await app.state.runtime.aclose()

    app = FastAPI(
        title="Repo Assistant",
        version="0.1.0",
        summary="RAG-powered GitHub repository assistant.",
        lifespan=lifespan,
    )
    register_error_handlers(app)

    # Browser UI runs on a different origin; allow it to call the API and read
    # the streaming responses. Authorization is a header, so credentials aren't
    # cookie-based — allow_credentials stays off.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Retry-After"],
        )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Every data route requires a valid API key + is rate-limited; /health stays open.
    protected = [Depends(secured)]
    app.include_router(repos.router, dependencies=protected)
    app.include_router(sessions.router, dependencies=protected)
    app.include_router(search.router, dependencies=protected)
    app.include_router(chat.router, dependencies=protected)
    # Webhooks authenticate by HMAC signature, not API key — no `secured` dependency.
    app.include_router(webhooks.router)
    return app
