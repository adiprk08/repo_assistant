"""Symbol retrieval channel: match query identifiers against the symbol table.

Each matched symbol is mapped to the chunk that contains it, so this channel
emits ranked chunk ids that fuse with the vector channels (docs/ARCHITECTURE.md
§5). Matching is exact (case-insensitive) or trigram-fuzzy via pg_trgm.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.logging import get_logger
from repo_assistant.retrieval.identifiers import extract_identifiers

logger = get_logger(__name__)

# Map a matched symbol to the chunk whose line span contains the symbol's start.
_QUERY = text(
    """
    SELECT c.id::text AS chunk_id,
           GREATEST(
               similarity(s.name, :term),
               CASE WHEN lower(s.name) = lower(:term) THEN 1.0 ELSE 0.0 END,
               CASE WHEN s.qualified_name ILIKE :like THEN 0.6 ELSE 0.0 END
           ) AS score
    FROM symbols s
    JOIN chunks c
      ON c.snapshot_id = s.snapshot_id
     AND c.file_path = s.file_path
     AND c.start_line <= s.start_line
     AND c.end_line >= s.start_line
    WHERE s.snapshot_id = :snapshot_id
      AND (
          similarity(s.name, :term) >= :min_sim
          OR lower(s.name) = lower(:term)
          OR s.qualified_name ILIKE :like
      )
    ORDER BY score DESC
    LIMIT :limit
    """
)


async def _search_term(
    session: AsyncSession, snapshot_id: str, term: str, *, min_sim: float, limit: int
) -> list[tuple[str, float]]:
    rows = await session.execute(
        _QUERY,
        {
            "term": term,
            "like": f"%{term}%",
            "snapshot_id": snapshot_id,
            "min_sim": min_sim,
            "limit": limit,
        },
    )
    return [(row.chunk_id, float(row.score)) for row in rows]


async def symbol_search(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot_id: str,
    query: str,
    *,
    limit: int = 15,
    # Recall-oriented: the symbol channel only proposes candidates for RRF; a
    # single-char misspelling ("slugfy"~"slugify") scores ~0.3, and downstream
    # fusion + reranking enforce precision.
    min_similarity: float = 0.3,
) -> list[str]:
    """Return chunk ids ranked by best symbol-match score for the query's identifiers."""
    identifiers = extract_identifiers(query)
    if not identifiers:
        return []

    best: dict[str, float] = {}
    async with session_factory() as session:
        for term in identifiers:
            for chunk_id, score in await _search_term(
                session, snapshot_id, term, min_sim=min_similarity, limit=limit
            ):
                if score > best.get(chunk_id, 0.0):
                    best[chunk_id] = score

    ranked = sorted(best, key=lambda cid: best[cid], reverse=True)[:limit]
    logger.info("symbol channel", identifiers=identifiers, hits=len(ranked))
    return ranked
