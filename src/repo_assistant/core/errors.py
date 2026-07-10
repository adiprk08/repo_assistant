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
