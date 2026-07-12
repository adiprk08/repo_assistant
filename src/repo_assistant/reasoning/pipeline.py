"""Routed reasoning: classify intent, then take the fast or agent path (ADR-0006).

The router picks a path; the fast path is one hybrid retrieval + grounded
generation, the agent path is the budgeted tool loop. Both return the same
``Answer``; this wrapper adds the routing metadata (path, intent, tool budget
usage) that the CLI surfaces and the eval harness scores.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.interfaces import Embedder, LLMClient, OnText, Reranker, VectorIndex
from repo_assistant.reasoning.agent import run_agent
from repo_assistant.reasoning.router import Path, RouterDecision, classify_intent
from repo_assistant.reasoning.service import Answer, generate_answer
from repo_assistant.reasoning.tools import ToolContext
from repo_assistant.retrieval import hybrid_retrieve
from repo_assistant.retrieval.service import RetrievedChunk


@dataclass(frozen=True, slots=True)
class RoutedAnswer:
    answer: Answer | None  # None in gather_only mode
    chunks: list[RetrievedChunk]  # the evidence the chosen path surfaced
    path: Path
    intent: str
    n_tool_calls: int
    forced_stop: bool


async def answer_routed(
    *,
    repo_id: str,
    snapshot_id: str,
    commit: str,
    question: str,
    embedder: Embedder,
    vector_index: VectorIndex,
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMClient,
    router_llm: LLMClient,
    reranker: Reranker | None = None,
    force_path: Path | None = None,
    budget: int = 8,
    gather_only: bool = False,
    on_text: OnText | None = None,
) -> RoutedAnswer:
    """Route ``question`` to the fast or agent path and answer it.

    ``gather_only`` returns the surfaced evidence without the final grounded
    generation — used by the cheap retrieval-only agentic eval. ``on_text``
    streams the final answer's text deltas (both paths).
    """
    if force_path is not None:
        decision = RouterDecision(intent="other", multi_hop=force_path == "agent", path=force_path)
    else:
        decision = await classify_intent(router_llm, question)

    if decision.path == "agent":
        ctx = ToolContext(
            repo_id=repo_id,
            snapshot_id=snapshot_id,
            commit=commit,
            embedder=embedder,
            vector_index=vector_index,
            session_factory=session_factory,
            reranker=reranker,
        )
        result = await run_agent(
            question, ctx=ctx, llm=llm, budget=budget, gather_only=gather_only, on_text=on_text
        )
        return RoutedAnswer(
            answer=result.answer,
            chunks=result.chunks,
            path="agent",
            intent=decision.intent,
            n_tool_calls=result.n_tool_calls,
            forced_stop=result.forced_stop,
        )

    retrieved = await hybrid_retrieve(
        repo_id,
        snapshot_id,
        question,
        embedder=embedder,
        vector_index=vector_index,
        session_factory=session_factory,
        reranker=reranker,
        commit=commit,
        use_graph=False,
        use_rerank=False,
    )
    answer = (
        None
        if gather_only
        else await generate_answer(question, retrieved, llm=llm, on_text=on_text)
    )
    return RoutedAnswer(
        answer=answer,
        chunks=retrieved,
        path="fast",
        intent=decision.intent,
        n_tool_calls=0,
        forced_stop=False,
    )
