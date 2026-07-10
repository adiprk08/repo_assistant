"""Dependency-free BM25 sparse vectors (docs/adr/0004).

We emit term-frequency sparse vectors and let Qdrant apply IDF at query time (its
``Modifier.IDF``), which gives BM25-style lexical scoring without a corpus-fitting
pass or a neural sparse model. Tokenization is code-aware: identifiers are split
on case and underscore boundaries so ``resolve_command`` and ``resolveCommand``
both match a ``resolve command`` query.
"""

import hashlib
import re
from math import log

_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_MIN_LEN = 2


def _subtokens(token: str) -> set[str]:
    parts = {token.lower()}
    for part in _CAMEL.sub(" ", token).split():
        if len(part) >= _MIN_LEN:
            parts.add(part.lower())
    return parts


def tokenize(text: str) -> list[str]:
    """Split text into lexical terms, expanding camelCase/snake_case identifiers."""
    tokens: list[str] = []
    for match in _WORD.findall(text):
        if len(match) < _MIN_LEN:
            continue
        tokens.extend(t for t in _subtokens(match) if len(t) >= _MIN_LEN)
    return tokens


def _token_id(token: str) -> int:
    """Stable, process-independent uint32 id for a term (Qdrant sparse indices)."""
    return int.from_bytes(hashlib.sha1(token.encode("utf-8")).digest()[:4], "big")


def text_to_sparse(text: str) -> dict[int, float]:
    """Return {token_id: log-saturated term frequency}. Empty for empty text."""
    counts: dict[int, int] = {}
    for token in tokenize(text):
        tid = _token_id(token)
        counts[tid] = counts.get(tid, 0) + 1
    return {tid: 1.0 + log(count) for tid, count in counts.items()}
