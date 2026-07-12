"""Map the library error taxonomy (core/errors.py) to HTTP responses.

Registered once on the app so routers can let domain errors propagate instead of
translating status codes inline.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from repo_assistant.core.errors import (
    IngestionError,
    NotFoundError,
    ProviderError,
    RepoAssistantError,
    ValidationError,
)
from repo_assistant.core.logging import get_logger

logger = get_logger(__name__)

# Domain error -> HTTP status. Anything not listed is an unexpected server fault.
_STATUS: list[tuple[type[RepoAssistantError], int]] = [
    (NotFoundError, 404),
    (ValidationError, 422),
    (IngestionError, 400),  # bad/again-untrusted repo URL is a client error
    (ProviderError, 502),  # an upstream (LLM/embedder/Qdrant/Redis) failed
]


def _status_for(exc: RepoAssistantError) -> int:
    for exc_type, status in _STATUS:
        if isinstance(exc, exc_type):
            return status
    return 500


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RepoAssistantError)
    async def _handle(request: Request, exc: RepoAssistantError) -> JSONResponse:
        status = _status_for(exc)
        if status >= 500:
            logger.error("unhandled domain error", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=status,
            content={"error": type(exc).__name__, "detail": str(exc)},
        )
