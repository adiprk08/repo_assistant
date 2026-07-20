"""Domain types produced by the ingestion pipeline.

These are plain, storage-agnostic value objects. Persistence (Postgres/Qdrant)
happens later in the indexing stage; ingestion only reads a working tree and
emits descriptions of what it found.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class FileCategory(StrEnum):
    """How a scanned file will be treated downstream."""

    CODE = "code"  # has a tree-sitter grammar -> parsed + AST-chunked
    DOC = "doc"  # markdown/rst -> structure-aware chunking
    CONFIG = "config"  # json/yaml/toml -> key-path chunking
    TEXT = "text"  # plain text, no grammar -> fallback line-window chunking


class SkipReason(StrEnum):
    """Why a file present in the tree was excluded from indexing."""

    BINARY = "binary"
    TOO_LARGE = "too_large"
    VENDORED = "vendored"
    GENERATED = "generated"
    SECRET = "secret"
    EMPTY = "empty"
    IGNORED = "ignored"
    SYMLINK = "symlink"
    REPO_LIMIT = "repo_limit"


@dataclass(frozen=True, slots=True)
class Acquisition:
    """The result of cloning/checking out a repository at a specific commit."""

    url: str
    ref: str
    commit_sha: str
    root_path: str


@dataclass(frozen=True, slots=True)
class ScannedFile:
    """A file selected for indexing."""

    path: str  # POSIX-normalized, repo-root-relative
    language: str | None  # tree-sitter language name, or None for non-code
    category: FileCategory
    size_bytes: int
    content_hash: str  # sha256 of raw bytes


@dataclass(frozen=True, slots=True)
class SkippedFile:
    """A file present in the tree but excluded, with the reason why."""

    path: str
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Everything the scanner concluded about a working tree."""

    files: list[ScannedFile] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)

    @property
    def total_seen(self) -> int:
        return len(self.files) + len(self.skipped)
