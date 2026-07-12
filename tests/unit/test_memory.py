"""Conversation-memory helpers: windowing, history framing, condensation, summary."""

from typing import Any

from repo_assistant.core.interfaces import Document, LLMClient, LLMResponse, Message
from repo_assistant.reasoning.memory import (
    Turn,
    build_history,
    condense_followup,
    roll_summary,
    split_window,
)


class CannedLLM(LLMClient):
    """Returns a fixed text and records the prompts it saw."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    async def generate(
        self,
        *,
        messages: list[Message],
        system: str = "",
        documents: list[Document] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(messages[-1].content)
        return LLMResponse(text=self.text)


def _turns(*roles_contents: tuple[str, str]) -> list[Turn]:
    return [Turn(role=r, content=c) for r, c in roles_contents]


def test_split_window_keeps_last_n() -> None:
    turns = _turns(("user", "1"), ("assistant", "2"), ("user", "3"), ("assistant", "4"))
    aged, recent = split_window(turns, 2)
    assert [t.content for t in aged] == ["1", "2"]
    assert [t.content for t in recent] == ["3", "4"]


def test_split_window_shorter_than_window() -> None:
    turns = _turns(("user", "1"), ("assistant", "2"))
    aged, recent = split_window(turns, 6)
    assert aged == []
    assert len(recent) == 2


def test_build_history_frames_user_first_assistant_last() -> None:
    # Leading assistant + trailing user should be trimmed for valid alternation.
    recent = _turns(
        ("assistant", "orphan"), ("user", "q1"), ("assistant", "a1"), ("user", "dangling")
    )
    history = build_history(recent, summary=None)
    assert history[0].role == "user" and history[0].content == "q1"
    assert history[-1].role == "assistant" and history[-1].content == "a1"


def test_build_history_prepends_summary_pair() -> None:
    history = build_history(_turns(("user", "q"), ("assistant", "a")), summary="we discussed X")
    assert history[0].role == "user"  # synthetic recap prompt
    assert history[1].role == "assistant" and history[1].content == "we discussed X"
    assert history[-1].content == "a"


async def test_condense_followup_noop_without_context() -> None:
    llm = CannedLLM("REWRITTEN")
    out = await condense_followup(llm, "what is X?", recent=[], summary=None)
    assert out == "what is X?"
    assert llm.calls == []  # no LLM call when there is nothing to resolve against


async def test_condense_followup_rewrites_with_context() -> None:
    llm = CannedLLM("How does the SessionManager.refresh method work?")
    out = await condense_followup(
        llm, "how does it work?", recent=_turns(("user", "tell me about refresh")), summary=None
    )
    assert out == "How does the SessionManager.refresh method work?"


async def test_condense_followup_falls_back_on_empty_rewrite() -> None:
    llm = CannedLLM("   ")
    out = await condense_followup(
        llm, "how does it work?", recent=_turns(("user", "x")), summary=None
    )
    assert out == "how does it work?"


async def test_roll_summary_folds_new_turns() -> None:
    llm = CannedLLM("updated summary")
    out = await roll_summary(llm, "old summary", _turns(("user", "q"), ("assistant", "a")))
    assert out == "updated summary"


async def test_roll_summary_noop_without_new_turns() -> None:
    llm = CannedLLM("should not be used")
    out = await roll_summary(llm, "keep me", [])
    assert out == "keep me"
    assert llm.calls == []
