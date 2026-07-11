"""Evaluation dataset loading and metric aggregation (no network)."""

from pathlib import Path

from repo_assistant.evaluation.models import DatasetSpec, EvalReport, QuestionResult


def _result(**overrides) -> QuestionResult:
    base = dict(
        id="q1",
        category="explain",
        is_negative=False,
        retrieval_hit=True,
        refused=False,
        n_citations=2,
        cited_expected_file=True,
        correctness=5,
        groundedness=5,
        passed=True,
        rationale="",
    )
    base.update(overrides)
    return QuestionResult(**base)  # type: ignore[arg-type]


def test_dataset_loads_from_yaml(tmp_path: Path) -> None:
    yaml_text = (
        "repo_url: https://github.com/x/y\n"
        "questions:\n"
        "  - id: q1\n"
        "    question: How does it work?\n"
        "    category: explain\n"
        "    expected_files: [index.js]\n"
        "  - id: q2\n"
        "    question: Is there a thing that is absent?\n"
        "    category: negative\n"
        "    expected_files: []\n"
    )
    path = tmp_path / "d.yaml"
    path.write_text(yaml_text, encoding="utf-8")

    dataset = DatasetSpec.from_yaml(path)
    assert dataset.repo_url == "https://github.com/x/y"
    assert len(dataset.questions) == 2
    assert dataset.questions[0].expected_files == ["index.js"]
    assert dataset.questions[0].is_negative is False
    assert dataset.questions[1].is_negative is True


def test_summary_aggregates_positive_and_negative_metrics() -> None:
    report = EvalReport(
        dataset="d",
        repo_url="u",
        results=[
            _result(id="p1", retrieval_hit=True, correctness=5, n_citations=2, passed=True),
            _result(id="p2", retrieval_hit=False, correctness=2, n_citations=0, passed=False),
            _result(
                id="n1",
                is_negative=True,
                refused=True,
                n_citations=0,
                correctness=0,
                groundedness=0,
                passed=True,
            ),
        ],
    )
    summary = report.summary()

    assert summary["questions"] == 3
    assert summary["retrieval_recall"] == 0.5  # 1 of 2 positives
    assert summary["answer_correctness"] == 3.5  # mean(5, 2)
    assert summary["citation_presence"] == 0.5  # 1 of 2 positives cited
    assert summary["negative_handled_rate"] == 1.0  # 1 of 1 negatives handled (passed)
    assert summary["pass_rate"] == round(2 / 3, 2)


def test_by_category_groups_ranking_metrics_and_pass_rate() -> None:
    report = EvalReport(
        dataset="d",
        repo_url="u",
        results=[
            _result(id="t1", category="trace", passed=True, ranking={"mrr": 1.0, "ndcg@10": 0.8}),
            _result(id="t2", category="trace", passed=False, ranking={"mrr": 0.5, "ndcg@10": 0.4}),
            _result(id="e1", category="explain", passed=True, ranking={"mrr": 1.0}),
            _result(
                id="n1",
                category="negative",
                is_negative=True,
                refused=True,
                n_citations=0,
                correctness=0,
                groundedness=0,
                passed=True,
            ),
        ],
    )
    by_category = report.by_category()

    assert set(by_category) == {"trace", "explain", "negative"}
    assert by_category["trace"]["questions"] == 2
    assert by_category["trace"]["mrr"] == 0.75
    assert by_category["trace"]["ndcg@10"] == 0.6
    assert by_category["trace"]["pass_rate"] == 0.5
    assert by_category["explain"]["mrr"] == 1.0
    # Negatives carry no ranking metrics, only pass rate.
    assert by_category["negative"] == {"questions": 1, "pass_rate": 1.0}


def test_router_expected_path_and_correctness() -> None:
    from repo_assistant.evaluation.harness import _expected_path, _router_correct

    assert _expected_path("trace") == "agent"
    assert _expected_path("architecture") == "agent"
    assert _expected_path("lookup") == "fast"
    assert _router_correct("trace", "agent") is True
    assert _router_correct("trace", "fast") is False
    # Negatives are answerable by either path.
    assert _router_correct("negative", "agent") is True


def test_summary_includes_routing_metrics_in_agentic_mode() -> None:
    report = EvalReport(
        dataset="d",
        repo_url="u",
        results=[
            _result(
                id="t1",
                category="trace",
                path="agent",
                n_tool_calls=4,
                forced_stop=False,
                router_correct=True,
            ),
            _result(
                id="t2",
                category="trace",
                path="agent",
                n_tool_calls=8,
                forced_stop=True,
                router_correct=True,
            ),
            _result(id="l1", category="lookup", path="fast", router_correct=True),
            _result(id="l2", category="lookup", path="agent", router_correct=False),
        ],
    )
    summary = report.summary()
    assert summary["router_path_accuracy"] == 0.75  # 3 of 4 routed correctly
    assert summary["agent_path_share"] == 0.75  # 3 of 4 took the agent path
    assert summary["budget_ok_rate"] == round(2 / 3, 2)  # 2 of 3 agent runs stayed in budget
    assert summary["avg_tool_calls"] == round((4 + 8 + 0) / 3, 2)


def test_summary_omits_routing_metrics_without_routing() -> None:
    report = EvalReport(dataset="d", repo_url="u", results=[_result(id="p1")])
    assert "router_path_accuracy" not in report.summary()


def test_summary_of_empty_report_is_zeroed() -> None:
    summary = EvalReport(dataset="d", repo_url="u").summary()
    assert summary["questions"] == 0
    assert summary["retrieval_recall"] == 0.0


def test_gate_passes_above_floors_and_fails_below() -> None:
    from repo_assistant.evaluation.harness import GATE_FLOORS, gate_failures

    good = {"recall@10": 1.0, "mrr": 0.86, "ndcg@10": 0.87}
    assert gate_failures(good) == []

    bad = {"recall@10": 0.5, "mrr": 0.86, "ndcg@10": 0.87}
    failures = gate_failures(bad)
    assert len(failures) == 1
    assert "recall@10" in failures[0]

    # A metric missing from `overall` counts as 0 -> fails.
    assert any("mrr" in f for f in gate_failures({"recall@10": 1.0, "ndcg@10": 0.9}))
    assert set(GATE_FLOORS) == {"recall@10", "mrr", "ndcg@10"}


def test_shipped_datasets_are_valid() -> None:
    # The golden datasets we ship must load and be well-formed.
    for path in sorted(Path("evals/datasets").glob("*.yaml")):
        dataset = DatasetSpec.from_yaml(path)
        assert dataset.repo_url.startswith("https://github.com/")
        assert dataset.questions
        assert any(q.is_negative for q in dataset.questions), f"{path.name} has no negative case"
