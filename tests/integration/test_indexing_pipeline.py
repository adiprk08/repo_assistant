"""End-to-end indexing against the real Qdrant + Postgres stack, with a fake
embedder (zero API cost). Skipped when the stack isn't running.
"""

import uuid

from sqlalchemy import func, select

from repo_assistant.core.fakes import FakeEmbedder
from repo_assistant.indexing.pipeline import index_working_tree
from repo_assistant.storage.models import Chunk, Repo, Snapshot, Symbol
from tests.integration.conftest import requires_stack

pytestmark = requires_stack


async def test_index_working_tree_persists_vectors_and_metadata(
    local_repo, qdrant_index, session_factory
) -> None:
    embedder = FakeEmbedder(dimensions=32)

    result = await index_working_tree(
        local_repo,
        embedder=embedder,
        vector_index=qdrant_index,
        session_factory=session_factory,
    )

    assert result.n_files == 3
    assert result.n_chunks > 0
    assert result.n_symbols >= 3  # SessionManager, refresh, revoke, slugify

    # Snapshot promoted to active and READY.
    async with session_factory() as session:
        repo_row = await session.get(Repo, result.repo_id)
        assert repo_row is not None
        assert repo_row.active_snapshot_id == result.snapshot_id
        snapshot = await session.get(Snapshot, result.snapshot_id)
        assert snapshot.status == "ready"

        chunk_count = await session.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.snapshot_id == result.snapshot_id)
        )
        assert chunk_count == result.n_chunks

        symbols = (
            (
                await session.execute(
                    select(Symbol.qualified_name).where(Symbol.snapshot_id == result.snapshot_id)
                )
            )
            .scalars()
            .all()
        )
        assert "SessionManager.refresh" in symbols


async def test_indexed_vectors_are_queryable_and_tenant_scoped(
    local_repo, qdrant_index, session_factory
) -> None:
    embedder = FakeEmbedder(dimensions=32)
    result = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )

    (query_vec,) = await embedder.embed(["session refresh token"], input_type="query")
    hits = await qdrant_index.query(repo_id=str(result.repo_id), dense_vector=query_vec, limit=5)
    assert hits
    assert all("path" in h.payload and "text" in h.payload for h in hits)
    assert all(h.payload["repo_id"] == str(result.repo_id) for h in hits)

    # A different tenant sees nothing in this collection.
    empty = await qdrant_index.query(repo_id="some-other-repo", dense_vector=query_vec, limit=5)
    assert empty == []


async def test_embedding_cache_avoids_reembedding(
    local_repo, qdrant_index, session_factory
) -> None:
    from repo_assistant.indexing.cache import CachingEmbedder, EmbeddingCacheStore

    class CountingEmbedder(FakeEmbedder):
        def __init__(self) -> None:
            # Unique model name isolates this test's cache namespace from other
            # tests that index identical (repo-relative) content in the shared DB.
            super().__init__(dimensions=32, model_name=f"counting-{uuid.uuid4().hex}")
            self.embed_calls = 0

        async def embed(self, texts, *, input_type="document"):
            self.embed_calls += len(texts)
            return await super().embed(texts, input_type=input_type)

    inner = CountingEmbedder()
    caching = CachingEmbedder(inner, EmbeddingCacheStore(session_factory))

    await index_working_tree(
        local_repo, embedder=caching, vector_index=qdrant_index, session_factory=session_factory
    )
    first_pass_calls = inner.embed_calls
    assert first_pass_calls > 0

    # Re-index the identical tree: every chunk hash is cached, so no new embeds.
    await index_working_tree(
        local_repo, embedder=caching, vector_index=qdrant_index, session_factory=session_factory
    )
    assert inner.embed_calls == first_pass_calls
