"""Context assembly: clean a ranked chunk list before it becomes LLM context.

Dedupes overlapping spans (the overlapping-citation noise seen on large files),
caps how many chunks any single file may contribute so the context stays diverse,
and preserves the incoming rank order (docs/ARCHITECTURE.md §5).
"""

from collections import defaultdict

from repo_assistant.retrieval.service import RetrievedChunk


def _overlaps(a: RetrievedChunk, b: RetrievedChunk) -> bool:
    return a.path == b.path and not (a.end_line < b.start_line or a.start_line > b.end_line)


def assemble_context(
    chunks: list[RetrievedChunk], *, limit: int = 12, max_per_file: int = 4
) -> list[RetrievedChunk]:
    """Return a deduped, per-file-capped slice of ``chunks`` in rank order."""
    kept: list[RetrievedChunk] = []
    per_file: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        if per_file[chunk.path] >= max_per_file:
            continue
        if any(_overlaps(chunk, existing) for existing in kept):
            continue
        kept.append(chunk)
        per_file[chunk.path] += 1
        if len(kept) >= limit:
            break
    return kept
