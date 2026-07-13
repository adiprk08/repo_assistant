"""MCP server exposing Repo Assistant's read-only index tools to IDE agents."""

from repo_assistant.mcp_server.server import build_server, dispatch, mcp_tools, serve

__all__ = ["build_server", "dispatch", "mcp_tools", "serve"]
