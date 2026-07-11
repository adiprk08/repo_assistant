"""Agentic loop control flow with a scripted LLM (no network, no DB)."""

from dataclasses import dataclass, field

from repo_assistant.core.interfaces import LLMClient, LLMResponse, ToolCall
from repo_assistant.reasoning.agent import run_agent
from repo_assistant.reasoning.tools import ToolContext
from repo_assistant.retrieval.service import RetrievedChunk


def _chunk(cid: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        path="a.py",
        text=f"def f_{cid}(): ...",
        start_line=1,
        end_line=1,
        commit="c",
        symbol=None,
        language="python",
        score=1.0,
    )


@dataclass
class _ScriptedAgentLLM(LLMClient):
    """Emits a queued sequence of responses; the final grounded answer call (no
    tools) returns plain text."""

    turns: list[LLMResponse]
    calls: int = 0
    saw_documents: bool = field(default=False)

    async def generate(self, *, messages, system="", documents=None, tools=None, max_tokens=4096):
        # The final generate_answer call passes documents and no tools.
        if tools is None:
            self.saw_documents = documents is not None
            return LLMResponse(text="Final grounded answer.")
        response = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        return response


class _FakeToolCtx(ToolContext):
    """A ToolContext whose tools are stubbed to record fixed chunks."""

    def __init__(self) -> None:
        self.gathered = {}
        self._executed: list[str] = []


async def _run(monkeypatch, turns, *, budget=8, gather_only=False):
    ctx = _FakeToolCtx()

    async def fake_execute(context, name, arguments):
        context.record([_chunk(name)])
        return f"ran {name}", False

    async def fake_search(context, *, query, k=8):
        context.record([_chunk("search")])
        return "searched"

    monkeypatch.setattr("repo_assistant.reasoning.agent.execute_tool", fake_execute)
    monkeypatch.setattr("repo_assistant.reasoning.agent.search_code", fake_search)

    llm = _ScriptedAgentLLM(turns=turns)
    result = await run_agent(
        "trace the flow", ctx=ctx, llm=llm, budget=budget, gather_only=gather_only
    )
    return result, ctx, llm


async def test_agent_runs_tools_then_answers(monkeypatch) -> None:
    turns = [
        LLMResponse(text="", tool_calls=(ToolCall("1", "get_symbol", {"name": "main"}),)),
        LLMResponse(text="", tool_calls=(ToolCall("2", "graph_neighbors", {"symbol": "main"}),)),
        LLMResponse(text="ready", tool_calls=()),  # model stops
    ]
    result, ctx, llm = await _run(monkeypatch, turns)

    assert result.n_tool_calls == 2
    assert result.forced_stop is False
    assert result.answer is not None
    assert result.answer.text == "Final grounded answer."
    # Both tools' chunks were gathered and fed to the final grounded generation.
    assert {c.chunk_id for c in ctx.grounding_chunks()} == {"get_symbol", "graph_neighbors"}
    assert llm.saw_documents is True


async def test_agent_stops_at_budget(monkeypatch) -> None:
    # Model keeps requesting tools; the loop must cut it off at the budget.
    greedy = LLMResponse(text="", tool_calls=(ToolCall("x", "search_code", {"query": "q"}),))
    result, _ctx, _llm = await _run(monkeypatch, [greedy], budget=3)

    assert result.n_tool_calls >= 3
    assert result.forced_stop is True


async def test_agent_seeds_search_when_no_tools_used(monkeypatch) -> None:
    # Model answers immediately without tools -> safety-net search grounds it.
    result, ctx, _llm = await _run(monkeypatch, [LLMResponse(text="done", tool_calls=())])

    assert result.n_tool_calls == 0
    assert result.forced_stop is False
    assert {c.chunk_id for c in ctx.grounding_chunks()} == {"search"}


async def test_gather_only_skips_generation_but_returns_chunks(monkeypatch) -> None:
    turns = [
        LLMResponse(text="", tool_calls=(ToolCall("1", "get_symbol", {"name": "main"}),)),
        LLMResponse(text="ready", tool_calls=()),
    ]
    result, _ctx, llm = await _run(monkeypatch, turns, gather_only=True)

    assert result.answer is None  # no final grounded generation
    assert {c.chunk_id for c in result.chunks} == {"get_symbol"}
    assert llm.saw_documents is False  # the generate_answer call never happened
