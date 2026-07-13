"""MCP server adapter: tool-schema conversion and call dispatch (no infra)."""

import mcp.types as types
import pytest

from repo_assistant.mcp_server import server as mcp_server
from repo_assistant.reasoning.tools import TOOL_SCHEMAS


def test_mcp_tools_mirror_the_shared_schemas() -> None:
    tools = mcp_server.mcp_tools()
    assert {t.name for t in tools} == {s["name"] for s in TOOL_SCHEMAS}
    assert all(isinstance(t, types.Tool) for t in tools)
    by_name = {t.name: t for t in tools}
    # inputSchema is carried through verbatim so IDE agents see the same contract.
    assert by_name["search_code"].inputSchema["required"] == ["query"]
    assert "path" in by_name["read_span"].inputSchema["properties"]


async def test_dispatch_wraps_success_as_text_content(monkeypatch) -> None:
    async def fake_execute(ctx, name, arguments):
        return "result body", False

    monkeypatch.setattr(mcp_server, "execute_tool", fake_execute)
    out = await mcp_server.dispatch(None, "search_code", {"query": "x"})  # type: ignore[arg-type]
    assert len(out) == 1
    assert isinstance(out[0], types.TextContent)
    assert out[0].text == "result body"


async def test_dispatch_raises_on_tool_error(monkeypatch) -> None:
    async def fake_execute(ctx, name, arguments):
        return "Unknown tool: 'nope'.", True

    monkeypatch.setattr(mcp_server, "execute_tool", fake_execute)
    with pytest.raises(ValueError, match="Unknown tool"):
        await mcp_server.dispatch(None, "nope", {})  # type: ignore[arg-type]


async def test_dispatch_tolerates_none_arguments(monkeypatch) -> None:
    seen = {}

    async def fake_execute(ctx, name, arguments):
        seen["args"] = arguments
        return "ok", False

    monkeypatch.setattr(mcp_server, "execute_tool", fake_execute)
    await mcp_server.dispatch(None, "list_dir", None)  # type: ignore[arg-type]
    assert seen["args"] == {}  # None normalized to empty dict
