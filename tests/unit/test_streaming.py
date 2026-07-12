"""Streaming contract: SSE formatting, fake streaming LLM, and streamed generation."""

import json

import pytest

from repo_assistant.api.sse import sse_event
from repo_assistant.core.fakes import FakeLLMClient
from repo_assistant.core.interfaces import Document, Message
from repo_assistant.reasoning.service import _REFUSAL, generate_answer
from repo_assistant.retrieval.service import RetrievedChunk


def test_sse_event_shape() -> None:
    raw = sse_event("progress", {"stage": "embedding", "chunks": 12})
    assert raw.startswith("event: progress\ndata: ")
    assert raw.endswith("\n\n")
    body = raw.split("data: ", 1)[1].strip()
    assert json.loads(body) == {"stage": "embedding", "chunks": 12}


def _collector(sink: list[str]):
    async def on_text(delta: str) -> None:
        sink.append(delta)

    return on_text


async def test_fake_llm_streams_multiple_deltas() -> None:
    deltas: list[str] = []
    response = await FakeLLMClient().generate_stream(
        messages=[Message(role="user", content="hi")],
        on_text=_collector(deltas),
        documents=[Document(id="d1", title="a.py:1-2", content="print('hello world here')")],
    )
    # Deltas concatenate to exactly the returned text (no loss, no duplication).
    assert "".join(deltas) == response.text
    assert len(deltas) > 1  # streamed word-by-word, not one shot


def _chunk(text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id="c1",
        path="a.py",
        text=text,
        start_line=1,
        end_line=text.count("\n") + 1,
        commit="abc123",
        symbol=None,
        language="python",
        score=1.0,
    )


async def test_generate_answer_streams_full_text() -> None:
    sink: list[str] = []
    answer = await generate_answer(
        "what does it do?",
        [_chunk("def f():\n    return 42")],
        llm=FakeLLMClient(),
        on_text=_collector(sink),
    )
    assert not answer.refused
    assert "".join(sink) == answer.text


async def test_generate_answer_streams_refusal_when_no_context() -> None:
    sink: list[str] = []
    answer = await generate_answer("anything?", [], llm=FakeLLMClient(), on_text=_collector(sink))
    assert answer.refused
    assert "".join(sink) == _REFUSAL


@pytest.mark.parametrize("on_text", [None])
async def test_generate_answer_without_on_text_still_works(on_text: None) -> None:
    answer = await generate_answer("q", [_chunk("body")], llm=FakeLLMClient(), on_text=on_text)
    assert answer.text
