"""Retrieval ranking metrics: recall@k, MRR, nDCG."""

import math

from repo_assistant.evaluation.metrics import RankedChunk, Span, retrieval_metrics


def _chunks(*specs: tuple[str, int, int]) -> list[RankedChunk]:
    return [RankedChunk(path=p, start_line=s, end_line=e) for p, s, e in specs]


def test_file_level_recall_and_mrr() -> None:
    ranked = _chunks(("other.py", 1, 5), ("core.py", 10, 20), ("x.py", 1, 2))
    m = retrieval_metrics(ranked, expected_files={"core.py"}, expected_spans=[], ks=(1, 5))
    assert m["recall@1"] == 0.0  # core.py not in top-1
    assert m["recall@5"] == 1.0  # covered within top-5
    assert m["mrr"] == 0.5  # first relevant at rank 2


def test_span_overlap_is_relevance_when_spans_labeled() -> None:
    ranked = _chunks(("core.py", 1, 8), ("core.py", 100, 120))
    spans = [Span(file="core.py", start=105, end=110)]
    m = retrieval_metrics(ranked, expected_files={"core.py"}, expected_spans=spans, ks=(1, 2))
    # Rank 1 is the same file but wrong span -> not relevant; rank 2 overlaps.
    assert m["recall@1"] == 0.0
    assert m["recall@2"] == 1.0
    assert m["mrr"] == 0.5


def test_perfect_ranking_scores_one() -> None:
    ranked = _chunks(("core.py", 100, 120))
    spans = [Span(file="core.py", start=105, end=110)]
    m = retrieval_metrics(ranked, expected_files={"core.py"}, expected_spans=spans)
    assert m["recall@5"] == 1.0
    assert m["mrr"] == 1.0
    assert m["ndcg@10"] == 1.0


def test_ndcg_rewards_earlier_relevance() -> None:
    relevant_first = _chunks(("core.py", 10, 20), ("a.py", 1, 2), ("b.py", 1, 2))
    relevant_third = _chunks(("a.py", 1, 2), ("b.py", 1, 2), ("core.py", 10, 20))
    early = retrieval_metrics(relevant_first, expected_files={"core.py"}, expected_spans=[])
    late = retrieval_metrics(relevant_third, expected_files={"core.py"}, expected_spans=[])
    assert early["ndcg@10"] == 1.0
    assert late["ndcg@10"] < 1.0
    assert late["ndcg@10"] == 1.0 / math.log2(4)  # relevance at rank 3


def test_no_relevant_results_scores_zero() -> None:
    ranked = _chunks(("a.py", 1, 2), ("b.py", 3, 4))
    m = retrieval_metrics(ranked, expected_files={"core.py"}, expected_spans=[])
    assert m["recall@5"] == 0.0
    assert m["mrr"] == 0.0
    assert m["ndcg@10"] == 0.0


def test_multi_span_recall_is_fractional() -> None:
    ranked = _chunks(("core.py", 10, 20))
    spans = [Span("core.py", 10, 20), Span("util.py", 5, 9)]
    m = retrieval_metrics(ranked, expected_files={"core.py", "util.py"}, expected_spans=spans)
    assert m["recall@5"] == 0.5  # one of two labeled spans covered
