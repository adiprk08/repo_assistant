"""Non-code chunkers: heading-aware markdown and a line-window fallback.

These handle docs, config, and any file without a tree-sitter grammar so every
selected file is retrievable, even if it carries no symbols (docs/adr/0002).
"""

import re

from repo_assistant.chunking.models import Chunk
from repo_assistant.core.tokens import estimate_tokens
from repo_assistant.ingestion.models import FileCategory

DEFAULT_BUDGET_TOKENS = 1200
_FALLBACK_WINDOW_LINES = 60
_FALLBACK_OVERLAP_LINES = 10

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _emit(
    path: str,
    category: FileCategory,
    lines: list[str],
    start_line: int,
    header: str,
    index: int,
) -> Chunk | None:
    """Build a chunk from ``lines``, trimming blank edges and keeping the line
    range consistent with the trimmed text (the citation roundtrip invariant)."""
    leading = 0
    while leading < len(lines) and not lines[leading].strip():
        leading += 1
    trailing = len(lines)
    while trailing > leading and not lines[trailing - 1].strip():
        trailing -= 1
    trimmed = lines[leading:trailing]
    if not trimmed:
        return None
    return Chunk(
        path=path,
        language=None,
        category=category,
        text="\n".join(trimmed),
        header=header,
        start_line=start_line + leading,
        end_line=start_line + trailing - 1,
        symbol=None,
        index=index,
    )


def chunk_markdown(path: str, source: str, budget: int = DEFAULT_BUDGET_TOKENS) -> list[Chunk]:
    """Split markdown at heading boundaries, keeping a breadcrumb of the heading path."""
    lines = source.split("\n")
    chunks: list[Chunk] = []
    heading_stack: list[tuple[int, str]] = []  # (level, title)
    section: list[str] = []
    section_start = 1

    def breadcrumb() -> str:
        trail = " > ".join(title for _, title in heading_stack)
        return f"{path} > {trail}" if trail else path

    def flush(next_start: int) -> None:
        nonlocal section, section_start
        if section:
            chunk = _emit(path, FileCategory.DOC, section, section_start, breadcrumb(), len(chunks))
            if chunk is not None:
                _append_budgeted(chunks, chunk, budget)
        section = []
        section_start = next_start

    for i, line in enumerate(lines, start=1):
        match = _HEADING.match(line)
        if match:
            flush(i)
            level = len(match.group(1))
            title = match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            section = [line]
            section_start = i
        else:
            section.append(line)
    flush(len(lines) + 1)
    return _reindex(chunks)


def chunk_fallback(
    path: str, source: str, category: FileCategory, budget: int = DEFAULT_BUDGET_TOKENS
) -> list[Chunk]:
    """Overlapping line-window chunker for config/text/unsupported files."""
    lines = source.split("\n")
    chunks: list[Chunk] = []
    step = _FALLBACK_WINDOW_LINES - _FALLBACK_OVERLAP_LINES
    for start in range(0, len(lines), step):
        window = lines[start : start + _FALLBACK_WINDOW_LINES]
        chunk = _emit(path, category, window, start + 1, path, len(chunks))
        if chunk is not None:
            chunks.append(chunk)
        if start + _FALLBACK_WINDOW_LINES >= len(lines):
            break
    return chunks


def _append_budgeted(chunks: list[Chunk], chunk: Chunk, budget: int) -> None:
    """Add a chunk, line-splitting it first if it exceeds the token budget."""
    if estimate_tokens(chunk.text) <= budget:
        chunks.append(chunk)
        return
    lines = chunk.text.split("\n")
    window = max(1, budget * 4 // 80)  # ~80 chars/line estimate
    for start in range(0, len(lines), window):
        piece = lines[start : start + window]
        sub = _emit(chunk.path, chunk.category, piece, chunk.start_line + start, chunk.header, 0)
        if sub is not None:
            chunks.append(sub)


def _reindex(chunks: list[Chunk]) -> list[Chunk]:
    from dataclasses import replace

    return [replace(c, index=i) for i, c in enumerate(chunks)]
