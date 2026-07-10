"""Evaluation harness: golden datasets, LLM judge, metrics, reports."""

from repo_assistant.evaluation.harness import run_dataset, write_report
from repo_assistant.evaluation.models import DatasetSpec, EvalReport, QuestionResult, QuestionSpec

__all__ = [
    "DatasetSpec",
    "EvalReport",
    "QuestionResult",
    "QuestionSpec",
    "run_dataset",
    "write_report",
]
