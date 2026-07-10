"""LLM-as-judge parsing and retry robustness (no network).

A judge that returns unparseable output is a judge-side failure, not evidence
about the answer; scoring it as a wrong answer would corrupt pass_rate. These
tests pin the retry-and-recover behavior (docs/EVALUATION.md §3).
"""

from repo_assistant.core.interfaces import LLMClient, LLMResponse
from repo_assistant.evaluation.judge import judge_answer, judge_negative


class _ScriptedLLM(LLMClient):
    """Returns a fixed sequence of response texts, one per generate() call."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.calls = 0
        self.last_prompt = ""

    async def generate(
        self, *, messages, system="", documents=None, tools=None, max_tokens=4096
    ) -> LLMResponse:
        self.last_prompt = messages[-1].content
        text = self._texts[min(self.calls, len(self._texts) - 1)]
        self.calls += 1
        return LLMResponse(text=text)


async def test_judge_answer_parses_clean_json() -> None:
    llm = _ScriptedLLM(['{"correctness": 5, "groundedness": 4, "rationale": "good"}'])
    result = await judge_answer(llm, question="q", answer="a", expected_files=["f.py"])
    assert (result.correctness, result.groundedness, result.rationale) == (5, 4, "good")
    assert llm.calls == 1


async def test_judge_answer_retries_then_recovers() -> None:
    # First response is prose with no JSON; the retry returns a valid object.
    llm = _ScriptedLLM(
        ["Sure, here is my assessment of the answer.", '{"correctness": 4, "groundedness": 5}']
    )
    result = await judge_answer(llm, question="q", answer="a", expected_files=["f.py"])
    assert result.correctness == 4
    assert result.groundedness == 5
    assert llm.calls == 2


async def test_judge_answer_gives_up_after_retry() -> None:
    llm = _ScriptedLLM(["no json here", "still no json"])
    result = await judge_answer(llm, question="q", answer="a", expected_files=["f.py"])
    assert result.rationale == "unparseable judge output"
    assert llm.calls == 2


async def test_judge_answer_clamps_out_of_range_scores() -> None:
    llm = _ScriptedLLM(['{"correctness": 9, "groundedness": 0, "rationale": "x"}'])
    result = await judge_answer(llm, question="q", answer="a", expected_files=["f.py"])
    assert result.correctness == 5  # clamped to max
    assert result.groundedness == 1  # clamped to min


async def test_judge_answer_ignores_non_object_json() -> None:
    # A bare JSON array is valid JSON but not a judgement; treat as unparseable.
    llm = _ScriptedLLM(["[1, 2, 3]", "[4, 5]"])
    result = await judge_answer(llm, question="q", answer="a", expected_files=["f.py"])
    assert result.rationale == "unparseable judge output"


async def test_judge_answer_includes_evidence_in_prompt() -> None:
    # The source excerpts must reach the judge so it grades against real code,
    # not its (possibly stale) memory of the library.
    llm = _ScriptedLLM(['{"correctness": 5, "groundedness": 5, "rationale": "ok"}'])
    evidence = "# src/click/testing.py:80-116\nclass _FDCapture:\n    ..."
    await judge_answer(
        llm, question="q", answer="a", expected_files=["testing.py"], evidence=evidence
    )
    assert "_FDCapture" in llm.last_prompt
    assert evidence in llm.last_prompt


async def test_judge_negative_retries_then_recovers() -> None:
    llm = _ScriptedLLM(["prose only", '{"handled_correctly": true, "rationale": "ok"}'])
    handled, rationale = await judge_negative(llm, question="q", answer="a")
    assert handled is True
    assert rationale == "ok"
    assert llm.calls == 2


async def test_judge_negative_gives_up_after_retry() -> None:
    llm = _ScriptedLLM(["nope", "nope again"])
    handled, rationale = await judge_negative(llm, question="q", answer="a")
    assert handled is False
    assert rationale == "unparseable judge output"
