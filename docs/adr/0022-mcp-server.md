# ADR-0022: MCP server exposing the index tools to IDE agents

**Status:** Accepted (2026-07-12)

## Context

Repo Assistant's most reusable asset is its **read-only index tools** — hybrid
`search_code`, `get_symbol`, `read_span`, `graph_neighbors`, `list_dir` — over a
commit-pinned snapshot. The agent loop already uses them (ADR-0006). Exposing the
same tools over the **Model Context Protocol** lets any MCP client (Claude Desktop,
Cursor, other IDE agents) explore a repo the assistant has indexed, without our
generation loop or API in the path. This is the first Phase 6 extension.

## Decision

- **Wrap the existing tools, don't duplicate.** `mcp_server` builds a `ToolContext`
  and serves `reasoning.tools.TOOL_SCHEMAS` / `execute_tool` verbatim — the MCP
  layer only converts schemas to `mcp.types.Tool` and marshals results to
  `TextContent`. One source of truth for tool behavior; the agent loop and MCP
  clients get identical semantics.

- **Bound to one repo at launch.** `ra mcp <repo-url|id>` resolves the repo's
  **active snapshot** and serves tools scoped to it. An IDE points its MCP config
  at `ra mcp <repo>`; the tools read the index at that pinned commit only — never
  the live filesystem — so results are reproducible and the injection surface stays
  minimal (the same guarantee the agent loop relies on).

- **stdio transport.** MCP over stdio is the standard for local IDE integration.
  Critically, **stdout carries only JSON-RPC** — so the server routes all logging
  to **stderr** (`configure_logging(..., stream=sys.stderr)`). A single log line on
  stdout corrupts the protocol; this was caught and fixed during verification.

- **Read-only, no auth.** The tools cannot write, execute, or reach the network
  (ADR-0021), and stdio is a local trust boundary (the user launches the process),
  so no API-key layer is added. A future HTTP/SSE MCP transport would need auth.

- **Error mapping.** A tool-call error (unknown tool, bad arguments) raises so MCP
  reports `isError`; an ordinary "not found" is normal content — matching how the
  agent loop already distinguishes the two.

## Alternatives considered

- **A new MCP-specific tool set.** More freedom, but forks tool behavior from the
  agent loop and doubles the maintenance + test surface. Reusing `execute_tool`
  keeps them in lockstep.
- **HTTP/SSE MCP transport instead of stdio.** Better for a hosted, multi-client
  server, but needs auth and deployment; stdio is the right first cut for local IDE
  use. The transport is swappable later without touching tool logic.
- **Expose generation (ask-a-question) over MCP too.** The client *is* the LLM, so
  giving it raw retrieval tools is more composable and cheaper than nesting our
  generation loop inside its. Retrieval tools first; a grounded-answer tool could
  be added if demand appears.
- **A repo-selection tool (multi-repo server).** Deferred — one-repo-per-launch is
  the simplest IDE mental model and matches how editors scope to a project.

## Consequences

- `ra mcp <repo>` turns any indexed repo into an MCP tool source for IDE agents —
  a distinctive integration with no new tool code.
- Logging discipline (stdout = protocol only) is now a documented constraint for
  the stdio transport; `configure_logging` grew a `stream` parameter.
- New dependency: `mcp`.
- Verified end-to-end with a real MCP client over stdio (initialize → list tools →
  `list_dir` / `get_symbol`). Deferred: HTTP/SSE transport + auth, multi-repo
  selection, and a grounded-answer tool.
