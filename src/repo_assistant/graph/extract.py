"""Code-graph edge extraction (docs/adr/0005-code-graph.md).

Phase 3 extracts two edge kinds heuristically from tree-sitter symbols:
- ``contains``: enclosing symbol -> nested symbol (confidence 1.0, structural).
- ``calls``: a symbol whose body references another symbol's name (best-effort,
  confidence scored by locality). Ambiguous common names are dropped rather than
  linked to every homonym. Precision is enforced downstream (RRF + citation
  verification), so this channel is tuned for recall.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A name shared by more than this many symbols is too ambiguous to link across
# files (e.g. `convert` on a dozen ParamType subclasses).
_MAX_HOMONYMS = 8
_SAME_FILE_CONFIDENCE = 0.6
_CROSS_FILE_CONFIDENCE = 0.3


@dataclass(frozen=True, slots=True)
class SymbolContext:
    qualified_name: str
    name: str
    file_path: str
    parent: str | None
    body: str


@dataclass(frozen=True, slots=True)
class EdgeRow:
    src: str
    dst: str
    kind: str
    confidence: float
    src_file: str


def _body_identifiers(body: str) -> set[str]:
    return set(_IDENTIFIER.findall(body))


def extract_edges(contexts: Iterable[SymbolContext]) -> list[EdgeRow]:
    contexts = list(contexts)
    edges: list[EdgeRow] = []
    seen: set[tuple[str, str, str]] = set()

    def add(src: str, dst: str, kind: str, confidence: float, src_file: str) -> None:
        key = (src, dst, kind)
        if src != dst and key not in seen:
            seen.add(key)
            edges.append(
                EdgeRow(src=src, dst=dst, kind=kind, confidence=confidence, src_file=src_file)
            )

    # Structural containment from the qualified-name hierarchy.
    for ctx in contexts:
        if ctx.parent:
            add(ctx.parent, ctx.qualified_name, "contains", 1.0, ctx.file_path)

    # name -> the symbols that define it (for call resolution).
    by_name: dict[str, list[SymbolContext]] = {}
    for ctx in contexts:
        by_name.setdefault(ctx.name, []).append(ctx)

    for ctx in contexts:
        referenced = _body_identifiers(ctx.body)
        for name in referenced:
            targets = by_name.get(name)
            if not targets:
                continue
            same_file = [t for t in targets if t.file_path == ctx.file_path]
            if same_file:
                for target in same_file:
                    add(
                        ctx.qualified_name,
                        target.qualified_name,
                        "calls",
                        _SAME_FILE_CONFIDENCE,
                        ctx.file_path,
                    )
            elif len(targets) <= _MAX_HOMONYMS:
                for target in targets:
                    add(
                        ctx.qualified_name,
                        target.qualified_name,
                        "calls",
                        _CROSS_FILE_CONFIDENCE,
                        ctx.file_path,
                    )
    return edges
