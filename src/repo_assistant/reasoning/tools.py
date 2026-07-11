"""Read-only index tools for the agentic reasoning loop (ADR-0006).

Five tools let the model explore the indexed repository at its pinned commit —
never the live filesystem, so answers stay reproducible and the injection surface
is minimal. Each tool returns a compact text payload for the model AND records the
source chunks it surfaced into a shared accumulator, which becomes the grounding
set for the final citation-verified answer.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.interfaces import Embedder, Reranker, VectorIndex
from repo_assistant.core.logging import get_logger
from repo_assistant.retrieval.service import RetrievedChunk, _to_chunk, hybrid_retrieve

logger = get_logger(__name__)

_SNIPPET_CHARS = 200
_SYMBOL_LIMIT = 10
_NEIGHBOR_LIMIT = 20
_LISTING_LIMIT = 200
# read_span feeds full source back into the (token-metered) loop; cap it so a
# single call can't blow up context cost. The chunks are still recorded in full
# for the final grounding set.
_READ_SPAN_MAX_CHARS = 2400


@dataclass(slots=True)
class ToolContext:
    """Dependencies and the grounding accumulator for one agent session."""

    repo_id: str
    snapshot_id: str
    commit: str
    embedder: Embedder
    vector_index: VectorIndex
    session_factory: async_sessionmaker[AsyncSession]
    reranker: Reranker | None = None
    gathered: dict[str, RetrievedChunk] = field(default_factory=dict)

    def record(self, chunks: list[RetrievedChunk]) -> None:
        for chunk in chunks:
            self.gathered.setdefault(chunk.chunk_id, chunk)

    def grounding_chunks(self) -> list[RetrievedChunk]:
        return list(self.gathered.values())


async def _materialize(ctx: ToolContext, chunk_ids: list[str]) -> list[RetrievedChunk]:
    if not chunk_ids:
        return []
    results = await ctx.vector_index.fetch(repo_id=ctx.repo_id, ids=chunk_ids)
    return [_to_chunk(r) for r in results]


def _fmt_chunk(chunk: RetrievedChunk) -> str:
    head = f"{chunk.path}:{chunk.start_line}-{chunk.end_line}"
    if chunk.symbol:
        head += f" [{chunk.symbol}]"
    snippet = chunk.text[:_SNIPPET_CHARS].replace("\n", " ").strip()
    return f"- {head}\n  {snippet}"


async def search_code(ctx: ToolContext, *, query: str, k: int = 8) -> str:
    """Hybrid retrieval over the repo; records and lists the top chunks."""
    chunks = await hybrid_retrieve(
        ctx.repo_id,
        ctx.snapshot_id,
        query,
        embedder=ctx.embedder,
        vector_index=ctx.vector_index,
        session_factory=ctx.session_factory,
        reranker=ctx.reranker,
        commit=ctx.commit,
        limit=k,
        use_graph=False,
        use_rerank=False,
    )
    ctx.record(chunks)
    if not chunks:
        return "No matching code found."
    return "\n".join(_fmt_chunk(c) for c in chunks)


_SYMBOL_QUERY = text(
    """
    SELECT s.qualified_name, s.kind, s.file_path, s.start_line, s.end_line,
           s.signature, s.docstring,
           (SELECT c.id::text FROM chunks c
             WHERE c.snapshot_id = s.snapshot_id AND c.file_path = s.file_path
               AND c.start_line <= s.start_line AND c.end_line >= s.start_line
             LIMIT 1) AS chunk_id
    FROM symbols s
    WHERE s.snapshot_id = :sid
      AND (lower(s.name) = lower(:name) OR s.qualified_name ILIKE :like)
    ORDER BY (lower(s.name) = lower(:name)) DESC, length(s.qualified_name)
    LIMIT :limit
    """
)


async def get_symbol(ctx: ToolContext, *, name: str) -> str:
    """Look up a symbol's definition(s) by name or qualified name."""
    async with ctx.session_factory() as session:
        rows = (
            await session.execute(
                _SYMBOL_QUERY,
                {"sid": ctx.snapshot_id, "name": name, "like": f"%{name}%", "limit": _SYMBOL_LIMIT},
            )
        ).all()
    if not rows:
        return f"No symbol named {name!r} found."
    ctx.record(await _materialize(ctx, [r.chunk_id for r in rows if r.chunk_id]))
    lines: list[str] = []
    for r in rows:
        entry = f"- {r.qualified_name} ({r.kind}) at {r.file_path}:{r.start_line}-{r.end_line}"
        if r.signature:
            entry += f"\n  {r.signature.strip()}"
        if r.docstring:
            entry += f"\n  doc: {r.docstring.strip()[:_SNIPPET_CHARS]}"
        lines.append(entry)
    return "\n".join(lines)


_SPAN_QUERY = text(
    """
    SELECT id::text AS chunk_id FROM chunks
    WHERE snapshot_id = :sid AND file_path = :path
      AND start_line <= :end AND end_line >= :start
    ORDER BY start_line
    """
)


async def read_span(ctx: ToolContext, *, path: str, start: int, end: int) -> str:
    """Return the source of ``path`` between lines ``start`` and ``end``."""
    async with ctx.session_factory() as session:
        rows = (
            await session.execute(
                _SPAN_QUERY, {"sid": ctx.snapshot_id, "path": path, "start": start, "end": end}
            )
        ).all()
    chunks = await _materialize(ctx, [r.chunk_id for r in rows])
    if not chunks:
        return f"No indexed source for {path}:{start}-{end}."
    ctx.record(chunks)  # full chunks kept for grounding, even if the reply is truncated
    chunks.sort(key=lambda c: c.start_line)
    body = "\n\n".join(f"# {c.path}:{c.start_line}-{c.end_line}\n{c.text}" for c in chunks)
    if len(body) > _READ_SPAN_MAX_CHARS:
        body = body[:_READ_SPAN_MAX_CHARS] + "\n… (truncated; span already recorded)"
    return body


_NEIGHBOR_QUERY = text(
    """
    SELECT dst AS neighbor, kind, confidence, 'calls/contains ->' AS direction
    FROM edges WHERE snapshot_id = :sid AND src ILIKE :sym
    UNION ALL
    SELECT src AS neighbor, kind, confidence, '<- called/contained by' AS direction
    FROM edges WHERE snapshot_id = :sid AND dst ILIKE :sym
    ORDER BY confidence DESC
    LIMIT :limit
    """
)

_NEIGHBOR_CHUNKS = text(
    """
    SELECT DISTINCT (SELECT c.id::text FROM chunks c
        WHERE c.snapshot_id = s.snapshot_id AND c.file_path = s.file_path
          AND c.start_line <= s.start_line AND c.end_line >= s.start_line LIMIT 1) AS chunk_id
    FROM symbols s
    WHERE s.snapshot_id = :sid AND s.qualified_name = ANY(:names)
    """
)


async def graph_neighbors(ctx: ToolContext, *, symbol: str, direction: str = "both") -> str:
    """List callers/callees and container/contained neighbors of ``symbol``."""
    async with ctx.session_factory() as session:
        rows = (
            await session.execute(
                _NEIGHBOR_QUERY,
                {"sid": ctx.snapshot_id, "sym": symbol, "limit": _NEIGHBOR_LIMIT},
            )
        ).all()
        if not rows:
            return f"No graph neighbors found for {symbol!r}."
        names = list({r.neighbor for r in rows})
        chunk_rows = (
            await session.execute(_NEIGHBOR_CHUNKS, {"sid": ctx.snapshot_id, "names": names})
        ).all()
    ctx.record(await _materialize(ctx, [r.chunk_id for r in chunk_rows if r.chunk_id]))
    return "\n".join(
        f"- {r.direction} {r.neighbor} ({r.kind}, confidence {r.confidence:.1f})" for r in rows
    )


_LISTING_QUERY = text(
    """
    SELECT DISTINCT file_path FROM chunks
    WHERE snapshot_id = :sid AND file_path LIKE :prefix
    ORDER BY file_path LIMIT :limit
    """
)


async def list_dir(ctx: ToolContext, *, prefix: str = "") -> str:
    """List files and immediate subdirectories under ``prefix`` (navigation only)."""
    prefix = prefix.strip("/")
    like = f"{prefix}/%" if prefix else "%"
    async with ctx.session_factory() as session:
        rows = (
            await session.execute(
                _LISTING_QUERY, {"sid": ctx.snapshot_id, "prefix": like, "limit": _LISTING_LIMIT}
            )
        ).all()
    entries: set[str] = set()
    depth = len(prefix.split("/")) if prefix else 0
    for r in rows:
        parts = r.file_path.split("/")
        if len(parts) > depth + 1:
            entries.add(parts[depth] + "/")
        else:
            entries.add(parts[depth])
    if not entries:
        return f"No files under {prefix or '/'}."
    return "\n".join(sorted(entries))


# Anthropic tool schemas (name + JSON input schema) exposed to the model.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_code",
        "description": "Hybrid semantic + keyword + symbol search over the repository. "
        "Use to find code relevant to a concept or identifier.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "k": {"type": "integer", "description": "Number of results (default 8)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_symbol",
        "description": "Look up where a function/class/method is defined, with its "
        "signature and docstring, by name or qualified name.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "read_span",
        "description": "Read the source of a file between two line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Repo-relative file path."},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["path", "start", "end"],
        },
    },
    {
        "name": "graph_neighbors",
        "description": "List the callers, callees, and container/contained symbols of a "
        "given symbol (qualified name) — for following a call chain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Qualified symbol name."},
                "direction": {"type": "string", "enum": ["both", "callers", "callees"]},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and subdirectories under a path prefix.",
        "input_schema": {
            "type": "object",
            "properties": {"prefix": {"type": "string"}},
            "required": [],
        },
    },
]

_DISPATCH = {
    "search_code": search_code,
    "get_symbol": get_symbol,
    "read_span": read_span,
    "graph_neighbors": graph_neighbors,
    "list_dir": list_dir,
}


async def execute_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Run a tool by name; returns (content, is_error). Unknown tools/bad args are
    reported back to the model as an error result rather than raising."""
    fn = _DISPATCH.get(name)
    if fn is None:
        return f"Unknown tool: {name!r}.", True
    try:
        return await fn(ctx, **arguments), False
    except TypeError as exc:
        return f"Invalid arguments for {name}: {exc}", True
