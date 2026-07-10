"""Context assembly: overlap dedup and per-file capping."""

from dataclasses import replace

from repo_assistant.retrieval.assembly import assemble_context
from repo_assistant.retrieval.service import RetrievedChunk

_BASE = RetrievedChunk(
    chunk_id="c",
    path="a.py",
    text="x",
    start_line=1,
    end_line=10,
    commit="sha",
    symbol=None,
    language="python",
    score=1.0,
)


def _chunk(cid: str, path: str, start: int, end: int) -> RetrievedChunk:
    return replace(_BASE, chunk_id=cid, path=path, start_line=start, end_line=end)


def test_overlapping_spans_are_deduped() -> None:
    chunks = [
        _chunk("1", "core.py", 100, 120),
        _chunk("2", "core.py", 110, 130),  # overlaps #1 -> dropped
        _chunk("3", "core.py", 200, 210),  # disjoint -> kept
    ]
    kept = assemble_context(chunks, limit=10)
    assert [c.chunk_id for c in kept] == ["1", "3"]


def test_per_file_cap_limits_dominant_file() -> None:
    chunks = [_chunk(str(i), "big.py", i * 100, i * 100 + 10) for i in range(6)]
    chunks.append(_chunk("other", "util.py", 1, 5))
    kept = assemble_context(chunks, limit=10, max_per_file=4)
    assert sum(c.path == "big.py" for c in kept) == 4
    assert any(c.path == "util.py" for c in kept)


def test_rank_order_is_preserved() -> None:
    chunks = [_chunk("a", "x.py", 1, 5), _chunk("b", "y.py", 1, 5), _chunk("c", "z.py", 1, 5)]
    kept = assemble_context(chunks, limit=2)
    assert [c.chunk_id for c in kept] == ["a", "b"]


def test_limit_is_respected() -> None:
    chunks = [_chunk(str(i), f"f{i}.py", 1, 5) for i in range(20)]
    assert len(assemble_context(chunks, limit=5)) == 5


def test_empty_input() -> None:
    assert assemble_context([], limit=5) == []
