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
from repo_assistant.evaluation.models import (
    DatasetSpec,
    EvalReport,
    QuestionResult,
    QuestionSpec,
)
from repo_assistant.reasoning import answer_question
from repo_assistant.reasoning.service import Answer

logger = get_logger(__name__)

_CORRECTNESS_PASS = 4


async def _run_question(
    spec: QuestionSpec, answer: Answer, llm, expected: set[str]
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
    )


async def run_dataset(dataset: DatasetSpec, runtime: Runtime, *, limit: int = 12) -> EvalReport:
    resolved = await resolve_indexed_repo(runtime, dataset.repo_url)
    embedder, llm = runtime.embedder(), runtime.llm()
    report = EvalReport(dataset=resolved.url, repo_url=dataset.repo_url)

    for spec in dataset.questions:
        answer = await answer_question(
            str(resolved.repo_id),
            spec.question,
            embedder=embedder,
            vector_index=runtime.vector_index,
            llm=llm,
            limit=limit,
            filters={"commit": resolved.commit_sha},
        )
        result = await _run_question(spec, answer, llm, set(spec.expected_files))
        report.results.append(result)
        logger.info("eval question", id=spec.id, passed=result.passed)

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
