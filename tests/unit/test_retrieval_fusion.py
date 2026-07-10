"""RRF fusion and identifier extraction."""

from repo_assistant.retrieval.fusion import reciprocal_rank_fusion
from repo_assistant.retrieval.identifiers import extract_identifiers


def test_rrf_rewards_agreement_across_channels() -> None:
    dense = ["a", "b", "c"]
    symbol = ["c", "a", "z"]
    fused = reciprocal_rank_fusion([dense, symbol])
    order = [cid for cid, _ in fused]
    # "a" (ranks 1 and 2) and "c" (ranks 3 and 1) beat items in one channel only.
    assert order[0] in {"a", "c"}
    assert order[1] in {"a", "c"}
    assert set(order[:2]) == {"a", "c"}


def test_rrf_single_channel_preserves_order() -> None:
    fused = reciprocal_rank_fusion([["x", "y", "z"]])
    assert [cid for cid, _ in fused] == ["x", "y", "z"]


def test_rrf_empty_input() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_higher_rank_scores_more() -> None:
    fused = dict(reciprocal_rank_fusion([["first", "second"]]))
    assert fused["first"] > fused["second"]


def test_extract_identifiers_prefers_code_tokens() -> None:
    ids = extract_identifiers("How does the SessionManager.refresh method work?")
    assert "SessionManager" in ids or "refresh" in ids
    # Stopwords like "how", "does", "the", "method", "work" are dropped.
    assert "how" not in ids
    assert "the" not in ids


def test_extract_identifiers_keeps_snake_and_camel() -> None:
    ids = extract_identifiers("what does resolve_command and isPlainObject do")
    assert "resolve_command" in ids
    assert "isPlainObject" in ids


def test_extract_identifiers_keeps_plain_content_words() -> None:
    # A lowercase content word that could be a symbol name is kept for fuzzy match.
    ids = extract_identifiers("how does enqueue add a value")
    assert "enqueue" in ids


def test_extract_identifiers_drops_short_and_stopwords() -> None:
    assert extract_identifiers("is it in the of a to") == []
