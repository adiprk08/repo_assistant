"""Retrieval: turn a natural-language query into ranked, citable chunks.

Phase 1 is dense-only — embed the query (as a ``query``-type embedding) and search
the repo's partition of the vector index. Hybrid channels, the symbol lookup, and
reranking arrive in Phase 2 (docs/ROADMAP.md).
"""

from dataclasses import dataclass
from typing import Any

from repo_assistant.core.interfaces import Embedder, VectorIndex
from repo_assistant.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    path: str
    text: str
    start_line: int
    end_line: int
    commit: str
    symbol: str | None
    language: str | None
    score: float


def _to_chunk(result: Any) -> RetrievedChunk:
    p = result.payload
    return RetrievedChunk(
        chunk_id=result.id,
        path=p["path"],
        text=p["text"],
        start_line=p["start_line"],
        end_line=p["end_line"],
        commit=p.get("commit", ""),
        symbol=p.get("symbol"),
        language=p.get("language"),
        score=result.score,
    )


async def retrieve(
    repo_id: str,
    query: str,
    *,
    embedder: Embedder,
    vector_index: VectorIndex,
    limit: int = 12,
    filters: dict[str, Any] | None = None,
) -> list[RetrievedChunk]:
    """Return the top-``limit`` chunks for ``query`` within one repo."""
    if not query.strip():
        return []
    (query_vector,) = await embedder.embed([query], input_type="query")
    results = await vector_index.query(
        repo_id=repo_id, dense_vector=query_vector, filters=filters, limit=limit
    )
    chunks = [_to_chunk(r) for r in results]
    logger.info("retrieved", repo_id=repo_id, query_len=len(query), hits=len(chunks))
    return chunks
