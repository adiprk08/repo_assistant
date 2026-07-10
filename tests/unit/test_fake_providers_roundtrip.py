"""Exercises the full retrieval-then-generation shape against fake providers only —
no network access, no infrastructure. This is the Phase 0 exit-criteria smoke test:
it proves the provider interfaces are actually usable end to end.
"""

from repo_assistant.core.fakes import FakeEmbedder, FakeLLMClient, FakeReranker, FakeVectorIndex
from repo_assistant.core.interfaces import Document, Message, VectorPoint

REPO_ID = "test-repo"


async def test_embed_upsert_query_rerank_generate_roundtrip() -> None:
    embedder = FakeEmbedder()
    index = FakeVectorIndex()
    reranker = FakeReranker()
    llm = FakeLLMClient()

    chunks = {
        "chunk-1": "session refresh: reissues a token when the session is near expiry",
        "chunk-2": "session expiry is tracked on the session model as a timestamp",
        "chunk-3": "totally unrelated helper for formatting currency values",
    }
    vectors = await embedder.embed(list(chunks.values()))
    await index.upsert(
        [
            VectorPoint(
                id=chunk_id,
                dense_vector=vector,
                sparse_vector=None,
                payload={"repo_id": REPO_ID, "path": f"src/{chunk_id}.py", "content": content},
            )
            for (chunk_id, content), vector in zip(chunks.items(), vectors, strict=True)
        ]
    )

    query = "session refresh"
    (query_vector,) = await embedder.embed([query])
    results = await index.query(repo_id=REPO_ID, dense_vector=query_vector, limit=3)
    assert {r.id for r in results} == set(chunks)

    reranked = await reranker.rerank(
        query=query, documents=[r.payload["content"] for r in results], top_k=2
    )
    assert len(reranked) == 2
    top_result = results[reranked[0].index]
    assert top_result.id in ("chunk-1", "chunk-2")

    response = await llm.generate(
        messages=[Message(role="user", content=query)],
        system="Answer using only the provided documents.",
        documents=[
            Document(
                id=top_result.id,
                title=top_result.payload["path"],
                content=top_result.payload["content"],
            )
        ],
    )
    assert top_result.payload["content"] in response.text
    assert response.usage.input_tokens > 0


async def test_generate_without_documents_declines_to_answer() -> None:
    llm = FakeLLMClient()
    response = await llm.generate(messages=[Message(role="user", content="anything")])
    assert "could not find" in response.text.lower()


async def test_vector_index_is_partitioned_by_repo_id() -> None:
    embedder = FakeEmbedder()
    index = FakeVectorIndex()
    (vector,) = await embedder.embed(["shared content"])

    await index.upsert(
        [
            VectorPoint(
                id="p1", dense_vector=vector, sparse_vector=None, payload={"repo_id": "repo-a"}
            )
        ]
    )

    results_for_other_repo = await index.query(repo_id="repo-b", dense_vector=vector, limit=10)
    assert results_for_other_repo == []
