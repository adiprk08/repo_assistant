"""Code-graph edge extraction."""

from repo_assistant.graph.extract import SymbolContext, extract_edges


def _ctx(qname: str, name: str, file: str, parent: str | None, body: str) -> SymbolContext:
    return SymbolContext(qualified_name=qname, name=name, file_path=file, parent=parent, body=body)


def test_contains_edges_from_parent() -> None:
    contexts = [
        _ctx("Service", "Service", "s.py", None, "class Service: ..."),
        _ctx("Service.refresh", "refresh", "s.py", "Service", "def refresh(self): ..."),
    ]
    edges = extract_edges(contexts)
    contains = [e for e in edges if e.kind == "contains"]
    assert any(e.src == "Service" and e.dst == "Service.refresh" for e in contains)
    assert all(e.confidence == 1.0 for e in contains)


def test_calls_edge_when_body_references_another_symbol() -> None:
    contexts = [
        _ctx("caller", "caller", "a.py", None, "def caller():\n    return helper()"),
        _ctx("helper", "helper", "a.py", None, "def helper():\n    return 1"),
    ]
    edges = extract_edges(contexts)
    calls = [e for e in edges if e.kind == "calls"]
    assert any(e.src == "caller" and e.dst == "helper" for e in calls)


def test_same_file_call_scores_higher_than_cross_file() -> None:
    same = extract_edges(
        [
            _ctx("a", "a", "f.py", None, "b()"),
            _ctx("b", "b", "f.py", None, "pass"),
        ]
    )
    cross = extract_edges(
        [
            _ctx("a", "a", "f.py", None, "b()"),
            _ctx("b", "b", "other.py", None, "pass"),
        ]
    )
    same_conf = next(e.confidence for e in same if e.kind == "calls")
    cross_conf = next(e.confidence for e in cross if e.kind == "calls")
    assert same_conf > cross_conf


def test_ambiguous_homonyms_are_not_linked_across_files() -> None:
    # 9 cross-file definitions of "convert" -> too ambiguous to link.
    contexts = [_ctx("caller", "caller", "main.py", None, "convert()")]
    contexts += [_ctx(f"C{i}.convert", "convert", f"f{i}.py", f"C{i}", "pass") for i in range(9)]
    edges = extract_edges(contexts)
    assert not [e for e in edges if e.kind == "calls" and e.dst.endswith("convert")]


def test_no_self_edges() -> None:
    edges = extract_edges([_ctx("rec", "rec", "a.py", None, "def rec():\n    return rec()")])
    assert all(e.src != e.dst for e in edges)
