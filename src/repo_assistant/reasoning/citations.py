"""Citation verification — the anti-hallucination backstop (docs/adr/0007).

The model emits char-anchored citations into the document set we supplied. We do
not trust them blindly: each is re-checked against the exact chunk text we sent,
and only citations whose characters actually match are surfaced, mapped to
``path:start_line-end_line@commit``. Anything that fails verification is dropped.
"""

from dataclasses import dataclass

from repo_assistant.core.interfaces import Citation as ApiCitation
from repo_assistant.retrieval.service import RetrievedChunk


@dataclass(frozen=True, slots=True)
class VerifiedCitation:
    path: str
    start_line: int
    end_line: int
    commit: str
    cited_text: str

    def label(self) -> str:
        span = (
            f"{self.start_line}"
            if self.start_line == self.end_line
            else f"{self.start_line}-{self.end_line}"
        )
        return f"{self.path}:{span}"


def _line_at(text: str, char_offset: int) -> int:
    """0-based line offset of ``char_offset`` within ``text``."""
    return text.count("\n", 0, char_offset)


def verify_citations(
    api_citations: tuple[ApiCitation, ...], retrieved: list[RetrievedChunk]
) -> list[VerifiedCitation]:
    """Keep only citations whose characters match the chunk we actually sent."""
    verified: list[VerifiedCitation] = []
    seen: set[tuple[str, int, int]] = set()

    for citation in api_citations:
        idx = citation.document_index
        if not 0 <= idx < len(retrieved):
            continue
        chunk = retrieved[idx]

        # The offsets must reproduce exactly the text the model claims to cite.
        if chunk.text[citation.start_char : citation.end_char] != citation.cited_text:
            continue

        start_line = chunk.start_line + _line_at(chunk.text, citation.start_char)
        end_line = chunk.start_line + _line_at(
            chunk.text, max(citation.start_char, citation.end_char - 1)
        )

        key = (chunk.path, start_line, end_line)
        if key in seen:
            continue
        seen.add(key)
        verified.append(
            VerifiedCitation(
                path=chunk.path,
                start_line=start_line,
                end_line=end_line,
                commit=chunk.commit,
                cited_text=citation.cited_text,
            )
        )
    return verified
