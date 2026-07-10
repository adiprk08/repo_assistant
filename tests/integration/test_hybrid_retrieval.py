"""Symbol channel + hybrid retrieval against the real Qdrant + Postgres stack."""

from repo_assistant.core.fakes import FakeEmbedder, FakeReranker
from repo_assistant.indexing.pipeline import index_working_tree
from repo_assistant.retrieval import hybrid_retrieve
from repo_assistant.retrieval.symbols import symbol_search
from tests.integration.conftest import requires_stack

pytestmark = requires_stack


async def test_symbol_search_finds_chunk_for_identifier(
    local_repo, qdrant_index, session_factory
) -> None:
    result = await index_working_tree(
        local_repo,
        embedder=FakeEmbedder(dimensions=32),
        vector_index=qdrant_index,
        session_factory=session_factory,
    )

    # "refresh" should resolve to the chunk containing SessionManager.refresh.
    chunk_ids = await symbol_search(
        session_factory, str(result.snapshot_id), "how does refresh work"
    )
    assert chunk_ids

    fetched = await qdrant_index.fetch(repo_id=str(result.repo_id), ids=chunk_ids)
    texts = " ".join(f.payload["text"] for f in fetched)
    assert "refresh" in texts


async def test_symbol_search_fuzzy_matches_misspelling(
    local_repo, qdrant_index, session_factory
) -> None:
    result = await index_working_tree(
        local_repo,
        embedder=FakeEmbedder(dimensions=32),
        vector_index=qdrant_index,
        session_factory=session_factory,
    )
    # Trigram similarity should still catch a near-miss ("slugfy" ~ "slugify").
    chunk_ids = await symbol_search(session_factory, str(result.snapshot_id), "the slugfy helper")
    fetched = await qdrant_index.fetch(repo_id=str(result.repo_id), ids=chunk_ids)
    assert any("slugify" in f.payload["text"] for f in fetched)


async def test_hybrid_retrieve_returns_ranked_chunks(
    local_repo, qdrant_index, session_factory
) -> None:
    embedder = FakeEmbedder(dimensions=32)
    result = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )

    chunks = await hybrid_retrieve(
        str(result.repo_id),
        str(result.snapshot_id),
        "how does token refresh work",
        embedder=embedder,
        vector_index=qdrant_index,
        session_factory=session_factory,
        commit=local_repo.commit_sha,
        limit=5,
    )
    assert chunks
    assert all(c.text for c in chunks)
    # The refresh method chunk should surface via the symbol channel.
    assert any("refresh" in c.text for c in chunks)


async def test_hybrid_dense_only_still_works(local_repo, qdrant_index, session_factory) -> None:
    embedder = FakeEmbedder(dimensions=32)
    result = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )
    chunks = await hybrid_retrieve(
        str(result.repo_id),
        str(result.snapshot_id),
        "session token",
        embedder=embedder,
        vector_index=qdrant_index,
        session_factory=session_factory,
        commit=local_repo.commit_sha,
        use_symbols=False,
    )
    assert chunks


async def test_sparse_channel_retrieves_by_lexical_match(
    local_repo, qdrant_index, session_factory
) -> None:
    embedder = FakeEmbedder(dimensions=32)
    result = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )
    # Query the sparse (BM25) vector directly for an identifier in the source.
    from repo_assistant.core.sparse import text_to_sparse

    hits = await qdrant_index.query_sparse(
        repo_id=str(result.repo_id), sparse_vector=text_to_sparse("slugify text"), limit=5
    )
    assert hits
    assert any("slugify" in h.payload["text"] for h in hits)


async def test_graph_channel_surfaces_neighbors(local_repo, qdrant_index, session_factory) -> None:
    from repo_assistant.graph.search import graph_search

    embedder = FakeEmbedder(dimensions=32)
    result = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )
    # SessionManager contains refresh/revoke -> naming the class should surface
    # chunks for its members via contains edges.
    chunk_ids = await graph_search(session_factory, str(result.snapshot_id), "SessionManager")
    fetched = await qdrant_index.fetch(repo_id=str(result.repo_id), ids=chunk_ids)
    texts = " ".join(f.payload["text"] for f in fetched)
    assert "refresh" in texts or "revoke" in texts


async def test_hybrid_retrieve_with_reranker(local_repo, qdrant_index, session_factory) -> None:
    embedder = FakeEmbedder(dimensions=32)
    result = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )
    chunks = await hybrid_retrieve(
        str(result.repo_id),
        str(result.snapshot_id),
        "refresh token session",
        embedder=embedder,
        vector_index=qdrant_index,
        session_factory=session_factory,
        reranker=FakeReranker(),
        commit=local_repo.commit_sha,
        limit=3,
    )
    assert chunks
    assert len(chunks) <= 3
    # FakeReranker orders by token overlap, so the refresh chunk should rank well.
    assert any("refresh" in c.text for c in chunks)
