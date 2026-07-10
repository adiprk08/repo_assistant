"""BM25 sparse tokenization and vectorization."""

from repo_assistant.core.sparse import text_to_sparse, tokenize


def test_tokenize_splits_snake_case() -> None:
    assert set(tokenize("resolve_command")) >= {"resolve", "command"}


def test_tokenize_splits_camel_case_and_keeps_whole() -> None:
    tokens = set(tokenize("resolveCommand"))
    assert "resolve" in tokens
    assert "command" in tokens
    assert "resolvecommand" in tokens


def test_tokenize_drops_single_chars() -> None:
    assert "a" not in tokenize("a bb ccc")


def test_sparse_vector_is_term_frequency() -> None:
    # "queue" appears twice -> its weight exceeds a once-seen term's.
    vec = text_to_sparse("queue queue enqueue")
    from repo_assistant.core.sparse import _token_id

    assert vec[_token_id("queue")] > vec[_token_id("enqueue")]


def test_shared_identifier_terms_match_across_forms() -> None:
    doc = set(text_to_sparse("def resolve_command(self): ..."))
    query = set(text_to_sparse("how does resolveCommand work"))
    assert doc & query  # "resolve" and "command" overlap


def test_empty_text_is_empty_vector() -> None:
    assert text_to_sparse("") == {}
