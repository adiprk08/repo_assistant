"""FastAPI application factory.

A thin shell over the library (CLAUDE.md): the app owns one composition
``Runtime`` and one ``IngestionQueue`` for its lifetime, and each router wraps a
library call. Streaming (SSE) is used for the two long/interactive surfaces —
ingestion-job progress and chat — per docs/ARCHITECTURE.md §2.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from repo_assistant.api.errors import register_error_handlers
from repo_assistant.api.routers import chat, repos, search
from repo_assistant.cli.runtime import Runtime, build_runtime
from repo_assistant.core.config import Settings, get_settings
from repo_assistant.core.logging import configure_logging
from repo_assistant.workers.queue import IngestionQueue


def create_app(
    *,
    settings: Settings | None = None,
    runtime: Runtime | None = None,
    queue: IngestionQueue | None = None,
) -> FastAPI:
    """Build the API app.

    ``runtime``/``queue`` are injectable so tests can supply fakes; in production
    they are composed from settings at startup and closed at shutdown.
    """
    settings = settings or get_settings()
    owns_runtime = runtime is None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        app.state.settings = settings
        app.state.runtime = runtime or build_runtime(settings)
        app.state.queue = queue or IngestionQueue(settings.redis_dsn)
        try:
            yield
        finally:
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

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(repos.router)
    app.include_router(search.router)
    app.include_router(chat.router)
    return app
