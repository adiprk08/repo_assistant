"""Types produced by the parsing stage.

A ``ParsedFile`` carries the concrete syntax tree alongside the extracted symbols
so the chunker can align chunk boundaries to real AST nodes and compute breadcrumb
headers without re-parsing (docs/adr/0002-parsing-and-chunking.md).
"""

from dataclasses import dataclass, field
from enum import StrEnum

from tree_sitter import Node


class SymbolKind(StrEnum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    TYPE = "type"
    ENUM = "enum"
    STRUCT = "struct"  # Go/Rust aggregate types
    TRAIT = "trait"  # Rust traits (interface-like)
    MODULE = "module"  # Rust modules


@dataclass(frozen=True, slots=True)
class Symbol:
    """A named definition extracted from a source file.

    Line numbers are 1-indexed and inclusive so they map directly onto the
    citations users see (``path:start-end``). Byte offsets are retained for
    precise, whitespace-exact slicing by the chunker.
    """

    name: str
    qualified_name: str  # e.g. "SessionManager.refresh"
    kind: SymbolKind
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    signature: str  # first meaningful line, e.g. "def refresh(self, token):"
    docstring: str | None = None
    parent: str | None = None  # qualified name of the enclosing symbol


@dataclass(frozen=True, slots=True)
class Import:
    """An import/require statement (raw text + line span). Structured resolution
    into graph edges happens in Phase 3."""

    text: str
    start_line: int
    end_line: int


@dataclass(slots=True)
class ParsedFile:
    path: str
    language: str
    source: bytes
    root: Node
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
