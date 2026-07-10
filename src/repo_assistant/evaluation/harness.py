"""Evaluation harness: run golden datasets and record a baseline.

For each question: retrieve + answer (with verified citations), then score.
Positives pass when the right file was retrieved, the answer is judged correct,
and it carries at least one citation; negatives pass when the system honestly
refuses. Results are written as a JSON report under evals/reports/.
"""

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from repo_assistant.cli.runtime import Runtime, resolve_indexed_repo
from repo_assistant.core.logging import get_logger
from repo_assistant.evaluation.judge import judge_answer, judge_negative
from repo_assistant.evaluation.metrics import RankedChunk, Span, retrieval_metrics
from repo_assistant.evaluation.models import (
    DatasetSpec,
    EvalReport,
    QuestionResult,
    QuestionSpec,
)
from repo_assistant.reasoning import generate_answer
from repo_assistant.reasoning.service import Answer
from repo_assistant.retrieval import hybrid_retrieve

logger = get_logger(__name__)

_CORRECTNESS_PASS = 4
_RETRIEVE_K = 25  # rank depth for retrieval metrics; generation uses the top slice
_GENERATE_K = 12


def _ranking_metrics(spec: QuestionSpec, ranked: list[RankedChunk]) -> dict[str, float]:
    if spec.is_negative:
        return {}
    spans = [Span(file=s.file, start=s.start, end=s.end) for s in spec.expected_spans]
    return retrieval_metrics(ranked, expected_files=set(spec.expected_files), expected_spans=spans)


async def _run_question(
    spec: QuestionSpec, answer: Answer, ranking: dict[str, float], llm, expected: set[str]
) -> QuestionResult:
    retrieved_paths = {c.path for c in answer.retrieved}
    retrieval_hit = bool(expected & retrieved_paths)
    cited_expected = any(c.path in expected for c in answer.citations)

    if spec.is_negative:
        # A grounded "this isn't in the repo" answer is correct handling, not just
        # the empty-retrieval refusal path — so judge it rather than string-match.
        if answer.refused:
            handled, rationale = True, "refused (no retrieval)"
        else:
            handled, rationale = await judge_negative(
                llm, question=spec.question, answer=answer.text
            )
        return QuestionResult(
            id=spec.id,
            category=spec.category,
            is_negative=True,
            retrieval_hit=retrieval_hit,
            refused=answer.refused,
            n_citations=len(answer.citations),
            cited_expected_file=cited_expected,
            correctness=0,
            groundedness=0,
            passed=handled,
            rationale=rationale,
            ranking=ranking,
        )

    judgement = await judge_answer(
        llm, question=spec.question, answer=answer.text, expected_files=spec.expected_files
    )
    passed = (
        retrieval_hit and len(answer.citations) > 0 and judgement.correctness >= _CORRECTNESS_PASS
    )
    return QuestionResult(
        id=spec.id,
        category=spec.category,
        is_negative=False,
        retrieval_hit=retrieval_hit,
        refused=answer.refused,
        n_citations=len(answer.citations),
        cited_expected_file=cited_expected,
        correctness=judgement.correctness,
        groundedness=judgement.groundedness,
        passed=passed,
        rationale=judgement.rationale,
        ranking=ranking,
    )


def _retrieval_only_result(spec: QuestionSpec, ranking: dict[str, float]) -> QuestionResult:
    """A result carrying only retrieval metrics (no generation/judge — no LLM cost)."""
    passed = spec.is_negative or ranking.get("recall@10", 0.0) > 0.0
    return QuestionResult(
        id=spec.id,
        category=spec.category,
        is_negative=spec.is_negative,
        retrieval_hit=ranking.get("recall@25", 0.0) > 0.0,
        refused=False,
        n_citations=0,
        cited_expected_file=False,
        correctness=0,
        groundedness=0,
        passed=passed,
        rationale="retrieval-only",
        ranking=ranking,
    )


async def run_dataset(
    dataset: DatasetSpec,
    runtime: Runtime,
    *,
    use_symbols: bool = True,
    use_sparse: bool = True,
    use_rerank: bool = True,
    retrieval_only: bool = False,
) -> EvalReport:
    resolved = await resolve_indexed_repo(runtime, dataset.repo_url)
    embedder, llm = runtime.embedder(), runtime.llm()
    reranker = runtime.reranker()
    report = EvalReport(dataset=resolved.url, repo_url=dataset.repo_url)

    for spec in dataset.questions:
        # One retrieval at full depth: metrics score the ranked list, generation
        # uses the top slice (no double embedding of the query).
        retrieved = await hybrid_retrieve(
            str(resolved.repo_id),
            str(resolved.snapshot_id),
            spec.question,
            embedder=embedder,
            vector_index=runtime.vector_index,
            session_factory=runtime.session_factory,
            reranker=reranker,
            commit=resolved.commit_sha,
            limit=_RETRIEVE_K,
            dense_k=_RETRIEVE_K,
            rerank_k=_RETRIEVE_K,
            use_symbols=use_symbols,
            use_sparse=use_sparse,
            use_rerank=use_rerank,
        )
        ranked = [RankedChunk(c.path, c.start_line, c.end_line) for c in retrieved]
        ranking = _ranking_metrics(spec, ranked)

        if retrieval_only:
            result = _retrieval_only_result(spec, ranking)
        else:
            answer = await generate_answer(spec.question, retrieved[:_GENERATE_K], llm=llm)
            result = await _run_question(spec, answer, ranking, llm, set(spec.expected_files))
        report.results.append(result)
        logger.info("eval question", id=spec.id, passed=result.passed, **ranking)

    return report


def write_report(reports: list[EvalReport], config: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"eval-{stamp}.json"
    payload = {
        "timestamp": stamp,
        "config": config,
        "datasets": [
            {
                "dataset": r.dataset,
                "repo_url": r.repo_url,
                "summary": r.summary(),
                "results": [asdict(qr) for qr in r.results],
            }
            for r in reports
        ],
        "overall": _overall(reports),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _overall(reports: list[EvalReport]) -> dict[str, float | int]:
    all_results = [qr for r in reports for qr in r.results]
    if not all_results:
        return {}
    combined = EvalReport(dataset="overall", repo_url="", results=all_results)
    return combined.summary()


async def persist_report(reports: list[EvalReport], config: dict, runtime: Runtime) -> None:
    """Store the run and its per-question results (docs/EVALUATION.md §4)."""
    from repo_assistant.storage.models import EvalResult, EvalRun

    async with runtime.session_factory() as session:
        run = EvalRun(
            config=config,
            overall=_overall(reports),
            per_dataset={r.dataset: r.summary() for r in reports},
        )
        session.add(run)
        await session.flush()
        for report in reports:
            for qr in report.results:
                session.add(
                    EvalResult(
                        run_id=run.id,
                        dataset=report.dataset,
                        question_id=qr.id,
                        category=qr.category,
                        passed=qr.passed,
                        ranking=qr.ranking,
                        metrics={
                            "correctness": qr.correctness,
                            "groundedness": qr.groundedness,
                            "n_citations": qr.n_citations,
                            "retrieval_hit": qr.retrieval_hit,
                        },
                    )
                )
        await session.commit()


# Regression floors for the CI smoke gate — a change may not drop overall retrieval
# below these (set below the recorded dense+sparse+symbol baseline to absorb noise).
GATE_FLOORS: dict[str, float] = {"recall@10": 0.90, "mrr": 0.70, "ndcg@10": 0.70}


def gate_failures(overall: dict[str, float | int]) -> list[str]:
    """Return human-readable messages for any metric below its regression floor."""
    failures = []
    for metric, floor in GATE_FLOORS.items():
        value = float(overall.get(metric, 0.0))
        if value < floor:
            failures.append(f"{metric} {value:.3f} < floor {floor:.2f}")
    return failures
