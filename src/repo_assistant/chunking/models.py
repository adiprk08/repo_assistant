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

    @property
    def embed_text(self) -> str:
        """What actually gets embedded: breadcrumb context + the code itself."""
        return f"{self.header}\n\n{self.text}" if self.header else self.text

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1
