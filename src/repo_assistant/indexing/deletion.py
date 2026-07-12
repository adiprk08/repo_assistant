"""Repository deletion: remove a repo's rows and vector points.

Postgres rows are deleted first inside one transaction; vector points are removed
best-effort afterwards. If the vector delete fails, the orphaned points are
invisible (every query filters by repo_id) and only waste space, whereas the
reverse order could leave a live repo whose vectors are gone.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.interfaces import VectorIndex
from repo_assistant.core.logging import get_logger
from repo_assistant.storage import repositories as repo

logger = get_logger(__name__)


async def delete_repository(
    repo_id: uuid.UUID,
    *,
    vector_index: VectorIndex,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """Delete a repo, all its snapshots' rows, and its vector points.

    Returns False if the repo does not exist.
    """
    async with session_factory() as session:
        snapshot_ids = await repo.snapshot_ids_for_repo(session, repo_id)
        chunk_ids = await repo.chunk_ids_for_snapshots(session, snapshot_ids)
        deleted = await repo.delete_repo_rows(session, repo_id)
        if not deleted:
            return False
        await session.commit()

    try:
        await vector_index.delete(repo_id=str(repo_id), ids=[str(c) for c in chunk_ids])
    except Exception:  # noqa: BLE001 - orphaned points are harmless; rows are gone
        logger.warning("vector delete failed; orphaned points remain", repo_id=str(repo_id))
    logger.info(
        "repository deleted",
        repo_id=str(repo_id),
        snapshots=len(snapshot_ids),
        chunks=len(chunk_ids),
    )
    return True
