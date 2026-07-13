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
    GithubInstallation,
    Job,
    Repo,
    Snapshot,
    Symbol,
    User,
    UserRepo,
    WebSession,
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


async def set_repo_installation(
    session: AsyncSession, repo_id: uuid.UUID, installation_id: int
) -> None:
    """Mark a repo private and bind it to the GitHub App installation that can read it."""
    await session.execute(
        update(Repo)
        .where(Repo.id == repo_id)
        .values(visibility="private", installation_id=installation_id)
    )


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


async def file_hashes_for_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> dict[str, str]:
    """Map path -> content_hash for a snapshot's indexed files (the incremental diff key)."""
    result = await session.execute(
        select(File.path, File.content_hash).where(File.snapshot_id == snapshot_id)
    )
    return {path: h for path, h in result.all()}


async def files_for_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> list[File]:
    result = await session.execute(select(File).where(File.snapshot_id == snapshot_id))
    return list(result.scalars())


async def chunks_for_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> list[Chunk]:
    result = await session.execute(select(Chunk).where(Chunk.snapshot_id == snapshot_id))
    return list(result.scalars())


async def symbols_for_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> list[Symbol]:
    result = await session.execute(select(Symbol).where(Symbol.snapshot_id == snapshot_id))
    return list(result.scalars())


async def edges_for_snapshot(session: AsyncSession, snapshot_id: uuid.UUID) -> list[Edge]:
    result = await session.execute(select(Edge).where(Edge.snapshot_id == snapshot_id))
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
    user_id: uuid.UUID | None = None,
) -> ChatSession:
    chat = ChatSession(
        repo_id=repo_id,
        user_id=user_id,
        snapshot_id=snapshot_id,
        commit_sha=commit_sha,
        title=title,
    )
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


async def list_sessions_for_user_repo(
    session: AsyncSession, user_id: uuid.UUID, repo_id: uuid.UUID
) -> list[ChatSession]:
    """A user's own sessions on a repo — sessions are personal (docs/adr/0023)."""
    result = await session.execute(
        select(ChatSession)
        .where(ChatSession.repo_id == repo_id, ChatSession.user_id == user_id)
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
    session: AsyncSession,
    *,
    name: str,
    key_prefix: str,
    key_hash: str,
    user_id: uuid.UUID | None = None,
) -> ApiKey:
    api_key = ApiKey(name=name, key_prefix=key_prefix, key_hash=key_hash, user_id=user_id)
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


async def touch_api_key_by_hash(session: AsyncSession, key_hash: str) -> None:
    await session.execute(
        update(ApiKey).where(ApiKey.key_hash == key_hash).values(last_used_at=_now())
    )


async def revoke_api_key(session: AsyncSession, key_id: uuid.UUID) -> bool:
    """Mark a key revoked. Returns False if it doesn't exist or was already revoked."""
    api_key = await session.get(ApiKey, key_id)
    if api_key is None or api_key.revoked_at is not None:
        return False
    api_key.revoked_at = _now()
    return True


async def get_installation(
    session: AsyncSession, installation_id: int
) -> GithubInstallation | None:
    result = await session.execute(
        select(GithubInstallation).where(GithubInstallation.installation_id == installation_id)
    )
    return result.scalar_one_or_none()


async def upsert_installation_token(
    session: AsyncSession,
    installation_id: int,
    *,
    account_login: str | None,
    token_encrypted: str,
    token_expires_at,
) -> None:
    """Insert or refresh the cached (encrypted) token for an installation."""
    row = await get_installation(session, installation_id)
    if row is None:
        session.add(
            GithubInstallation(
                installation_id=installation_id,
                account_login=account_login,
                token_encrypted=token_encrypted,
                token_expires_at=token_expires_at,
            )
        )
        return
    row.token_encrypted = token_encrypted
    row.token_expires_at = token_expires_at
    if account_login:
        row.account_login = account_login


# --- users, sessions, and per-user repo library (docs/adr/0023) --------------

_LOCAL_USER_LOGIN = "local"


async def get_user_by_github_id(session: AsyncSession, github_id: int) -> User | None:
    result = await session.execute(select(User).where(User.github_id == github_id))
    return result.scalar_one_or_none()


async def get_user_by_login(session: AsyncSession, login: str) -> User | None:
    result = await session.execute(select(User).where(User.login == login))
    return result.scalar_one_or_none()


async def upsert_github_user(
    session: AsyncSession,
    *,
    github_id: int,
    login: str,
    name: str | None,
    avatar_url: str | None,
) -> User:
    """Create or refresh the user backing a GitHub identity (login/name/avatar can
    change upstream, so keep them current on every sign-in)."""
    user = await get_user_by_github_id(session, github_id)
    if user is None:
        user = User(github_id=github_id, login=login, name=name, avatar_url=avatar_url)
        session.add(user)
        await session.flush()
        return user
    user.login = login
    user.name = name
    user.avatar_url = avatar_url
    return user


async def get_or_create_local_user(session: AsyncSession) -> User:
    """The ``local`` user: owner of CLI-minted keys and of everything in a
    ``require_auth``-off dev instance (docs/adr/0023)."""
    user = await get_user_by_login(session, _LOCAL_USER_LOGIN)
    if user is None:
        user = User(login=_LOCAL_USER_LOGIN, name="Local")
        session.add(user)
        await session.flush()
    return user


async def create_web_session(
    session: AsyncSession, *, user_id: uuid.UUID, token_hash: str, expires_at
) -> WebSession:
    web = WebSession(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    session.add(web)
    await session.flush()
    return web


async def get_user_for_session_token(session: AsyncSession, token_hash: str) -> User | None:
    """Resolve a live (unexpired) browser session's user, or None."""
    result = await session.execute(
        select(User)
        .join(WebSession, WebSession.user_id == User.id)
        .where(WebSession.token_hash == token_hash, WebSession.expires_at > _now())
    )
    return result.scalar_one_or_none()


async def delete_web_session(session: AsyncSession, token_hash: str) -> None:
    await session.execute(delete(WebSession).where(WebSession.token_hash == token_hash))


async def user_for_api_key_hash(session: AsyncSession, key_hash: str) -> User | None:
    """The user owning a (non-revoked) API key, or None if the key is unknown,
    revoked, or unlinked to a user."""
    result = await session.execute(
        select(User)
        .join(ApiKey, ApiKey.user_id == User.id)
        .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
    )
    return result.scalar_one_or_none()


async def add_user_repo(session: AsyncSession, user_id: uuid.UUID, repo_id: uuid.UUID) -> None:
    """Add a repo to a user's library (idempotent)."""
    if await is_repo_member(session, user_id, repo_id):
        return
    session.add(UserRepo(user_id=user_id, repo_id=repo_id))
    await session.flush()


async def remove_user_repo(session: AsyncSession, user_id: uuid.UUID, repo_id: uuid.UUID) -> bool:
    if not await is_repo_member(session, user_id, repo_id):
        return False
    await session.execute(
        delete(UserRepo).where(UserRepo.user_id == user_id, UserRepo.repo_id == repo_id)
    )
    return True


async def is_repo_member(session: AsyncSession, user_id: uuid.UUID, repo_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(UserRepo.repo_id).where(UserRepo.user_id == user_id, UserRepo.repo_id == repo_id)
    )
    return result.first() is not None


async def repo_member_count(session: AsyncSession, repo_id: uuid.UUID) -> int:
    result = await session.execute(select(UserRepo.user_id).where(UserRepo.repo_id == repo_id))
    return len(result.all())


async def list_repos_for_user(session: AsyncSession, user_id: uuid.UUID) -> list[Repo]:
    result = await session.execute(
        select(Repo)
        .join(UserRepo, UserRepo.repo_id == Repo.id)
        .where(UserRepo.user_id == user_id)
        .order_by(Repo.created_at)
    )
    return list(result.scalars())


def _now():
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(tzinfo=None)
