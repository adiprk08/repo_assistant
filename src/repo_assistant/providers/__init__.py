"""Concrete provider adapters and a factory that selects them from settings.

Pipelines depend on the ``core.interfaces`` abstractions; this package is the one
place vendor SDKs are imported. The factory returns the real adapter when the
relevant API key is configured and otherwise raises a clear, actionable error —
tests inject fakes directly rather than going through the factory.
"""

from repo_assistant.core.config import Settings, get_settings
from repo_assistant.core.errors import ProviderError
from repo_assistant.core.interfaces import Embedder, LLMClient, Reranker
from repo_assistant.providers.anthropic_client import AnthropicLLMClient
from repo_assistant.providers.voyage import VoyageEmbedder, VoyageReranker

__all__ = [
    "AnthropicLLMClient",
    "VoyageEmbedder",
    "VoyageReranker",
    "get_embedder",
    "get_llm_client",
    "get_reranker",
]


def get_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    if not settings.voyage_api_key:
        raise ProviderError(
            "No embedding provider configured: set RA_VOYAGE_API_KEY (voyage-code-3). "
            "A local BGE-M3 fallback is planned but not yet implemented."
        )
    return VoyageEmbedder(
        api_key=settings.voyage_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


def get_llm_client(settings: Settings | None = None, *, model: str | None = None) -> LLMClient:
    settings = settings or get_settings()
    if not settings.anthropic_api_key:
        raise ProviderError("No LLM provider configured: set RA_ANTHROPIC_API_KEY.")
    return AnthropicLLMClient(
        api_key=settings.anthropic_api_key, model=model or settings.generation_model
    )


def get_reranker(settings: Settings | None = None) -> Reranker | None:
    """Return the reranker, or None when no provider is configured (rerank is optional)."""
    settings = settings or get_settings()
    if not settings.voyage_api_key:
        return None
    return VoyageReranker(api_key=settings.voyage_api_key, model=settings.reranker_model)
