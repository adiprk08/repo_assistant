class RepoAssistantError(Exception):
    """Base class for all errors raised by repo_assistant pipeline code."""


class NotFoundError(RepoAssistantError):
    """A requested entity (repo, snapshot, symbol, chunk...) does not exist."""


class ValidationError(RepoAssistantError):
    """Input failed validation at a system boundary."""


class ProviderError(RepoAssistantError):
    """An external provider (LLM, embedder, reranker, vector store) call failed."""


class IngestionError(RepoAssistantError):
    """Cloning, scanning, or filtering a repository failed."""


class ParsingError(RepoAssistantError):
    """tree-sitter parsing or symbol extraction failed for a file."""


class IndexingError(RepoAssistantError):
    """Embedding, upserting, or otherwise persisting indexed data failed."""


class RetrievalError(RepoAssistantError):
    """Query understanding, candidate generation, or fusion failed."""


class ReasoningError(RepoAssistantError):
    """Routing, generation, or citation verification failed."""


class AuthenticationError(RepoAssistantError):
    """A request presented no credential, or an invalid/revoked one (HTTP 401)."""


class RateLimitError(RepoAssistantError):
    """A caller exceeded its request budget (HTTP 429)."""

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after
