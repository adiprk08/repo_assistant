"""Agent index tools against the real Qdrant + Postgres stack."""

from repo_assistant.core.fakes import FakeEmbedder
from repo_assistant.indexing.pipeline import index_working_tree
from repo_assistant.reasoning.tools import (
    ToolContext,
    execute_tool,
    get_symbol,
    graph_neighbors,
    list_dir,
    read_span,
    search_code,
)
from tests.integration.conftest import requires_stack

pytestmark = requires_stack


async def _index_and_ctx(local_repo, qdrant_index, session_factory) -> ToolContext:
    embedder = FakeEmbedder(dimensions=32)
    result = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )
    return ToolContext(
        repo_id=str(result.repo_id),
        snapshot_id=str(result.snapshot_id),
        commit=local_repo.commit_sha,
        embedder=embedder,
        vector_index=qdrant_index,
        session_factory=session_factory,
    )


async def test_get_symbol_returns_definition_and_records_chunk(
    local_repo, qdrant_index, session_factory
) -> None:
    ctx = await _index_and_ctx(local_repo, qdrant_index, session_factory)
    out = await get_symbol(ctx, name="refresh")
    assert "SessionManager.refresh" in out
    assert "method" in out
    # The containing chunk is recorded for final grounding.
    assert any("refresh" in c.text for c in ctx.grounding_chunks())


async def test_read_span_returns_source_lines(local_repo, qdrant_index, session_factory) -> None:
    ctx = await _index_and_ctx(local_repo, qdrant_index, session_factory)
    out = await read_span(ctx, path="src/service.py", start=1, end=20)
    assert "class SessionManager" in out


async def test_graph_neighbors_lists_contained_members(
    local_repo, qdrant_index, session_factory
) -> None:
    ctx = await _index_and_ctx(local_repo, qdrant_index, session_factory)
    out = await graph_neighbors(ctx, symbol="SessionManager")
    # contains edges: SessionManager -> refresh / revoke.
    assert "refresh" in out or "revoke" in out


async def test_search_code_finds_and_records(local_repo, qdrant_index, session_factory) -> None:
    ctx = await _index_and_ctx(local_repo, qdrant_index, session_factory)
    out = await search_code(ctx, query="refresh a token", k=5)
    assert "src/service.py" in out
    assert ctx.grounding_chunks()


async def test_list_dir_lists_files_under_prefix(local_repo, qdrant_index, session_factory) -> None:
    ctx = await _index_and_ctx(local_repo, qdrant_index, session_factory)
    out = await list_dir(ctx, prefix="src")
    assert "service.py" in out
    assert "util.py" in out


async def test_execute_tool_reports_unknown_and_bad_args(
    local_repo, qdrant_index, session_factory
) -> None:
    ctx = await _index_and_ctx(local_repo, qdrant_index, session_factory)
    content, is_error = await execute_tool(ctx, "nope", {})
    assert is_error and "Unknown tool" in content
    content, is_error = await execute_tool(ctx, "get_symbol", {"wrong": "arg"})
    assert is_error and "Invalid arguments" in content
