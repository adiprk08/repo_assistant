"""Types for the evaluation harness (docs/EVALUATION.md).

A dataset is a set of questions over one benchmark repo, each labeled with the
files that hold the evidence. Negative questions (empty ``expected_files``) test
honest refusal.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class SpanLabel:
    file: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class QuestionSpec:
    id: str
    question: str
    category: str
    expected_files: list[str]
    expected_spans: list[SpanLabel] = field(default_factory=list)

    @property
    def is_negative(self) -> bool:
        return not self.expected_files and not self.expected_spans


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
                    expected_spans=[
                        SpanLabel(file=s["file"], start=s["start"], end=s["end"])
                        for s in (q.get("expected_spans") or [])
                    ],
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
    ranking: dict[str, float] = field(default_factory=dict)  # recall@k, mrr, ndcg
    # Routing metadata (agentic eval mode only; empty path means not routed).
    path: str = ""  # "fast" | "agent"
    n_tool_calls: int = 0
    forced_stop: bool = False  # agent hit the tool-call budget
    router_correct: bool = False  # router path matched the category's expected path


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
        summary: dict[str, float | int] = {
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
        # Routing metrics, present only when the run went through the router (agentic).
        routed = [r for r in self.results if r.path]
        if routed:
            summary["router_path_accuracy"] = self._mean([float(r.router_correct) for r in routed])
            summary["agent_path_share"] = self._mean([float(r.path == "agent") for r in routed])
            agent_path = [r for r in routed if r.path == "agent"]
            if agent_path:
                summary["budget_ok_rate"] = self._mean(
                    [float(not r.forced_stop) for r in agent_path]
                )
                summary["avg_tool_calls"] = self._mean([r.n_tool_calls for r in agent_path])
        # Span/file-level ranking metrics, averaged over positives that carry them.
        ranking_keys = {key for r in positives for key in r.ranking}
        for key in sorted(ranking_keys):
            summary[key] = self._mean([r.ranking[key] for r in positives if key in r.ranking])
        return summary

    def by_category(self) -> dict[str, dict[str, float | int]]:
        """Ranking metrics and pass rate per question category (docs/EVALUATION.md §2).

        Category-level numbers are what channel ablations are judged on — an
        overall average hides a channel that only helps (or only hurts) one
        question shape, e.g. the graph channel on `trace` questions.
        """
        categories: dict[str, dict[str, float | int]] = {}
        for category in sorted({r.category for r in self.results}):
            members = [r for r in self.results if r.category == category]
            positives = [r for r in members if not r.is_negative]
            entry: dict[str, float | int] = {
                "questions": len(members),
                "pass_rate": self._mean([float(r.passed) for r in members]),
            }
            ranking_keys = {key for r in positives for key in r.ranking}
            for key in sorted(ranking_keys):
                entry[key] = self._mean([r.ranking[key] for r in positives if key in r.ranking])
            categories[category] = entry
        return categories
