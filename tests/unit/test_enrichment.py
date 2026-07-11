"""Contextual chunk descriptions (no network)."""

from dataclasses import replace

from repo_assistant.chunking.models import Chunk
from repo_assistant.core.interfaces import LLMClient, LLMResponse
from repo_assistant.indexing.enrichment import describe_file_chunks, enrich_chunks
from repo_assistant.ingestion.models import FileCategory


def _chunk(index: int, text: str, *, path: str = "a.py", language: str | None = "python") -> Chunk:
    return Chunk(
        path=path,
        language=language,
        category=FileCategory.CODE,
        text=text,
        header=f"{path} > f{index}",
        start_line=index * 10 + 1,
        end_line=index * 10 + 5,
        symbol=f"f{index}",
        index=index,
    )


class _ScriptedLLM(LLMClient):
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def generate(
        self, *, messages, system="", documents=None, tools=None, max_tokens=4096
    ) -> LLMResponse:
        self.calls += 1
        return LLMResponse(text=self._text)


def test_embed_text_includes_context_but_text_excludes_it() -> None:
    base = _chunk(0, "def f(): ...")
    assert base.context is None
    assert "def f(): ..." in base.embed_text
    enriched = replace(base, context="Entry point for parsing.")
    assert "Entry point for parsing." in enriched.embed_text
    assert enriched.embed_text.index("Entry point") < enriched.embed_text.index("def f")
    # The cited span never contains the description.
    assert enriched.text == "def f(): ..."


async def test_describe_file_chunks_maps_blurbs_by_index() -> None:
    llm = _ScriptedLLM('{"0": "First function.", "1": "Second function."}')
    out = await describe_file_chunks(
        llm, file_path="a.py", file_text="...", chunks=[_chunk(0, "a"), _chunk(1, "b")]
    )
    assert out == {0: "First function.", 1: "Second function."}


async def test_describe_file_chunks_tolerates_unparseable() -> None:
    llm = _ScriptedLLM("sorry, no JSON here")
    out = await describe_file_chunks(
        llm, file_path="a.py", file_text="...", chunks=[_chunk(0, "a")]
    )
    assert out == {}


async def test_enrich_chunks_attaches_context_to_code_only() -> None:
    llm = _ScriptedLLM('{"0": "Does A.", "1": "Does B."}')
    code = [_chunk(0, "a"), _chunk(1, "b")]
    doc = Chunk(
        path="README.md",
        language=None,
        category=FileCategory.DOC,
        text="# hi",
        header="",
        start_line=1,
        end_line=1,
        symbol=None,
        index=0,
    )
    enriched = await enrich_chunks(llm, [*code, doc])

    assert enriched[0].context == "Does A."
    assert enriched[1].context == "Does B."
    # Non-code chunk is untouched, and only one call was made (one code file).
    assert enriched[2].context is None
    assert llm.calls == 1
    # Order preserved.
    assert [c.index for c in enriched] == [0, 1, 0]
