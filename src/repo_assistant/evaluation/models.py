"""Types for the evaluation harness (docs/EVALUATION.md).

A dataset is a set of questions over one benchmark repo, each labeled with the
files that hold the evidence. Negative questions (empty ``expected_files``) test
honest refusal.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    id: str
    question: str
    category: str
    expected_files: list[str]

    @property
    def is_negative(self) -> bool:
        return not self.expected_files


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    repo_url: str
    questions: list[QuestionSpec]

    @classmethod
    def from_yaml(cls, path: Path) -> "DatasetSpec":
        data = yaml.safe_load(path.read_text("utf-8"))
        return cls(
            repo_url=data["repo_url"],
            questions=[
                QuestionSpec(
                    id=q["id"],
                    question=q["question"],
                    category=q["category"],
                    expected_files=list(q.get("expected_files") or []),
                )
                for q in data["questions"]
            ],
        )


@dataclass(frozen=True, slots=True)
class QuestionResult:
    id: str
    category: str
    is_negative: bool
    retrieval_hit: bool  # an expected file appeared in retrieved chunks
    refused: bool
    n_citations: int
    cited_expected_file: bool  # a citation points at an expected-evidence file
    correctness: int  # judge score 1-5 (0 for negatives, scored via refusal)
    groundedness: int  # judge score 1-5
    passed: bool  # overall per-question verdict
    rationale: str


@dataclass(slots=True)
class EvalReport:
    dataset: str
    repo_url: str
    results: list[QuestionResult] = field(default_factory=list)

    def _mean(self, values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    def summary(self) -> dict[str, float | int]:
        positives = [r for r in self.results if not r.is_negative]
        negatives = [r for r in self.results if r.is_negative]
        return {
            "questions": len(self.results),
            "retrieval_recall": self._mean([float(r.retrieval_hit) for r in positives]),
            "answer_correctness": self._mean([r.correctness for r in positives]),
            "groundedness": self._mean([r.groundedness for r in positives]),
            "citation_presence": self._mean([float(r.n_citations > 0) for r in positives]),
            "citation_file_precision": self._mean(
                [float(r.cited_expected_file) for r in positives]
            ),
            # For negatives, `passed` means the answer correctly indicated absence
            # (judged), not merely that the empty-retrieval refusal path fired.
            "negative_handled_rate": self._mean([float(r.passed) for r in negatives]),
            "pass_rate": self._mean([float(r.passed) for r in self.results]),
        }
