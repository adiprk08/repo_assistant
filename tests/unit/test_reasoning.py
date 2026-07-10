"""Citation verification and the single-pass grounded-answer flow."""

from dataclasses import replace
from typing import Any

from repo_assistant.core.fakes import FakeEmbedder, FakeVectorIndex
from repo_assistant.core.interfaces import (
    Citation,
    Document,
    LLMClient,
    LLMResponse,
    Usage,
    VectorPoint,
)
from repo_assistant.reasoning.citations import verify_citations
from repo_assistant.reasoning.service import answer_question
from repo_assistant.retrieval.service import RetrievedChunk

CHUNK_TEXT = "def refresh(self, token):\n    return token + '-new'\n"

_BASE_CHUNK = RetrievedChunk(
    chunk_id="c1",
    path="src/service.py",
    text=CHUNK_TEXT,
    start_line=10,
    end_line=11,
    commit="abc123",
    symbol="Service.refresh",
    language="python",
    score=0.9,
)


def _chunk(**overrides: Any) -> RetrievedChunk:
    return replace(_BASE_CHUNK, **overrides)


# --- citation verification ---------------------------------------------------


def test_valid_citation_maps_to_absolute_file_lines() -> None:
    chunk = _chunk()
    # Cite the second line of the chunk ("    return token...").
    start = CHUNK_TEXT.index("    return")
    end = CHUNK_TEXT.index("-new'") + len("-new'")
    citation = Citation(
        document_index=0, start_char=start, end_char=end, cited_text=CHUNK_TEXT[start:end]
    )

    (verified,) = verify_citations((citation,), [chunk])
    assert verified.path == "src/service.py"
    assert verified.start_line == 11  # chunk starts at line 10, cited span is its 2nd line
    assert verified.end_line == 11
    assert verified.commit == "abc123"
    assert verified.label() == "src/service.py:11"


def test_citation_with_mismatched_text_is_dropped() -> None:
    chunk = _chunk()
    # Offsets point at real text, but cited_text claims something else -> fabricated.
    citation = Citation(document_index=0, start_char=0, end_char=3, cited_text="XYZ")
    assert verify_citations((citation,), [chunk]) == []


def test_citation_with_out_of_range_document_is_dropped() -> None:
    citation = Citation(document_index=5, start_char=0, end_char=3, cited_text="def")
    assert verify_citations((citation,), [_chunk()]) == []


def test_duplicate_citations_are_deduped() -> None:
    chunk = _chunk()
    citation = Citation(document_index=0, start_char=0, end_char=3, cited_text="def")
    verified = verify_citations((citation, citation), [chunk])
    assert len(verified) == 1


def test_multiline_citation_spans_correct_line_range() -> None:
    chunk = _chunk()
    citation = Citation(
        document_index=0,
        start_char=0,
        end_char=len(CHUNK_TEXT.rstrip()),
        cited_text=CHUNK_TEXT.rstrip(),
    )
    (verified,) = verify_citations((citation,), [chunk])
    assert (verified.start_line, verified.end_line) == (10, 11)


# --- end-to-end answer flow with fakes ---------------------------------------


class _CitingLLM(LLMClient):
    """A fake LLM that cites the first half of the first document it is given."""

    async def generate(
        self, *, messages, system="", documents=None, tools=None, max_tokens=4096
    ) -> LLMResponse:
        doc: Document = (documents or [])[0]
        cited = doc.content[: doc.content.index("\n")]
        citation = Citation(document_index=0, start_char=0, end_char=len(cited), cited_text=cited)
        return LLMResponse(
            text=f"It refreshes a token. [{doc.title}]",
            citations=(citation,),
            usage=Usage(input_tokens=100, output_tokens=20),
        )


async def _index_one_chunk() -> tuple[FakeEmbedder, FakeVectorIndex, str]:
    embedder = FakeEmbedder(dimensions=32)
    index = FakeVectorIndex()
    repo_id = "repo-1"
    (vec,) = await embedder.embed([CHUNK_TEXT])
    await index.upsert(
        [
            VectorPoint(
                id="c1",
                dense_vector=vec,
                sparse_vector=None,
                payload={
                    "repo_id": repo_id,
                    "path": "src/service.py",
                    "text": CHUNK_TEXT,
                    "start_line": 10,
                    "end_line": 11,
                    "commit": "abc123",
                    "symbol": "Service.refresh",
                    "language": "python",
                },
            )
        ]
    )
    return embedder, index, repo_id


async def test_answer_question_returns_verified_citation() -> None:
    embedder, index, repo_id = await _index_one_chunk()
    result = await answer_question(
        repo_id,
        "what does refresh do?",
        embedder=embedder,
        vector_index=index,
        llm=_CitingLLM(),
    )
    assert not result.refused
    assert "refreshes a token" in result.text
    assert len(result.citations) == 1
    assert result.citations[0].path == "src/service.py"
    assert result.citations[0].start_line == 10


async def test_answer_question_refuses_when_nothing_retrieved() -> None:
    embedder = FakeEmbedder(dimensions=32)
    index = FakeVectorIndex()  # empty
    result = await answer_question(
        "empty-repo", "anything?", embedder=embedder, vector_index=index, llm=_CitingLLM()
    )
    assert result.refused
    assert "could not find" in result.text.lower()
    assert result.citations == []


async def test_answer_drops_fabricated_citations(monkeypatch) -> None:
    embedder, index, repo_id = await _index_one_chunk()

    class _FabricatingLLM(LLMClient):
        async def generate(
            self, *, messages, system="", documents=None, tools=None, max_tokens=4096
        ):
            bogus = Citation(document_index=0, start_char=0, end_char=3, cited_text="NOPE")
            return LLMResponse(text="Made up.", citations=(bogus,), usage=Usage(1, 1))

    result = await answer_question(
        repo_id, "q?", embedder=embedder, vector_index=index, llm=_FabricatingLLM()
    )
    assert result.citations == []  # fabricated citation dropped by verification
