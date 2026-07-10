"""Chunking behavior and the citation-critical line-span invariant."""

from repo_assistant.chunking import chunk_file
from repo_assistant.chunking.code import chunk_code
from repo_assistant.chunking.text import chunk_fallback, chunk_markdown
from repo_assistant.ingestion.models import FileCategory
from repo_assistant.parsing import parse_file

PY_SOURCE = (
    b"import os\n\n"
    b"def alpha(x):\n"
    b'    """Alpha."""\n'
    b"    return x + 1\n\n"
    b"class Service:\n"
    b"    def refresh(self, token):\n"
    b"        return token\n\n"
    b"    def revoke(self, token):\n"
    b"        return None\n"
)


def _assert_line_spans_roundtrip(text_source: str, chunks) -> None:
    """Every chunk's [start_line, end_line] must reproduce chunk.text exactly.

    This is the invariant the citation verifier relies on (docs/adr/0007)."""
    lines = text_source.split("\n")
    for c in chunks:
        assert c.start_line >= 1
        assert c.end_line >= c.start_line
        reconstructed = "\n".join(lines[c.start_line - 1 : c.end_line])
        assert reconstructed == c.text


def test_small_file_is_a_single_chunk() -> None:
    parsed = parse_file("svc.py", "python", PY_SOURCE)
    chunks = chunk_code(parsed)
    assert len(chunks) == 1
    _assert_line_spans_roundtrip(PY_SOURCE.decode(), chunks)


def test_small_budget_splits_at_symbol_boundaries_with_breadcrumbs() -> None:
    parsed = parse_file("svc.py", "python", PY_SOURCE)
    chunks = chunk_code(parsed, budget=8)

    _assert_line_spans_roundtrip(PY_SOURCE.decode(), chunks)
    symbols = {c.symbol for c in chunks}
    assert "Service.refresh" in symbols
    assert "Service.revoke" in symbols
    for c in chunks:
        if c.symbol:
            assert c.header.startswith(f"svc.py > {c.symbol}")


def test_chunk_embed_text_includes_header() -> None:
    parsed = parse_file("svc.py", "python", PY_SOURCE)
    (chunk,) = chunk_code(parsed)
    assert chunk.text in chunk.embed_text
    assert chunk.header in chunk.embed_text


def test_markdown_chunks_carry_heading_breadcrumb() -> None:
    md = "# Title\n\nIntro.\n\n## Section A\n\nBody A.\n\n## Section B\n\nBody B.\n"
    chunks = chunk_markdown("docs/guide.md", md)
    _assert_line_spans_roundtrip(md, chunks)
    headers = " ".join(c.header for c in chunks)
    assert "Title" in headers
    assert "Section A" in headers
    assert all(c.category is FileCategory.DOC for c in chunks)


def test_fallback_windows_cover_all_lines() -> None:
    source = "\n".join(f"line {i}" for i in range(1, 201))
    chunks = chunk_fallback("data.txt", source, FileCategory.TEXT)
    assert chunks[0].start_line == 1
    # Windows overlap, so the union of covered lines must reach the last line.
    assert max(c.end_line for c in chunks) >= 200


def test_chunk_file_dispatches_by_category() -> None:
    code_chunks = chunk_file("a.py", b"def f():\n    return 1\n", "python", FileCategory.CODE)
    assert code_chunks[0].category is FileCategory.CODE

    doc_chunks = chunk_file("r.md", b"# H\n\ntext\n", None, FileCategory.DOC)
    assert doc_chunks[0].category is FileCategory.DOC

    cfg_chunks = chunk_file("c.toml", b"[a]\nx = 1\n", None, FileCategory.CONFIG)
    assert cfg_chunks[0].category is FileCategory.CONFIG
