"""LLM-as-judge grading (docs/EVALUATION.md §3).

The judge scores an answer against the question and the gold evidence files on
two axes — correctness and groundedness — returning a small JSON object we parse.
Kept model-agnostic behind ``LLMClient``; the judge model id is configuration.
"""

from dataclasses import dataclass

from repo_assistant.core.interfaces import LLMClient, Message
from repo_assistant.core.json_parse import extract_json_object
from repo_assistant.core.logging import get_logger

logger = get_logger(__name__)

_JUDGE_SYSTEM = """\
You are a strict evaluator of a code-question-answering system. You are given a \
question, the assistant's answer, the repository files that contain the answer \
(the gold evidence), and source excerpts retrieved from those files for this \
question. Score the answer on two axes from 1 to 5:

- correctness: Is the answer factually right about the code? 5 = fully correct, \
1 = wrong or misleading.
- groundedness: Are the claims supported by the source excerpts rather than \
generic knowledge? 5 = clearly grounded in the excerpts, 1 = ungrounded.

IMPORTANT: Treat the provided source excerpts as the authoritative ground truth \
about this repository. Judge correctness against them, NOT against any prior \
knowledge you have of the library — that knowledge may be outdated or describe a \
different version. If the answer describes code that appears in the excerpts, it \
is correct even if it conflicts with what you remember about the library. The \
excerpts are a subset of the evidence, so an answer may correctly reference code \
not shown; do not penalize a claim merely for being absent from the excerpts \
unless the excerpts contradict it.

Respond with ONLY a JSON object, no prose:
{"correctness": <1-5>, "groundedness": <1-5>, "rationale": "<one sentence>"}\
"""


@dataclass(frozen=True, slots=True)
class Judgement:
    correctness: int
    groundedness: int
    rationale: str


# Headroom so a verbose rationale (or a brief model preamble) can't truncate the
# closing brace and make the whole judgement unparseable.
_JUDGE_MAX_TOKENS = 512


def _clamp(value: object) -> int:
    try:
        return max(1, min(5, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1


async def _judge_json(
    llm: LLMClient, *, system: str, prompt: str, attempts: int = 2
) -> dict | None:
    """Generate and parse a JSON judgement, retrying once on a formatting glitch.

    An unparseable judge response is a judge-side failure, not evidence about the
    answer — scoring it as a wrong answer would corrupt pass_rate — so we retry
    before giving up (docs/EVALUATION.md §3).
    """
    for attempt in range(attempts):
        response = await llm.generate(
            messages=[Message(role="user", content=prompt)],
            system=system,
            max_tokens=_JUDGE_MAX_TOKENS,
        )
        data = extract_json_object(response.text.strip())
        if data is not None:
            return data
        logger.warning("judge output not parseable JSON", attempt=attempt, text=response.text[:200])
    return None


_NEGATIVE_JUDGE_SYSTEM = """\
You are evaluating whether a code assistant correctly handled a question whose \
answer is NOT present in the repository. The correct behavior is to indicate the \
feature/capability is absent (or that it could not be found) WITHOUT fabricating \
that the repository provides it.

Respond with ONLY a JSON object, no prose:
{"handled_correctly": <true|false>, "rationale": "<one sentence>"}

handled_correctly is true if the answer makes clear the capability is not present \
in this repository; false if it invents or claims the capability exists here.\
"""


async def judge_negative(llm: LLMClient, *, question: str, answer: str) -> tuple[bool, str]:
    """Return (handled_correctly, rationale) for a not-present question."""
    prompt = f"Question:\n{question}\n\nAssistant answer:\n{answer}\n"
    data = await _judge_json(llm, system=_NEGATIVE_JUDGE_SYSTEM, prompt=prompt)
    if data is None:
        return False, "unparseable judge output"
    return bool(data.get("handled_correctly")), str(data.get("rationale", ""))[:300]


async def judge_answer(
    llm: LLMClient, *, question: str, answer: str, expected_files: list[str], evidence: str = ""
) -> Judgement:
    prompt = (
        f"Question:\n{question}\n\n"
        f"Gold evidence files:\n{', '.join(expected_files) or '(none)'}\n\n"
        f"Source excerpts (authoritative — grade against these, not prior knowledge):\n"
        f"{evidence or '(none provided)'}\n\n"
        f"Assistant answer:\n{answer}\n"
    )
    data = await _judge_json(llm, system=_JUDGE_SYSTEM, prompt=prompt)
    if data is None:
        return Judgement(correctness=1, groundedness=1, rationale="unparseable judge output")
    return Judgement(
        correctness=_clamp(data.get("correctness")),
        groundedness=_clamp(data.get("groundedness")),
        rationale=str(data.get("rationale", ""))[:300],
    )
