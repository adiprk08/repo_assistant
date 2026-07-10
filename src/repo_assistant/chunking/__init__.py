"""Chunking: turn a scanned/parsed file into retrieval units.

Dispatch by category: code -> AST-aware chunker (requires a ParsedFile),
docs -> heading-aware markdown, everything else -> line-window fallback.
"""

from repo_assistant.chunking.code import DEFAULT_BUDGET_TOKENS, chunk_code
from repo_assistant.chunking.models import Chunk
from repo_assistant.chunking.text import chunk_fallback, chunk_markdown
from repo_assistant.ingestion.models import FileCategory
from repo_assistant.parsing import parse_file
from repo_assistant.parsing.models import ParsedFile

__all__ = [
    "DEFAULT_BUDGET_TOKENS",
    "Chunk",
    "chunk_code",
    "chunk_fallback",
    "chunk_file",
    "chunk_markdown",
]


def chunk_file(
    path: str,
    source: bytes,
    language: str | None,
    category: FileCategory,
    budget: int = DEFAULT_BUDGET_TOKENS,
) -> list[Chunk]:
    """Chunk one file end to end, choosing the strategy from its classification.

    Code files are parsed here so callers that only need chunks don't have to
    manage the parse step; callers that also want symbols should call
    ``parse_file`` themselves and pass the result to ``chunk_code``.
    """
    if category is FileCategory.CODE and language is not None:
        parsed: ParsedFile = parse_file(path, language, source)
        return chunk_code(parsed, budget)

    text = source.decode("utf-8", "replace")
    if category is FileCategory.DOC and path.rsplit(".", 1)[-1].lower() in {
        "md",
        "markdown",
        "mdx",
    }:
        return chunk_markdown(path, text, budget)
    return chunk_fallback(path, text, category, budget)
