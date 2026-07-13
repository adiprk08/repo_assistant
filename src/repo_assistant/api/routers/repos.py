"""Repository lifecycle: register (+ enqueue ingestion), list, inspect, delete,
and stream ingestion-job progress over SSE.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from repo_assistant.api.auth import CurrentUser
from repo_assistant.api.deps import QueueDep, RuntimeDep
from repo_assistant.api.schemas import (
    JobOut,
    RepoCreate,
    RepoDetailOut,
    RepoOut,
    RepoRegistered,
    SnapshotOut,
)
from repo_assistant.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_event
from repo_assistant.core.errors import NotFoundError
from repo_assistant.indexing.deletion import delete_repository
from repo_assistant.ingestion.git import normalize_github_url
from repo_assistant.storage import repositories as repo

router = APIRouter(prefix="/repos", tags=["repos"])

# Job states that mean the ingestion is over — the SSE stream closes on these.
_TERMINAL_STATES = frozenset({"succeeded", "failed"})


async def _require_member(session, user, repo_id: uuid.UUID) -> None:
    """Guard a repo-scoped route by library membership. Denials read as 404 (not
    403) so the API never reveals that a repo the caller can't see exists."""
    if not await repo.is_repo_member(session, user.id, repo_id):
        raise NotFoundError(f"No repository {repo_id}")


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def register_repo(
    body: RepoCreate, runtime: RuntimeDep, queue: QueueDep, user: CurrentUser
) -> RepoRegistered:
    """Register a repo and enqueue an ingestion job. Returns the job to watch.

    Idempotent on URL: re-posting an existing repo adds it to the caller's library
    and queues a fresh (re-)index, which the content-hash embedding cache makes
    cheap — an already-indexed repo is added to the library instantly.
    """
    url = normalize_github_url(body.url)  # IngestionError -> 400 for a bad URL
    async with runtime.session_factory() as session:
        repo_row = await repo.create_or_get_repo(session, url, body.ref or "main")
        await repo.add_user_repo(session, user.id, repo_row.id)
        if body.installation_id is not None:
            # Private repo: bind it to the GitHub App installation (docs/adr/0020).
            await repo.set_repo_installation(session, repo_row.id, body.installation_id)
        job = await repo.create_job(
            session,
            repo_row.id,
            params={"url": url, "ref": body.ref, "enrich": body.enrich},
        )
        await repo.set_repo_status(session, repo_row.id, "pending")
        await session.commit()
        await session.refresh(repo_row)
        registered = RepoRegistered(
            repo=RepoOut.model_validate(repo_row), job=JobOut.model_validate(job)
        )

    await queue.enqueue(job.id)  # ProviderError -> 502 if Redis is down
    return registered


@router.get("")
async def list_repos(runtime: RuntimeDep, user: CurrentUser) -> list[RepoOut]:
    """The caller's library — the repos they have added (docs/adr/0023)."""
    async with runtime.session_factory() as session:
        rows = await repo.list_repos_for_user(session, user.id)
        return [RepoOut.model_validate(r) for r in rows]


@router.get("/{repo_id}")
async def get_repo(repo_id: uuid.UUID, runtime: RuntimeDep, user: CurrentUser) -> RepoDetailOut:
    async with runtime.session_factory() as session:
        await _require_member(session, user, repo_id)
        repo_row = await session.get(repo.Repo, repo_id)
        if repo_row is None:
            raise NotFoundError(f"No repository {repo_id}")
        snapshot = await repo.get_active_snapshot(session, repo_id)
        job = await repo.latest_job_for_repo(session, repo_id)
        return RepoDetailOut(
            **RepoOut.model_validate(repo_row).model_dump(),
            active_snapshot=SnapshotOut.model_validate(snapshot) if snapshot else None,
            latest_job=JobOut.model_validate(job) if job else None,
        )


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repo(repo_id: uuid.UUID, runtime: RuntimeDep, user: CurrentUser) -> None:
    """Remove the repo from the caller's library. The shared index is torn down
    only when the last member leaves (docs/adr/0023)."""
    async with runtime.session_factory() as session:
        await _require_member(session, user, repo_id)
        await repo.remove_user_repo(session, user.id, repo_id)
        remaining = await repo.repo_member_count(session, repo_id)
        await session.commit()

    if remaining == 0:
        await delete_repository(
            repo_id, vector_index=runtime.vector_index, session_factory=runtime.session_factory
        )


@router.get("/{repo_id}/job")
async def get_repo_job(repo_id: uuid.UUID, runtime: RuntimeDep, user: CurrentUser) -> JobOut:
    """The latest ingestion job for the repo (poll this, or stream ``/job/stream``)."""
    async with runtime.session_factory() as session:
        await _require_member(session, user, repo_id)
        job = await repo.latest_job_for_repo(session, repo_id)
        if job is None:
            raise NotFoundError(f"No ingestion job for repository {repo_id}")
        return JobOut.model_validate(job)


@router.get("/{repo_id}/job/stream")
async def stream_repo_job(
    repo_id: uuid.UUID, runtime: RuntimeDep, user: CurrentUser
) -> StreamingResponse:
    """Stream the latest ingestion job's stage/progress until it reaches a terminal state.

    Polls the jobs row (the worker persists each stage transition) and emits a
    ``progress`` event on every change, then a final ``done`` event. Polling — not
    a Redis pub/sub — keeps the API decoupled from the worker's transport and
    survives worker restarts.
    """
    # Guard before streaming so a non-member is a real 404, not an SSE error frame.
    async with runtime.session_factory() as session:
        await _require_member(session, user, repo_id)
    poll = runtime.settings.job_stream_poll_seconds

    async def events() -> AsyncIterator[str]:
        last_signature: tuple | None = None
        while True:
            async with runtime.session_factory() as session:
                job = await repo.latest_job_for_repo(session, repo_id)
                out = JobOut.model_validate(job) if job else None
            if out is None:
                yield sse_event("error", {"detail": f"No ingestion job for repository {repo_id}"})
                return
            signature = (out.stage, out.state, tuple(sorted(out.progress.items())))
            if signature != last_signature:
                last_signature = signature
                yield sse_event("progress", out.model_dump())
            if out.state in _TERMINAL_STATES:
                yield sse_event("done", {"state": out.state, "error": out.error})
                return
            await asyncio.sleep(poll)

    return StreamingResponse(events(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)
