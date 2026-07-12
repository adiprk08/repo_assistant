"""Single-pass grounded answering (Phase 1 fast path).

retrieve -> build citable documents -> generate -> verify citations. The agentic
multi-hop path and the intent router arrive in Phase 3 (docs/adr/0006).
"""

from dataclasses import dataclass, field

from repo_assistant.core import metrics
from repo_assistant.core.interfaces import (
    Document,
    Embedder,
    LLMClient,
    Message,
    OnText,
    Usage,
    VectorIndex,
)
from repo_assistant.core.logging import get_logger
from repo_assistant.reasoning.citations import VerifiedCitation, verify_citations
from repo_assistant.reasoning.prompts import SYSTEM_PROMPT
from repo_assistant.retrieval.assembly import assemble_context
from repo_assistant.retrieval.service import RetrievedChunk, retrieve

logger = get_logger(__name__)

_REFUSAL = "I could not find this in the repository."


@dataclass(frozen=True, slots=True)
class Answer:
    text: str
    citations: list[VerifiedCitation]
    retrieved: list[RetrievedChunk]
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    refused: bool = False


def _documents(retrieved: list[RetrievedChunk]) -> list[Document]:
    return [
        Document(
            id=chunk.chunk_id,
            title=f"{chunk.path}:{chunk.start_line}-{chunk.end_line}",
            content=chunk.text,
        )
        for chunk in retrieved
    ]


async def generate_answer(
    question: str,
    retrieved: list[RetrievedChunk],
    *,
    llm: LLMClient,
    history: list[Message] | None = None,
    context_limit: int = 12,
    on_text: OnText | None = None,
) -> Answer:
    """Generate a grounded, citation-verified answer from already-retrieved chunks.

    The retrieved chunks are assembled first — overlapping spans deduped and each
    file capped — so the model gets diverse, non-redundant context and citations
    don't pile up on the same lines. When ``on_text`` is given, text deltas are
    streamed through it while the returned Answer stays complete and verified.
    """
    context = assemble_context(retrieved, limit=context_limit)
    if not context:
        if on_text is not None:
            await on_text(_REFUSAL)
        return Answer(text=_REFUSAL, citations=[], retrieved=[], refused=True)

    messages = [*(history or []), Message(role="user", content=question)]
    if on_text is None:
        response = await llm.generate(
            messages=messages,
            system=SYSTEM_PROMPT,
            documents=_documents(context),
        )
    else:
        response = await llm.generate_stream(
            messages=messages,
            on_text=on_text,
            system=SYSTEM_PROMPT,
            documents=_documents(context),
        )

    citations = verify_citations(response.citations, context)
    dropped = len(response.citations) - len(citations)
    if dropped:
        logger.warning("dropped unverified citations", dropped=dropped, kept=len(citations))
        metrics.observe_citation_drops(dropped)

    return Answer(
        text=response.text,
        citations=citations,
        retrieved=context,
        usage=response.usage,
        refused=False,
    )


async def answer_question(
    repo_id: str,
    question: str,
    *,
    embedder: Embedder,
    vector_index: VectorIndex,
    llm: LLMClient,
    history: list[Message] | None = None,
    limit: int = 12,
    filters: dict[str, object] | None = None,
) -> Answer:
    """Answer ``question`` about a repo, grounded in retrieved chunks with verified citations."""
    retrieved = await retrieve(
        repo_id,
        question,
        embedder=embedder,
        vector_index=vector_index,
        limit=limit,
        filters=filters,
    )
    return await generate_answer(question, retrieved, llm=llm, history=history)
