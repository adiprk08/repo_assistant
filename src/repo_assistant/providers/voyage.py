"""voyage-code-3 embedding adapter (docs/adr/0003-embedding-strategy.md).

Wraps the Voyage async SDK behind the ``Embedder`` interface. Requests are
token-batched to stay within Voyage's per-request limits; retries with backoff
handle transient rate limits.
"""

from collections.abc import Iterator

import voyageai.error as voyage_error
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from voyageai.client_async import AsyncClient

from repo_assistant.core.errors import ProviderError
from repo_assistant.core.interfaces import Embedder, InputType, Reranker, RerankResult
from repo_assistant.core.logging import get_logger
from repo_assistant.core.tokens import estimate_tokens

logger = get_logger(__name__)

# Conservative caps below Voyage's documented per-request limits (1000 texts /
# 120k tokens) to leave headroom for our token *estimate* being approximate.
_MAX_BATCH_TEXTS = 128
_MAX_BATCH_TOKENS = 100_000


def _batches(texts: list[str]) -> Iterator[list[str]]:
    batch: list[str] = []
    batch_tokens = 0
    for text in texts:
        text_tokens = estimate_tokens(text)
        if batch and (
            len(batch) >= _MAX_BATCH_TEXTS or batch_tokens + text_tokens > _MAX_BATCH_TOKENS
        ):
            yield batch
            batch, batch_tokens = [], 0
        batch.append(text)
        batch_tokens += text_tokens
    if batch:
        yield batch


class VoyageEmbedder(Embedder):
    def __init__(
        self,
        api_key: str,
        model: str = "voyage-code-3",
        dimensions: int = 1024,
    ) -> None:
        self._client = AsyncClient(api_key=api_key)
        self._model = model
        self._dimensions = dimensions

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @retry(
        # Retry transient failures — including free-tier rate limits, whose window
        # can be up to a minute, so the backoff must be patient enough to clear it.
        retry=retry_if_exception_type(
            (
                voyage_error.RateLimitError,
                voyage_error.ServerError,
                voyage_error.ServiceUnavailableError,
            )
        ),
        wait=wait_exponential(multiplier=2, min=4, max=64),
        stop=stop_after_attempt(7),
        reraise=True,
    )
    async def _embed_batch(self, batch: list[str], input_type: InputType) -> list[list[float]]:
        result = await self._client.embed(
            batch,
            model=self._model,
            input_type=input_type,
            output_dimension=self._dimensions,
        )
        return [[float(v) for v in vector] for vector in result.embeddings]

    async def embed(
        self, texts: list[str], *, input_type: InputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []
        embeddings: list[list[float]] = []
        try:
            for batch in _batches(texts):
                embeddings.extend(await self._embed_batch(batch, input_type))
        except voyage_error.VoyageError as exc:
            raise ProviderError(f"Voyage embedding failed: {exc}") from exc
        return embeddings


class VoyageReranker(Reranker):
    """Cross-encoder reranking via Voyage rerank-2.5 (docs/adr/0004)."""

    def __init__(self, api_key: str, model: str = "rerank-2.5") -> None:
        self._client = AsyncClient(api_key=api_key)
        self._model = model

    @retry(
        retry=retry_if_exception_type(
            (
                voyage_error.RateLimitError,
                voyage_error.ServerError,
                voyage_error.ServiceUnavailableError,
            )
        ),
        wait=wait_exponential(multiplier=2, min=4, max=64),
        stop=stop_after_attempt(7),
        reraise=True,
    )
    async def rerank(self, *, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        if not documents:
            return []
        try:
            result = await self._client.rerank(query, documents, model=self._model, top_k=top_k)
        except voyage_error.VoyageError as exc:
            raise ProviderError(f"Voyage rerank failed: {exc}") from exc
        return [RerankResult(index=r.index, score=r.relevance_score) for r in result.results]
