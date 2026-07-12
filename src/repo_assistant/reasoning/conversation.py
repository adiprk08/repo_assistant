"""Session glue around the pure memory helpers (docs/adr/0015).

Bridges stored ``chat_messages`` to the reasoning pipeline: loads the history a
turn should see (verbatim window + summary), condenses the follow-up into a
standalone retrieval query, then persists the user/assistant turns and rolls the
summary forward incrementally. The API chat router calls ``prepare_turn`` before
answering and ``record_turn`` after.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.config import Settings
from repo_assistant.core.interfaces import LLMClient, Message
from repo_assistant.reasoning.memory import (
    Turn,
    build_history,
    condense_followup,
    roll_summary,
    split_window,
)
from repo_assistant.reasoning.service import Answer
from repo_assistant.storage import repositories as repo


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    history: list[Message]
    retrieval_query: str


def _turns(messages) -> list[Turn]:
    return [Turn(role=m.role, content=m.content) for m in messages]


async def prepare_turn(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    question: str,
    *,
    router_llm: LLMClient,
    settings: Settings,
) -> PreparedTurn:
    """Load history for ``session_id`` and condense ``question`` into a search query."""
    async with session_factory() as session:
        chat = await repo.get_session(session, session_id)
        summary = chat.summary if chat else None
        messages = await repo.get_messages(session, session_id)

    _, recent = split_window(_turns(messages), settings.history_window_messages)
    history = build_history(recent, summary)

    retrieval_query = question
    if settings.condense_followups and (recent or summary):
        retrieval_query = await condense_followup(router_llm, question, recent, summary)
    return PreparedTurn(history=history, retrieval_query=retrieval_query)


def _citations_json(answer: Answer) -> list[dict]:
    return [
        {
            "path": c.path,
            "start_line": c.start_line,
            "end_line": c.end_line,
            "commit": c.commit,
            "cited_text": c.cited_text,
        }
        for c in answer.citations
    ]


async def record_turn(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: uuid.UUID,
    *,
    question: str,
    answer: Answer,
    summarizer_llm: LLMClient,
    settings: Settings,
) -> None:
    """Persist the user + assistant turns, then roll the summary forward.

    The summary is updated incrementally: only messages that have newly aged out
    of the verbatim window (beyond ``summary_covered_messages``) are folded in, so
    cost is one summarizer call per turn once a session is long — not a re-read of
    the whole history.
    """
    async with session_factory() as session:
        await repo.append_message(session, session_id, role="user", content=question)
        await repo.append_message(
            session,
            session_id,
            role="assistant",
            content=answer.text,
            citations=_citations_json(answer),
            usage={
                "input_tokens": answer.usage.input_tokens,
                "output_tokens": answer.usage.output_tokens,
            },
        )
        await session.flush()

        chat = await repo.get_session(session, session_id)
        messages = await repo.get_messages(session, session_id)
        aged_out = max(0, len(messages) - settings.history_window_messages)
        covered = chat.summary_covered_messages if chat else 0
        if aged_out > covered:
            new_turns = _turns(messages[covered:aged_out])
            summary = await roll_summary(summarizer_llm, chat.summary if chat else None, new_turns)
            await repo.update_session_summary(session, session_id, summary, aged_out)
        await session.commit()
