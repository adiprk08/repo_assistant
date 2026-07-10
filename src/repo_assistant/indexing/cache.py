"""Content-addressed embedding cache (docs/adr/0003, RISKS #1).

``CachingEmbedder`` wraps any ``Embedder``: document embeddings are looked up by
``sha256(text)`` and only cache misses hit the underlying provider, so
re-indexing unchanged content costs nothing. Query embeddings bypass the cache
(they are unique and short-lived).
"""

import hashlib

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.interfaces import Embedder, InputType
from repo_assistant.core.logging import get_logger
from repo_assistant.storage.models import EmbeddingCache

logger = get_logger(__name__)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCacheStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_many(
        self, model: str, dimensions: int, hashes: list[str]
    ) -> dict[str, list[float]]:
        if not hashes:
            return {}
        async with self._session_factory() as session:
            rows = await session.execute(
                select(EmbeddingCache.content_hash, EmbeddingCache.vector).where(
                    EmbeddingCache.model == model,
                    EmbeddingCache.dimensions == dimensions,
                    EmbeddingCache.content_hash.in_(hashes),
                )
            )
            return {h: v for h, v in rows.all()}

    async def put_many(self, model: str, dimensions: int, vectors: dict[str, list[float]]) -> None:
        if not vectors:
            return
        async with self._session_factory() as session:
            stmt = insert(EmbeddingCache).values(
                [
                    {
                        "content_hash": h,
                        "model": model,
                        "dimensions": dimensions,
                        "vector": vec,
                    }
                    for h, vec in vectors.items()
                ]
            )
            await session.execute(stmt.on_conflict_do_nothing())
            await session.commit()


class CachingEmbedder(Embedder):
    def __init__(self, inner: Embedder, store: EmbeddingCacheStore) -> None:
        self._inner = inner
        self._store = store

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed(
        self, texts: list[str], *, input_type: InputType = "document"
    ) -> list[list[float]]:
        if not texts:
            return []
        if input_type == "query":
            return await self._inner.embed(texts, input_type=input_type)

        model, dims = self._inner.model_name, self._inner.dimensions
        hashes = [content_hash(t) for t in texts]
        cached = await self._store.get_many(model, dims, list(dict.fromkeys(hashes)))

        miss_indices = [i for i, h in enumerate(hashes) if h not in cached]
        if miss_indices:
            miss_vectors = await self._inner.embed(
                [texts[i] for i in miss_indices], input_type=input_type
            )
            fresh = {hashes[i]: vec for i, vec in zip(miss_indices, miss_vectors, strict=True)}
            await self._store.put_many(model, dims, fresh)
            cached.update(fresh)

        logger.info(
            "embedding cache",
            total=len(texts),
            hits=len(texts) - len(miss_indices),
            misses=len(miss_indices),
        )
        return [cached[h] for h in hashes]
