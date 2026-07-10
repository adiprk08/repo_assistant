"""Graph retrieval channel (docs/ARCHITECTURE.md §5, adr/0005).

Resolves query identifiers to symbols, walks one hop in the code graph (callers
and callees / container and contained), and maps those neighbor symbols to the
chunks that contain them. This surfaces evidence a keyword/vector query would
miss — e.g. the caller of the function the user named — for trace-style questions.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.identifiers import extract_identifiers
from repo_assistant.core.logging import get_logger

logger = get_logger(__name__)

# For symbols matching a query identifier, gather 1-hop neighbors in both
# directions, then map each neighbor to the chunk that contains it.
_QUERY = text(
    """
    WITH matched AS (
        SELECT qualified_name FROM symbols
        WHERE snapshot_id = :snapshot_id AND lower(name) = lower(:term)
    ),
    neighbors AS (
        SELECT e.dst AS qn, e.confidence AS conf
        FROM edges e JOIN matched m ON e.src = m.qualified_name
        WHERE e.snapshot_id = :snapshot_id
        UNION ALL
        SELECT e.src AS qn, e.confidence AS conf
        FROM edges e JOIN matched m ON e.dst = m.qualified_name
        WHERE e.snapshot_id = :snapshot_id
    )
    SELECT c.id::text AS chunk_id, max(n.conf) AS score
    FROM neighbors n
    JOIN symbols s
      ON s.snapshot_id = :snapshot_id AND s.qualified_name = n.qn
    JOIN chunks c
      ON c.snapshot_id = :snapshot_id
     AND c.file_path = s.file_path
     AND c.start_line <= s.start_line
     AND c.end_line >= s.start_line
    GROUP BY c.id
    ORDER BY score DESC
    LIMIT :limit
    """
)


async def _search_term(
    session: AsyncSession, snapshot_id: str, term: str, limit: int
) -> list[tuple[str, float]]:
    rows = await session.execute(_QUERY, {"snapshot_id": snapshot_id, "term": term, "limit": limit})
    return [(row.chunk_id, float(row.score)) for row in rows]


async def graph_search(
    session_factory: async_sessionmaker[AsyncSession],
    snapshot_id: str,
    query: str,
    *,
    limit: int = 15,
) -> list[str]:
    """Return chunk ids for the 1-hop graph neighbors of query-named symbols."""
    identifiers = extract_identifiers(query)
    if not identifiers:
        return []

    best: dict[str, float] = {}
    async with session_factory() as session:
        for term in identifiers:
            for chunk_id, score in await _search_term(session, snapshot_id, term, limit):
                if score > best.get(chunk_id, 0.0):
                    best[chunk_id] = score

    ranked = sorted(best, key=lambda cid: best[cid], reverse=True)[:limit]
    logger.info("graph channel", identifiers=identifiers, hits=len(ranked))
    return ranked
