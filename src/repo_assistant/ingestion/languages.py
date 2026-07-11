"""File-type detection: map a path to a tree-sitter language and a category.

Phase 1 supports Python and TypeScript/JavaScript as parsed *code*; everything
else is classified so downstream chunkers know how to handle it (doc/config/text)
even when there is no grammar. Extending code support later is a matter of adding
entries here plus a symbol-query file (see docs/adr/0002-parsing-and-chunking.md).
"""

from repo_assistant.ingestion.models import FileCategory

# Extension -> tree-sitter language name (parsed as code). Lowercased, no dot.
# Tier 1: Python, TypeScript/JavaScript. Tier 2: Go, Java, Rust.
_CODE_EXTENSIONS: dict[str, str] = {
    "py": "python",
    "pyi": "python",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "tsx": "tsx",
    "go": "go",
    "java": "java",
    "rs": "rust",
}

_DOC_EXTENSIONS: frozenset[str] = frozenset({"md", "markdown", "mdx", "rst", "txt"})

_CONFIG_EXTENSIONS: frozenset[str] = frozenset({"json", "yaml", "yml", "toml", "ini", "cfg", "env"})

# Files with no extension that are conventionally documentation.
_DOC_BASENAMES: frozenset[str] = frozenset(
    {"readme", "license", "licence", "changelog", "contributing", "authors", "notice"}
)


def _extension(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return base.rsplit(".", 1)[-1].lower()


def detect_language(path: str) -> str | None:
    """Return the tree-sitter language name for a code file, or None."""
    return _CODE_EXTENSIONS.get(_extension(path))


def classify(path: str) -> tuple[str | None, FileCategory]:
    """Classify a repo-relative path into (language, category).

    ``language`` is non-None only for files we can parse as code.
    """
    ext = _extension(path)
    language = _CODE_EXTENSIONS.get(ext)
    if language is not None:
        return language, FileCategory.CODE
    if ext in _DOC_EXTENSIONS:
        return None, FileCategory.DOC
    if ext in _CONFIG_EXTENSIONS:
        return None, FileCategory.CONFIG

    basename = path.rsplit("/", 1)[-1].lower()
    stem = basename.split(".", 1)[0]
    if stem in _DOC_BASENAMES:
        return None, FileCategory.DOC

    return None, FileCategory.TEXT
