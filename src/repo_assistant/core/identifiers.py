"""Extract identifier-like tokens from a natural-language query.

The symbol channel matches these against the symbol table, making questions that
name a function/class ("how does `enqueue` work?", "what is `SessionManager`?")
resolve deterministically rather than relying on embedding proximity
(docs/ARCHITECTURE.md §5).
"""

import re

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_BOUNDARY = re.compile(r"[a-z][A-Z]")

# Common English words that are unlikely to be the *identifier* the user means.
# A word is still kept if it looks like code (snake_case / camelCase / SHOUTY).
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "as",
        "at",
        "by",
        "for",
        "from",
        "with",
        "into",
        "out",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "can",
        "will",
        "just",
        "about",
        "return",
        "returns",
        "value",
        "values",
        "method",
        "methods",
        "function",
        "functions",
        "class",
        "code",
        "work",
        "works",
        "handle",
        "handles",
        "use",
        "uses",
        "using",
        "given",
        "get",
        "gets",
        "set",
        "sets",
        "make",
        "makes",
        "need",
        "needs",
    ]
)


def _looks_like_code(token: str) -> bool:
    return (
        "_" in token
        or _CAMEL_BOUNDARY.search(token) is not None
        or (token.isupper() and len(token) > 1)
    )


def extract_identifiers(query: str, *, max_terms: int = 8) -> list[str]:
    """Return candidate identifier tokens, most-specific first, deduped."""
    coded: list[str] = []
    plain: list[str] = []
    for token in _TOKEN.findall(query):
        if len(token) < 3:
            continue
        if _looks_like_code(token):
            coded.append(token)
        elif token.lower() not in _STOPWORDS:
            plain.append(token)
    ordered = list(dict.fromkeys([*coded, *plain]))
    return ordered[:max_terms]
