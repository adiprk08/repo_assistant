"""Single-pass grounded answering (Phase 1 fast path).

retrieve -> build citable documents -> generate -> verify citations. The agentic
multi-hop path and the intent router arrive in Phase 3 (docs/adr/0006).
"""

from dataclasses import dataclass, field

from repo_assistant.core.interfaces import (
    Document,
    Embedder,
    LLMClient,
    Message,
    Usage,
    VectorIndex,
)
from repo_assistant.core.logging import get_logger
from repo_assistant.reasoning.citations import VerifiedCitation, verify_citations
from repo_assistant.reasoning.prompts import SYSTEM_PROMPT
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


async def answer_question(
    repo_id: str,
    question: str,
    *,
    embedder: Embedder,
    vector_index: VectorIndex,
    llm: LLMClient,
    history: list[Message] | None = None,
    limit: int = 12,
) -> Answer:
    """Answer ``question`` about a repo, grounded in retrieved chunks with verified citations."""
    retrieved = await retrieve(
        repo_id, question, embedder=embedder, vector_index=vector_index, limit=limit
    )
    if not retrieved:
        return Answer(text=_REFUSAL, citations=[], retrieved=[], refused=True)

    messages = [*(history or []), Message(role="user", content=question)]
    response = await llm.generate(
        messages=messages,
        system=SYSTEM_PROMPT,
        documents=_documents(retrieved),
    )

    citations = verify_citations(response.citations, retrieved)
    dropped = len(response.citations) - len(citations)
    if dropped:
        logger.warning("dropped unverified citations", dropped=dropped, kept=len(citations))

    return Answer(
        text=response.text,
        citations=citations,
        retrieved=retrieved,
        usage=response.usage,
        refused=False,
    )
