"""Retrieval: turn a natural-language query into ranked, citable chunks.

Phase 1 is dense-only — embed the query (as a ``query``-type embedding) and search
the repo's partition of the vector index. Hybrid channels, the symbol lookup, and
reranking arrive in Phase 2 (docs/ROADMAP.md).
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.interfaces import Embedder, SearchResult, VectorIndex
from repo_assistant.core.logging import get_logger
from repo_assistant.retrieval.fusion import reciprocal_rank_fusion
from repo_assistant.retrieval.symbols import symbol_search

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


def _to_chunk(result: Any, score: float | None = None) -> RetrievedChunk:
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
        score=result.score if score is None else score,
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


async def hybrid_retrieve(
    repo_id: str,
    snapshot_id: str,
    query: str,
    *,
    embedder: Embedder,
    vector_index: VectorIndex,
    session_factory: async_sessionmaker[AsyncSession],
    commit: str | None = None,
    limit: int = 12,
    dense_k: int = 25,
    use_symbols: bool = True,
) -> list[RetrievedChunk]:
    """Retrieve by fusing the dense vector channel with the symbol-lookup channel.

    Channels each produce a ranked list of chunk ids; RRF fuses them, then the
    fused top-``limit`` chunks are materialized (dense payloads reused, any
    symbol-only chunks fetched by id).
    """
    if not query.strip():
        return []

    filters = {"commit": commit} if commit else None
    (query_vector,) = await embedder.embed([query], input_type="query")
    dense_results = await vector_index.query(
        repo_id=repo_id, dense_vector=query_vector, filters=filters, limit=dense_k
    )
    payloads: dict[str, SearchResult] = {r.id: r for r in dense_results}
    rankings: list[list[str]] = [[r.id for r in dense_results]]

    if use_symbols:
        symbol_ids = await symbol_search(session_factory, str(snapshot_id), query)
        if symbol_ids:
            rankings.append(symbol_ids)

    fused = reciprocal_rank_fusion(rankings)[:limit]
    missing = [cid for cid, _ in fused if cid not in payloads]
    if missing:
        for result in await vector_index.fetch(repo_id=repo_id, ids=missing):
            payloads[result.id] = result

    chunks = [_to_chunk(payloads[cid], score=score) for cid, score in fused if cid in payloads]
    logger.info(
        "hybrid retrieved",
        repo_id=repo_id,
        channels=len(rankings),
        hits=len(chunks),
        use_symbols=use_symbols,
    )
    return chunks
