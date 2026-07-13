"""MCP server over the indexed repository (Phase 6, docs/adr/0022).

Exposes the same five read-only index tools the agent loop uses — `search_code`,
`get_symbol`, `read_span`, `graph_neighbors`, `list_dir` — over the Model Context
Protocol, so an IDE agent (Claude Desktop, Cursor, …) can explore a repo indexed by
Repo Assistant. The server is bound to one repo's active snapshot at launch; tools
read the index only (never the live filesystem), so answers stay reproducible and
the injection surface is minimal. Reuses `reasoning.tools` verbatim — no tool logic
is duplicated.
"""

import sys

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from repo_assistant.cli.runtime import build_runtime, resolve_indexed_repo
from repo_assistant.core.config import get_settings
from repo_assistant.core.logging import configure_logging, get_logger
from repo_assistant.reasoning.tools import TOOL_SCHEMAS, ToolContext, execute_tool

logger = get_logger(__name__)


def mcp_tools() -> list[types.Tool]:
    """Convert the shared tool schemas into MCP `Tool` descriptors."""
    return [
        types.Tool(
            name=schema["name"],
            description=schema.get("description", ""),
            inputSchema=schema["input_schema"],
        )
        for schema in TOOL_SCHEMAS
    ]


async def dispatch(ctx: ToolContext, name: str, arguments: dict | None) -> list[types.TextContent]:
    """Run one tool call and return its text content. Tool-call errors (unknown
    tool, bad arguments) raise so MCP marks the result as an error; a normal
    'not found' is ordinary content."""
    content, is_error = await execute_tool(ctx, name, arguments or {})
    if is_error:
        raise ValueError(content)
    return [types.TextContent(type="text", text=content)]


def build_server(ctx: ToolContext) -> Server:
    server: Server = Server("repo-assistant")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return mcp_tools()

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        return await dispatch(ctx, name, arguments)

    return server


async def serve(repo_identifier: str) -> None:
    """Bind to ``repo_identifier``'s active snapshot and serve MCP over stdio."""
    # stdout is the JSON-RPC channel — send all logs to stderr so they can't
    # corrupt the protocol stream (ADR-0022).
    configure_logging(get_settings(), stream=sys.stderr)
    runtime = build_runtime()
    try:
        resolved = await resolve_indexed_repo(runtime, repo_identifier)
        ctx = ToolContext(
            repo_id=str(resolved.repo_id),
            snapshot_id=str(resolved.snapshot_id),
            commit=resolved.commit_sha,
            embedder=runtime.embedder(),
            vector_index=runtime.vector_index,
            session_factory=runtime.session_factory,
        )
        logger.info("mcp server bound", repo=resolved.url, commit=resolved.commit_sha[:12])
        server = build_server(ctx)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        await runtime.aclose()
