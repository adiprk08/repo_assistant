"""In-memory fake providers for tests.

These implement the `core.interfaces` ABCs with deterministic, dependency-free
logic so pipelines can be exercised end-to-end without network access,
API keys, or running infrastructure (Qdrant/Postgres/Redis).
"""

import hashlib
import math
from typing import Any

from repo_assistant.core.interfaces import (
    Document,
    Embedder,
    InputType,
    LLMClient,
    LLMResponse,
    Message,
    OnText,
    Reranker,
    RerankResult,
    SearchResult,
    Usage,
    VectorIndex,
    VectorPoint,
)


class FakeEmbedder(Embedder):
    """Deterministic hash-based embedding: same text always yields the same vector."""

    def __init__(self, dimensions: int = 32, model_name: str = "fake-embedder") -> None:
        self._dimensions = dimensions
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(
        self, texts: list[str], *, input_type: InputType = "document"
    ) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i % len(digest)] / 255.0 for i in range(self._dimensions)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]


class FakeReranker(Reranker):
    """Reranks by token-overlap count between the query and each document."""

    async def rerank(self, *, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        query_tokens = set(query.lower().split())
        scored = [
            RerankResult(index=i, score=float(len(query_tokens & set(doc.lower().split()))))
            for i, doc in enumerate(documents)
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]


class FakeVectorIndex(VectorIndex):
    """In-memory vector store, partitioned by repo_id, ranked by cosine similarity."""

    def __init__(self) -> None:
        self._points: dict[str, dict[str, VectorPoint]] = {}

    async def upsert(self, points: list[VectorPoint]) -> None:
        for point in points:
            repo_id = str(point.payload["repo_id"])
            self._points.setdefault(repo_id, {})[point.id] = point

    async def query(
        self,
        *,
        repo_id: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        candidates = self._points.get(repo_id, {}).values()
        if filters:
            candidates = [
                p for p in candidates if all(p.payload.get(k) == v for k, v in filters.items())
            ]
        scored = [
            SearchResult(
                id=p.id, score=_cosine_similarity(dense_vector, p.dense_vector), payload=p.payload
            )
            for p in candidates
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:limit]

    async def query_sparse(
        self,
        *,
        repo_id: str,
        sparse_vector: dict[int, float],
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        query_terms = set(sparse_vector)
        scored: list[SearchResult] = []
        for point in self._points.get(repo_id, {}).values():
            if filters and not all(point.payload.get(k) == v for k, v in filters.items()):
                continue
            overlap = query_terms & set(point.sparse_vector or {})
            if overlap:
                scored.append(
                    SearchResult(id=point.id, score=float(len(overlap)), payload=point.payload)
                )
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:limit]

    async def fetch(self, *, repo_id: str, ids: list[str]) -> list[SearchResult]:
        points = self._points.get(repo_id, {})
        return [
            SearchResult(id=pid, score=0.0, payload=points[pid].payload)
            for pid in ids
            if pid in points
        ]

    async def delete(self, *, repo_id: str, ids: list[str]) -> None:
        for point_id in ids:
            self._points.get(repo_id, {}).pop(point_id, None)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)


class FakeLLMClient(LLMClient):
    """Echoes a grounded answer built from whatever documents it was given."""

    async def generate(
        self,
        *,
        messages: list[Message],
        system: str = "",
        documents: list[Document] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        documents = documents or []
        if documents:
            text = f"Based on {documents[0].title}: {documents[0].content}"
        else:
            text = "I could not find this in the repository."
        prompt_chars = len(system) + sum(len(m.content) for m in messages)
        return LLMResponse(
            text=text,
            usage=Usage(input_tokens=prompt_chars // 4, output_tokens=len(text) // 4),
        )

    async def generate_stream(
        self,
        *,
        messages: list[Message],
        on_text: OnText,
        system: str = "",
        documents: list[Document] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Streams the answer word by word so consumers see multiple deltas."""
        response = await self.generate(
            messages=messages,
            system=system,
            documents=documents,
            tools=tools,
            max_tokens=max_tokens,
        )
        words = response.text.split(" ")
        for i, word in enumerate(words):
            await on_text(word if i == len(words) - 1 else word + " ")
        return response
