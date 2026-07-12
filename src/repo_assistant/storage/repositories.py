"""Async data-access helpers for the relational store.

Thin functions over the ORM models, grouped by aggregate. Pipeline code depends
on these rather than issuing queries inline, so tenancy/snapshot scoping stays in
one place (docs/adr/0009-multitenancy-and-versioning.md).
"""

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from repo_assistant.storage.models import (
    ApiKey,
    ChatMessage,
    ChatSession,
    Chunk,
    Edge,
    File,
    Job,
    Repo,
    Snapshot,
    Symbol,
)


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


async def list_repos(session: AsyncSession) -> list[Repo]:
    result = await session.execute(select(Repo).order_by(Repo.created_at))
    return list(result.scalars())


async def set_repo_status(session: AsyncSession, repo_id: uuid.UUID, status: str) -> None:
    await session.execute(update(Repo).where(Repo.id == repo_id).values(status=status))


async def create_job(
    session: AsyncSession,
    repo_id: uuid.UUID,
    *,
    job_type: str = "ingestion",
    params: dict | None = None,
) -> Job:
    job = Job(repo_id=repo_id, job_type=job_type, params=params or {})
    session.add(job)
    await session.flush()
    return job


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    return await session.get(Job, job_id)


async def latest_job_for_repo(session: AsyncSession, repo_id: uuid.UUID) -> Job | None:
    result = await session.execute(
        select(Job).where(Job.repo_id == repo_id).order_by(Job.created_at.desc()).limit(1)
    )
    return result.scalars().first()


async def update_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    stage: str | None = None,
    state: str | None = None,
    progress: dict | None = None,
    error: str | None = None,
) -> None:
    """Patch a job row. ``progress`` keys are merged into the existing JSONB dict."""
    job = await session.get(Job, job_id)
    if job is None:
        return
    if stage is not None:
        job.stage = stage
    if state is not None:
        job.state = state
    if progress:
        job.progress = {**job.progress, **progress}
    if error is not None:
        job.error = error


async def snapshot_ids_for_repo(session: AsyncSession, repo_id: uuid.UUID) -> list[uuid.UUID]:
    result = await session.execute(select(Snapshot.id).where(Snapshot.repo_id == repo_id))
    return list(result.scalars())


async def chunk_ids_for_snapshots(
    session: AsyncSession, snapshot_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    if not snapshot_ids:
        return []
    result = await session.execute(select(Chunk.id).where(Chunk.snapshot_id.in_(snapshot_ids)))
    return list(result.scalars())


async def delete_repo_rows(session: AsyncSession, repo_id: uuid.UUID) -> bool:
    """Delete a repo and every dependent row. Returns False if the repo doesn't exist.

    Vector points are not touched here — the caller owns cross-store deletion
    (see indexing/deletion.py).
    """
    repo = await session.get(Repo, repo_id)
    if repo is None:
        return False
    snapshot_ids = await snapshot_ids_for_repo(session, repo_id)
    # Break the repos -> snapshots FK cycle before deleting snapshots.
    await session.execute(update(Repo).where(Repo.id == repo_id).values(active_snapshot_id=None))
    # Chat messages -> sessions -> (repo, snapshot): delete children first.
    session_ids = (
        (await session.execute(select(ChatSession.id).where(ChatSession.repo_id == repo_id)))
        .scalars()
        .all()
    )
    if session_ids:
        await session.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(session_ids)))
    await session.execute(delete(ChatSession).where(ChatSession.repo_id == repo_id))
    for model in (Chunk, Symbol, Edge, File):
        if snapshot_ids:
            await session.execute(delete(model).where(model.snapshot_id.in_(snapshot_ids)))
    await session.execute(delete(Job).where(Job.repo_id == repo_id))
    await session.execute(delete(Snapshot).where(Snapshot.repo_id == repo_id))
    await session.execute(delete(Repo).where(Repo.id == repo_id))
    return True


async def create_session(
    session: AsyncSession,
    *,
    repo_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    commit_sha: str,
    title: str | None = None,
) -> ChatSession:
    chat = ChatSession(repo_id=repo_id, snapshot_id=snapshot_id, commit_sha=commit_sha, title=title)
    session.add(chat)
    await session.flush()
    return chat


async def get_session(session: AsyncSession, session_id: uuid.UUID) -> ChatSession | None:
    return await session.get(ChatSession, session_id)


async def list_sessions_for_repo(session: AsyncSession, repo_id: uuid.UUID) -> list[ChatSession]:
    result = await session.execute(
        select(ChatSession)
        .where(ChatSession.repo_id == repo_id)
        .order_by(ChatSession.created_at.desc())
    )
    return list(result.scalars())


async def append_message(
    session: AsyncSession,
    session_id: uuid.UUID,
    *,
    role: str,
    content: str,
    citations: list | None = None,
    usage: dict | None = None,
) -> ChatMessage:
    message = ChatMessage(
        session_id=session_id,
        role=role,
        content=content,
        citations=citations or [],
        usage=usage or {},
    )
    session.add(message)
    # Touch the parent session so ``updated_at`` reflects the latest activity.
    await session.execute(
        update(ChatSession).where(ChatSession.id == session_id).values(updated_at=_now())
    )
    await session.flush()
    return message


async def get_messages(session: AsyncSession, session_id: uuid.UUID) -> list[ChatMessage]:
    """All messages for a session, oldest first."""
    result = await session.execute(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.seq)
    )
    return list(result.scalars())


async def update_session_summary(
    session: AsyncSession, session_id: uuid.UUID, summary: str, covered_messages: int
) -> None:
    await session.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(summary=summary, summary_covered_messages=covered_messages)
    )


async def create_api_key(
    session: AsyncSession, *, name: str, key_prefix: str, key_hash: str
) -> ApiKey:
    api_key = ApiKey(name=name, key_prefix=key_prefix, key_hash=key_hash)
    session.add(api_key)
    await session.flush()
    return api_key


async def get_api_key_by_hash(session: AsyncSession, key_hash: str) -> ApiKey | None:
    result = await session.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    return result.scalar_one_or_none()


async def list_api_keys(session: AsyncSession) -> list[ApiKey]:
    result = await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    return list(result.scalars())


async def touch_api_key(session: AsyncSession, key_id: uuid.UUID) -> None:
    await session.execute(update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=_now()))


async def revoke_api_key(session: AsyncSession, key_id: uuid.UUID) -> bool:
    """Mark a key revoked. Returns False if it doesn't exist or was already revoked."""
    api_key = await session.get(ApiKey, key_id)
    if api_key is None or api_key.revoked_at is not None:
        return False
    api_key.revoked_at = _now()
    return True


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=None)
