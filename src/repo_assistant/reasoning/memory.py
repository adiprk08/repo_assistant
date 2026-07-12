"""Conversation memory: history window, rolling summary, follow-up condensation.

A session keeps its most recent turns verbatim and folds older turns into a
running summary (docs/adr/0015). This module is pure with respect to storage — it
operates on plain turn tuples and an LLM, so it is unit-testable with fakes and
reused by both the API and (later) the CLI. The DB glue lives in the router.
"""

from dataclasses import dataclass

from repo_assistant.core.interfaces import LLMClient, Message
from repo_assistant.core.logging import get_logger
from repo_assistant.reasoning.prompts import CONDENSE_SYSTEM, SUMMARIZE_SYSTEM

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Turn:
    """A stored conversation turn, decoupled from the ORM row."""

    role: str  # "user" | "assistant"
    content: str


def split_window(turns: list[Turn], window: int) -> tuple[list[Turn], list[Turn]]:
    """Return (aged_out, recent) — the last ``window`` turns are kept verbatim."""
    if window <= 0 or len(turns) <= window:
        return [], list(turns)
    return list(turns[:-window]), list(turns[-window:])


def _framed(turns: list[Turn]) -> list[Message]:
    """Turns as API messages, trimmed to a valid user-first / assistant-last frame.

    The verbatim window must start on a ``user`` turn and end on an ``assistant``
    turn so the caller can append the next user question and preserve alternation.
    """
    msgs = [Message(role=t.role, content=t.content) for t in turns if t.content]  # type: ignore[arg-type]
    while msgs and msgs[0].role != "user":
        msgs.pop(0)
    while msgs and msgs[-1].role != "assistant":
        msgs.pop()
    return msgs


def build_history(recent: list[Turn], summary: str | None) -> list[Message]:
    """Assemble the history messages: a summary preface (if any) + the verbatim window."""
    history: list[Message] = []
    if summary:
        # A synthetic exchange injects the summary while keeping role alternation.
        history.append(Message(role="user", content="Recap our conversation so far."))
        history.append(Message(role="assistant", content=summary))
    history.extend(_framed(recent))
    return history


def render_turns(turns: list[Turn]) -> str:
    labels = {"user": "User", "assistant": "Assistant"}
    return "\n".join(f"{labels.get(t.role, t.role)}: {t.content}" for t in turns)


async def condense_followup(
    llm: LLMClient, question: str, recent: list[Turn], summary: str | None
) -> str:
    """Rewrite ``question`` into a standalone query using conversation context.

    Falls back to the raw question when there is no context or the rewrite is
    empty — condensation must never lose the user's intent.
    """
    if not recent and not summary:
        return question
    context = render_turns(recent)
    if summary:
        context = f"Summary of earlier turns:\n{summary}\n\n{context}"
    response = await llm.generate(
        messages=[
            Message(
                role="user",
                content=f"Conversation so far:\n{context}\n\nFollow-up question:\n{question}",
            )
        ],
        system=CONDENSE_SYSTEM,
        max_tokens=256,
    )
    rewritten = response.text.strip()
    if rewritten and rewritten != question:
        logger.info("condensed follow-up", original_len=len(question), rewritten_len=len(rewritten))
    return rewritten or question


async def roll_summary(llm: LLMClient, prior_summary: str | None, new_turns: list[Turn]) -> str:
    """Fold ``new_turns`` into ``prior_summary``, returning the updated summary."""
    if not new_turns:
        return prior_summary or ""
    response = await llm.generate(
        messages=[
            Message(
                role="user",
                content=(
                    f"Summary so far:\n{prior_summary or '(none yet)'}\n\n"
                    f"New turns to fold in:\n{render_turns(new_turns)}"
                ),
            )
        ],
        system=SUMMARIZE_SYSTEM,
        max_tokens=512,
    )
    return response.text.strip() or (prior_summary or "")
