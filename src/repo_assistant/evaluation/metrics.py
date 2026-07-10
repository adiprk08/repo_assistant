"""Ranking metrics for retrieval evaluation (docs/EVALUATION.md §1).

Relevance is judged by evidence overlap: a retrieved chunk is relevant if it
overlaps a labeled span, or — when only file-level labels exist — if it comes
from an expected file. Metrics are computed over the ranked candidate list so
they capture ordering quality, not just presence.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Span:
    file: str
    start: int
    end: int

    def overlaps(self, path: str, start: int, end: int) -> bool:
        return self.file == path and not (end < self.start or start > self.end)


@dataclass(frozen=True, slots=True)
class RankedChunk:
    path: str
    start_line: int
    end_line: int


def _is_relevant(chunk: RankedChunk, expected_files: set[str], expected_spans: list[Span]) -> bool:
    if expected_spans:
        if any(s.overlaps(chunk.path, chunk.start_line, chunk.end_line) for s in expected_spans):
            return True
        # Span labels are authoritative when present; fall through to file match
        # only for files that carry no span label at all.
        span_files = {s.file for s in expected_spans}
        return chunk.path in expected_files and chunk.path not in span_files
    return chunk.path in expected_files


def _covered(
    ranked: list[RankedChunk], k: int, expected_files: set[str], expected_spans: list[Span]
) -> int:
    """Number of distinct expected-evidence items hit within the top-k."""
    top = ranked[:k]
    if expected_spans:
        return sum(
            any(s.overlaps(c.path, c.start_line, c.end_line) for c in top) for s in expected_spans
        )
    return sum(any(c.path == f for c in top) for f in expected_files)


def retrieval_metrics(
    ranked: list[RankedChunk],
    *,
    expected_files: set[str],
    expected_spans: list[Span],
    ks: tuple[int, ...] = (5, 10, 25),
    ndcg_k: int = 10,
) -> dict[str, float]:
    """Recall@k (coverage of labeled evidence), MRR, and nDCG@k with binary relevance."""
    total = len(expected_spans) if expected_spans else len(expected_files)
    metrics: dict[str, float] = {}
    for k in ks:
        metrics[f"recall@{k}"] = (
            (_covered(ranked, k, expected_files, expected_spans) / total) if total else 0.0
        )

    relevance = [_is_relevant(c, expected_files, expected_spans) for c in ranked]

    first = next((i for i, rel in enumerate(relevance) if rel), None)
    metrics["mrr"] = 1.0 / (first + 1) if first is not None else 0.0

    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(relevance[:ndcg_k]))
    ideal_hits = min(sum(relevance), ndcg_k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    metrics[f"ndcg@{ndcg_k}"] = (dcg / idcg) if idcg else 0.0
    return metrics
