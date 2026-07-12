"""The ingestion job: one arq task driving the indexing pipeline for one jobs row.

The task is a thin shell (CLAUDE.md): all pipeline logic lives in
``indexing.pipeline``; this wrapper owns the job-row state machine — stage and
progress persisted at every transition so the API's SSE endpoint streams them
live (docs/adr/0014). Failure marks the job and repo failed and re-raises so arq
records the outcome; a re-enqueued job re-runs safely (content-hash embedding
cache, atomic snapshot promotion).
"""

import uuid
from typing import Any

from repo_assistant.cli.runtime import Runtime
from repo_assistant.core.logging import get_logger
from repo_assistant.indexing.pipeline import index_repository
from repo_assistant.storage import repositories as repo

logger = get_logger(__name__)


async def _patch_job(
    runtime: Runtime,
    job_id: uuid.UUID,
    *,
    stage: str | None = None,
    state: str | None = None,
    progress: dict | None = None,
    error: str | None = None,
) -> None:
    async with runtime.session_factory() as session:
        await repo.update_job(
            session, job_id, stage=stage, state=state, progress=progress, error=error
        )
        await session.commit()


async def run_ingestion(ctx: dict[str, Any], job_id: str) -> None:
    runtime: Runtime = ctx["runtime"]
    jid = uuid.UUID(job_id)

    async with runtime.session_factory() as session:
        job = await repo.get_job(session, jid)
        if job is None:
            logger.warning("ingestion job not found; dropping", job_id=job_id)
            return
        repo_id, params = job.repo_id, dict(job.params)
        await repo.update_job(session, jid, state="running")
        await repo.set_repo_status(session, repo_id, "indexing")
        await session.commit()

    url = params.get("url")
    if not url:
        await _patch_job(runtime, jid, state="failed", error="job params missing 'url'")
        return

    async def on_stage(stage: str, progress: dict[str, Any]) -> None:
        await _patch_job(runtime, jid, stage=stage, progress=progress)

    enricher = (
        runtime.llm(model=runtime.settings.enrichment_model) if params.get("enrich") else None
    )
    try:
        result = await index_repository(
            url,
            embedder=runtime.embedder(),
            vector_index=runtime.vector_index,
            session_factory=runtime.session_factory,
            ref=params.get("ref"),
            enricher=enricher,
            on_stage=on_stage,
        )
    except Exception as exc:
        await _patch_job(runtime, jid, state="failed", error=str(exc))
        async with runtime.session_factory() as session:
            await repo.set_repo_status(session, repo_id, "failed")
            await session.commit()
        raise

    # finalize_snapshot already flipped the repo to ready; close out the job row.
    await _patch_job(
        runtime,
        jid,
        stage="ready",
        state="succeeded",
        progress={
            "commit": result.commit_sha,
            "snapshot_id": str(result.snapshot_id),
            "files": result.n_files,
            "chunks": result.n_chunks,
            "symbols": result.n_symbols,
        },
    )
    logger.info("ingestion job complete", job_id=job_id, commit=result.commit_sha)


async def run_update(ctx: dict[str, Any], job_id: str) -> None:
    """Incremental re-index job (docs/adr/0018): touches only changed files."""
    from repo_assistant.indexing.incremental import incremental_index

    runtime: Runtime = ctx["runtime"]
    jid = uuid.UUID(job_id)

    async with runtime.session_factory() as session:
        job = await repo.get_job(session, jid)
        if job is None:
            logger.warning("update job not found; dropping", job_id=job_id)
            return
        repo_id, params = job.repo_id, dict(job.params)
        await repo.update_job(session, jid, state="running")
        await repo.set_repo_status(session, repo_id, "indexing")
        await session.commit()

    url = params.get("url")
    if not url:
        await _patch_job(runtime, jid, state="failed", error="job params missing 'url'")
        return

    async def on_stage(stage: str, progress: dict[str, Any]) -> None:
        await _patch_job(runtime, jid, stage=stage, progress=progress)

    enricher = (
        runtime.llm(model=runtime.settings.enrichment_model) if params.get("enrich") else None
    )
    try:
        result = await incremental_index(
            url,
            embedder=runtime.embedder(),
            vector_index=runtime.vector_index,
            session_factory=runtime.session_factory,
            ref=params.get("ref"),
            enricher=enricher,
            on_stage=on_stage,
        )
    except Exception as exc:
        await _patch_job(runtime, jid, state="failed", error=str(exc))
        async with runtime.session_factory() as session:
            # An update failure leaves the previous active snapshot intact; the repo
            # was already ready, so mark it ready again rather than failed.
            await repo.set_repo_status(session, repo_id, "ready")
            await session.commit()
        raise

    # Always land ready: finalize promotes the new snapshot; a no-op left the
    # previous one active but we flipped status to "indexing" at the start.
    async with runtime.session_factory() as session:
        await repo.set_repo_status(session, repo_id, "ready")
        await session.commit()
    await _patch_job(
        runtime,
        jid,
        stage="ready",
        state="succeeded",
        progress={
            "commit": result.commit_sha,
            "snapshot_id": str(result.snapshot_id),
            "reprocessed": result.n_reprocessed,
            "unchanged": result.n_unchanged,
            "deleted": result.n_deleted,
            "no_op": result.no_op,
        },
    )
    logger.info("update job complete", job_id=job_id, commit=result.commit_sha, no_op=result.no_op)
