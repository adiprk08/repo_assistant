"""Async data-access helpers for the relational store.

Thin functions over the ORM models, grouped by aggregate. Pipeline code depends
on these rather than issuing queries inline, so tenancy/snapshot scoping stays in
one place (docs/adr/0009-multitenancy-and-versioning.md).
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from repo_assistant.storage.models import Chunk, File, Repo, Snapshot, Symbol


async def get_repo_by_url(session: AsyncSession, url: str) -> Repo | None:
    result = await session.execute(select(Repo).where(Repo.url == url))
    return result.scalar_one_or_none()


async def create_or_get_repo(session: AsyncSession, url: str, default_ref: str) -> Repo:
    existing = await get_repo_by_url(session, url)
    if existing is not None:
        return existing
    repo = Repo(url=url, default_ref=default_ref, status="pending")
    session.add(repo)
    await session.flush()
    return repo


async def create_snapshot(session: AsyncSession, repo_id: uuid.UUID, commit_sha: str) -> Snapshot:
    snapshot = Snapshot(repo_id=repo_id, commit_sha=commit_sha, status="indexing")
    session.add(snapshot)
    await session.flush()
    return snapshot


async def add_files(session: AsyncSession, snapshot_id: uuid.UUID, files: list[dict]) -> None:
    session.add_all([File(snapshot_id=snapshot_id, **f) for f in files])


async def add_symbols(session: AsyncSession, snapshot_id: uuid.UUID, symbols: list[dict]) -> None:
    session.add_all([Symbol(snapshot_id=snapshot_id, **s) for s in symbols])


async def add_chunks(session: AsyncSession, chunks: list[dict]) -> None:
    session.add_all([Chunk(**c) for c in chunks])


async def finalize_snapshot(
    session: AsyncSession, repo_id: uuid.UUID, snapshot_id: uuid.UUID, stats: dict
) -> None:
    """Mark a snapshot READY and atomically promote it to the repo's active one."""
    await session.execute(
        update(Snapshot)
        .where(Snapshot.id == snapshot_id)
        .values(status="ready", stats=stats, indexed_at=_now())
    )
    await session.execute(
        update(Repo)
        .where(Repo.id == repo_id)
        .values(status="ready", active_snapshot_id=snapshot_id)
    )


async def get_active_snapshot(session: AsyncSession, repo_id: uuid.UUID) -> Snapshot | None:
    repo = await session.get(Repo, repo_id)
    if repo is None or repo.active_snapshot_id is None:
        return None
    return await session.get(Snapshot, repo.active_snapshot_id)


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=None)
