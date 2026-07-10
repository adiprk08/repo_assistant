"""Qdrant-backed vector index (docs/adr/0004-vector-store-and-hybrid-retrieval.md).

Phase 1 is dense-only: a single named vector ``dense`` per point, with all points
sharing one collection and partitioned by an indexed ``repo_id`` payload field
(docs/adr/0009-multitenancy-and-versioning.md). Sparse vectors and server-side
hybrid fusion arrive in Phase 2.
"""

from typing import Any

from qdrant_client import AsyncQdrantClient, models

from repo_assistant.core.interfaces import SearchResult, VectorIndex, VectorPoint
from repo_assistant.core.logging import get_logger

logger = get_logger(__name__)

DENSE_VECTOR = "dense"
_TENANT_KEY = "repo_id"


class QdrantVectorIndex(VectorIndex):
    def __init__(self, client: AsyncQdrantClient, collection: str = "chunks") -> None:
        self._client = client
        self._collection = collection

    @classmethod
    def from_url(cls, url: str, collection: str = "chunks") -> "QdrantVectorIndex":
        # check_compatibility disabled: we rely only on the stable collection/point/query
        # API. Aligning client and server versions is a Phase 5 hardening task.
        return cls(AsyncQdrantClient(url=url, check_compatibility=False), collection=collection)

    async def prepare(self, dimensions: int) -> None:
        """Create the collection and the tenant payload index if absent (idempotent)."""
        if await self._client.collection_exists(self._collection):
            return
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                DENSE_VECTOR: models.VectorParams(size=dimensions, distance=models.Distance.COSINE)
            },
        )
        # Indexing the tenant key keeps per-repo filtering fast as repos accumulate.
        await self._client.create_payload_index(
            collection_name=self._collection,
            field_name=_TENANT_KEY,
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        logger.info("created qdrant collection", collection=self._collection, dims=dimensions)

    async def upsert(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        await self._client.upsert(
            collection_name=self._collection,
            points=[
                models.PointStruct(
                    id=p.id, vector={DENSE_VECTOR: p.dense_vector}, payload=p.payload
                )
                for p in points
            ],
        )

    async def query(
        self,
        *,
        repo_id: str,
        dense_vector: list[float],
        sparse_vector: dict[int, float] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        conditions: list[models.Condition] = [
            models.FieldCondition(key=_TENANT_KEY, match=models.MatchValue(value=repo_id))
        ]
        for key, value in (filters or {}).items():
            conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))

        response = await self._client.query_points(
            collection_name=self._collection,
            query=dense_vector,
            using=DENSE_VECTOR,
            query_filter=models.Filter(must=conditions),
            limit=limit,
            with_payload=True,
        )
        return [
            SearchResult(id=str(point.id), score=point.score, payload=point.payload or {})
            for point in response.points
        ]

    async def fetch(self, *, repo_id: str, ids: list[str]) -> list[SearchResult]:
        """Retrieve points by id (score 0.0) — used to materialize chunks that a
        non-vector channel (e.g. symbol lookup) surfaced."""
        if not ids:
            return []
        points = await self._client.retrieve(
            collection_name=self._collection, ids=list(ids), with_payload=True
        )
        return [SearchResult(id=str(p.id), score=0.0, payload=p.payload or {}) for p in points]

    async def delete(self, *, repo_id: str, ids: list[str]) -> None:
        if not ids:
            return
        await self._client.delete(
            collection_name=self._collection,
            points_selector=models.PointIdsList(points=list(ids)),
        )

    async def delete_repo(self, repo_id: str) -> None:
        """Remove every point for a tenant (used when re-indexing replaces a snapshot)."""
        await self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key=_TENANT_KEY, match=models.MatchValue(value=repo_id)
                        )
                    ]
                )
            ),
        )
