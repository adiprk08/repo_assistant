"""AST-aware code chunking.

A chunk is a contiguous run of complete top-level AST nodes whose combined size
fits a token budget; nodes larger than the budget are split by recursing into
their children, and any indivisible leaf that is still too large is split on line
boundaries. Every chunk is tagged with a breadcrumb header derived from the
enclosing symbols so embeddings carry structural context (docs/adr/0002).
"""

from tree_sitter import Node

from repo_assistant.chunking.models import Chunk
from repo_assistant.core.tokens import estimate_tokens
from repo_assistant.ingestion.models import FileCategory
from repo_assistant.parsing.models import ParsedFile, Symbol

DEFAULT_BUDGET_TOKENS = 1200
# Chunks smaller than this are merged forward where possible to avoid a long tail
# of trivially small fragments (single-line statements, lone comments).
_MIN_CHUNK_TOKENS = 40


def _span_text(source: bytes, start: int, end: int) -> str:
    return source[start:end].decode("utf-8", "replace")


def _line_split(start: int, end: int, source: bytes, budget: int) -> list[tuple[int, int]]:
    """Split an indivisible byte range on line boundaries to fit the budget."""
    text = source[start:end]
    spans: list[tuple[int, int]] = []
    line_start = start
    cursor = start
    budget_bytes = budget * 4  # estimate_tokens uses ~4 chars/token
    for offset, byte in enumerate(text):
        if byte == 0x0A:  # newline
            line_end = start + offset + 1
            if line_end - line_start >= budget_bytes and line_start < line_end:
                spans.append((line_start, line_end))
                line_start = line_end
            cursor = line_end
    if line_start < end:
        spans.append((line_start, end))
    elif cursor < end:
        spans.append((cursor, end))
    return spans or [(start, end)]


def _split_node(node: Node, source: bytes, budget: int) -> list[tuple[int, int]]:
    """Partition ``node``'s children into byte spans, each within ``budget``.

    Adjacent small children are greedily merged; an oversized child is recursed
    into (or line-split if it has no splittable children).
    """
    spans: list[tuple[int, int]] = []
    group_start: int | None = None
    group_end = 0
    group_tokens = 0

    for child in node.children:
        child_tokens = estimate_tokens(_span_text(source, child.start_byte, child.end_byte))

        if child_tokens > budget:
            if group_start is not None:
                spans.append((group_start, group_end))
                group_start, group_tokens = None, 0
            sub = _split_node(child, source, budget)
            spans.extend(
                sub if sub else _line_split(child.start_byte, child.end_byte, source, budget)
            )
        elif group_start is None:
            group_start, group_end, group_tokens = child.start_byte, child.end_byte, child_tokens
        elif group_tokens + child_tokens > budget:
            spans.append((group_start, group_end))
            group_start, group_end, group_tokens = child.start_byte, child.end_byte, child_tokens
        else:
            group_end, group_tokens = child.end_byte, group_tokens + child_tokens

    if group_start is not None:
        spans.append((group_start, group_end))
    return spans


def _merge_small_adjacent(
    spans: list[tuple[int, int]], source: bytes, budget: int
) -> list[tuple[int, int]]:
    """Fold tiny spans into their following neighbor when the union still fits."""
    if not spans:
        return spans
    merged: list[tuple[int, int]] = [spans[0]]
    for start, end in spans[1:]:
        prev_start, prev_end = merged[-1]
        prev_tokens = estimate_tokens(_span_text(source, prev_start, prev_end))
        union_tokens = estimate_tokens(_span_text(source, prev_start, end))
        if prev_tokens < _MIN_CHUNK_TOKENS and union_tokens <= budget:
            merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def _breadcrumb(path: str, symbols: list[Symbol], start_byte: int) -> tuple[str, str | None]:
    """Return (header, primary_symbol_qualified_name) for a chunk starting at ``start_byte``."""
    enclosing = [s for s in symbols if s.start_byte <= start_byte < s.end_byte]
    if not enclosing:
        return path, None
    primary = max(enclosing, key=lambda s: s.start_byte)
    header = f"{path} > {primary.qualified_name} > {primary.signature}"
    return header, primary.qualified_name


def _line_of(source: bytes, byte_offset: int) -> int:
    return source.count(b"\n", 0, byte_offset) + 1


def _snap_to_lines(source: bytes, start: int, end: int) -> tuple[int, int]:
    """Expand a byte span outward to whole-line boundaries.

    AST node spans begin at the node token, excluding a line's leading
    indentation; snapping makes ``chunk.text`` equal the exact source lines it
    covers, which keeps citations verifiable and embeds complete, readable code.
    """
    line_start = source.rfind(b"\n", 0, start) + 1  # 0 if not found -> start of file
    newline_at = source.find(b"\n", max(start, end - 1))
    line_end = len(source) if newline_at == -1 else newline_at
    return line_start, line_end


def chunk_code(parsed: ParsedFile, budget: int = DEFAULT_BUDGET_TOKENS) -> list[Chunk]:
    """Chunk a parsed code file into retrieval units."""
    spans = _split_node(parsed.root, parsed.source, budget)
    spans = _merge_small_adjacent(spans, parsed.source, budget)

    chunks: list[Chunk] = []
    for index, (raw_start, raw_end) in enumerate(spans):
        start, end = _snap_to_lines(parsed.source, raw_start, raw_end)
        text = _span_text(parsed.source, start, end)
        if not text.strip():
            continue
        header, symbol = _breadcrumb(parsed.path, parsed.symbols, raw_start)
        chunks.append(
            Chunk(
                path=parsed.path,
                language=parsed.language,
                category=FileCategory.CODE,
                text=text,
                header=header,
                start_line=_line_of(parsed.source, start),
                end_line=_line_of(parsed.source, max(start, end - 1)),
                symbol=symbol,
                index=index,
            )
        )
    return chunks
