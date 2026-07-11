"""The Chunk: the unit of retrieval.

A chunk's ``text`` is the exact source slice used for citation spans; ``header``
is a breadcrumb (``path > Class > signature``) prepended only for embedding, never
counted in the cited range (docs/adr/0002-parsing-and-chunking.md).
"""

from dataclasses import dataclass

from repo_assistant.ingestion.models import FileCategory


@dataclass(frozen=True, slots=True)
class Chunk:
    path: str
    language: str | None
    category: FileCategory
    text: str
    header: str
    start_line: int  # 1-indexed, inclusive
    end_line: int
    symbol: str | None  # qualified name of the primary enclosing symbol, if any
    index: int  # 0-based position within the file
    context: str | None = None  # optional LLM contextual description (docs/adr/0002)

    @property
    def embed_text(self) -> str:
        """What actually gets embedded: breadcrumb + optional contextual description
        + the code itself. The description situates the chunk in its file/repo to
        aid retrieval; it is never part of the cited ``text``."""
        parts = [p for p in (self.header, self.context, self.text) if p]
        return "\n\n".join(parts)

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1
