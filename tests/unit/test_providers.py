"""Provider adapters, exercised against mocked SDK responses (no network)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from repo_assistant.core.interfaces import Document, Message
from repo_assistant.providers.anthropic_client import AnthropicLLMClient, _build_messages
from repo_assistant.providers.voyage import VoyageEmbedder, VoyageReranker, _batches

# --- Voyage embedder ---------------------------------------------------------


def test_batches_respects_text_count_limit() -> None:
    texts = [f"t{i}" for i in range(300)]
    batches = list(_batches(texts))
    assert sum(len(b) for b in batches) == 300
    assert all(len(b) <= 128 for b in batches)


def test_batches_splits_on_token_budget() -> None:
    big = "x" * 500_000  # ~125k estimated tokens, over the 100k batch budget
    batches = list(_batches([big, big]))
    assert len(batches) == 2


async def test_voyage_embed_batches_and_flattens(monkeypatch) -> None:
    embedder = VoyageEmbedder(api_key="test-key", dimensions=4)
    calls: list[list[str]] = []

    async def fake_embed(batch, **kwargs):
        calls.append(batch)
        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3, 0.4] for _ in batch])

    monkeypatch.setattr(embedder._client, "embed", AsyncMock(side_effect=fake_embed))

    texts = [f"chunk {i}" for i in range(200)]
    vectors = await embedder.embed(texts, input_type="document")

    assert len(vectors) == 200
    assert all(len(v) == 4 for v in vectors)
    assert len(calls) == 2  # 200 texts -> two batches of <=128


async def test_voyage_embed_empty_is_noop() -> None:
    embedder = VoyageEmbedder(api_key="test-key")
    assert await embedder.embed([]) == []


async def test_voyage_reranker_parses_and_orders_results(monkeypatch) -> None:
    reranker = VoyageReranker(api_key="test-key")
    response = SimpleNamespace(
        results=[
            SimpleNamespace(index=2, relevance_score=0.9),
            SimpleNamespace(index=0, relevance_score=0.4),
        ]
    )
    monkeypatch.setattr(reranker._client, "rerank", AsyncMock(return_value=response))

    out = await reranker.rerank(query="q", documents=["a", "b", "c"], top_k=2)
    assert [(r.index, r.score) for r in out] == [(2, 0.9), (0, 0.4)]


async def test_voyage_reranker_empty_is_noop() -> None:
    reranker = VoyageReranker(api_key="test-key")
    assert await reranker.rerank(query="q", documents=[], top_k=5) == []


# --- Anthropic client --------------------------------------------------------


def test_build_messages_attaches_documents_to_last_turn() -> None:
    messages = [Message("user", "earlier"), Message("assistant", "reply"), Message("user", "now?")]
    docs = [Document(id="c1", title="a.py:1-3", content="def f(): ...")]
    api_messages = _build_messages(messages, docs)

    assert len(api_messages) == 3
    assert api_messages[0] == {"role": "user", "content": "earlier"}
    last = api_messages[-1]
    assert last["role"] == "user"
    doc_block, text_block = last["content"]
    assert doc_block["type"] == "document"
    assert doc_block["citations"] == {"enabled": True}
    assert doc_block["source"]["data"] == "def f(): ..."
    assert text_block == {"type": "text", "text": "now?"}


async def test_anthropic_parses_text_citations_and_usage(monkeypatch) -> None:
    client = AnthropicLLMClient(api_key="test-key")

    citation = SimpleNamespace(
        type="char_location",
        document_index=0,
        start_char_index=0,
        end_char_index=11,
        cited_text="def f(): ..",
    )
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="It defines f.", citations=[citation])],
        usage=SimpleNamespace(
            input_tokens=42,
            output_tokens=7,
            cache_read_input_tokens=10,
            cache_creation_input_tokens=0,
        ),
        stop_reason="end_turn",
    )
    monkeypatch.setattr(client._client.messages, "create", AsyncMock(return_value=response))

    result = await client.generate(
        messages=[Message("user", "what does f do?")],
        documents=[Document(id="c1", title="a.py:1-3", content="def f(): ...")],
    )

    assert result.text == "It defines f."
    assert len(result.citations) == 1
    assert result.citations[0].document_index == 0
    assert result.citations[0].cited_text == "def f(): .."
    assert result.usage.input_tokens == 42
    assert result.usage.cache_read_tokens == 10


async def test_anthropic_surfaces_tool_calls(monkeypatch) -> None:
    client = AnthropicLLMClient(api_key="test-key")
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Let me search.", citations=None),
            SimpleNamespace(type="tool_use", id="t1", name="search_code", input={"query": "auth"}),
        ],
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
        stop_reason="tool_use",
    )
    monkeypatch.setattr(client._client.messages, "create", AsyncMock(return_value=response))

    result = await client.generate(messages=[Message("user", "where is auth?")])

    assert result.stop_reason == "tool_use"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "search_code"
    assert result.tool_calls[0].arguments == {"query": "auth"}
