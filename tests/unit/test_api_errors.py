"""The library-error -> HTTP-status mapping used by the API exception handler."""

import pytest

from repo_assistant.api.errors import _status_for
from repo_assistant.core.errors import (
    IndexingError,
    IngestionError,
    NotFoundError,
    ProviderError,
    ReasoningError,
    ValidationError,
)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (NotFoundError("x"), 404),
        (ValidationError("x"), 422),
        (IngestionError("bad url"), 400),
        (ProviderError("llm down"), 502),
        (IndexingError("x"), 500),  # unmapped domain error -> server fault
        (ReasoningError("x"), 500),
    ],
)
def test_status_for(exc: Exception, expected: int) -> None:
    assert _status_for(exc) == expected  # type: ignore[arg-type]
