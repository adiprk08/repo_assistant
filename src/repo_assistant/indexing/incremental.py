"""Incremental re-indexing: update a repo touching only the changed files.

Diff is by content hash against the previous active snapshot (docs/adr/0018): a
file whose ``sha256`` is unchanged is copied forward into a new snapshot (rows via
SQL, Qdrant points via ``copy_points`` — no re-embedding); only changed/added
files are re-scanned, parsed, chunked, and embedded. The new snapshot is promoted
atomically, preserving ADR-0009.
"""

import tempfile
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.errors import IndexingError
from repo_assistant.core.interfaces import Embedder, LLMClient, VectorIndex
from repo_assistant.core.logging import get_logger
from repo_assistant.graph.extract import extract_edges
from repo_assistant.indexing.enrichment import enrich_chunks
from repo_assistant.indexing.pipeline import (
    OnStage,
    _build_units,
    _notify,
    _point_id,
    _vector_points,
    make_session_factory_from_settings,
)
from repo_assistant.ingestion import clone, scan
from repo_assistant.ingestion.models import Acquisition, ScannedFile
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.models import Edge as EdgeModel
from repo_assistant.storage.models import Symbol as SymbolRow

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    unchanged: list[str]  # paths carried forward untouched
    to_process: list[ScannedFile]  # changed + added -> reprocessed
    deleted: list[str]  # paths gone in the new tree


@dataclass(frozen=True, slots=True)
class IncrementalResult:
    repo_id: uuid.UUID
    snapshot_id: uuid.UUID
    commit_sha: str
    n_reprocessed: int
    n_unchanged: int
    n_deleted: int
    no_op: bool


def plan_update(scanned: list[ScannedFile], prev_hashes: dict[str, str]) -> UpdatePlan:
    """Partition the new scan against the previous snapshot's file hashes."""
    unchanged: list[str] = []
    to_process: list[ScannedFile] = []
    seen: set[str] = set()
    for f in scanned:
        seen.add(f.path)
        if prev_hashes.get(f.path) == f.content_hash:
            unchanged.append(f.path)
        else:
            to_process.append(f)
    deleted = [path for path in prev_hashes if path not in seen]
    return UpdatePlan(unchanged=unchanged, to_process=to_process, deleted=deleted)


async def incremental_index(
    url: str,
    *,
    embedder: Embedder,
    vector_index: VectorIndex,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    ref: str | None = None,
    workdir: str | None = None,
    enricher: LLMClient | None = None,
    on_stage: OnStage | None = None,
) -> IncrementalResult:
    """Clone ``url`` at its latest ref and apply an incremental update."""
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        await _notify(on_stage, "cloning", url=url, ref=ref)
        acquisition = await clone(url, tmp, ref=ref)
        return await update_working_tree(
            acquisition,
            embedder=embedder,
            vector_index=vector_index,
            session_factory=session_factory,
            enricher=enricher,
            on_stage=on_stage,
        )


async def update_working_tree(
    acquisition: Acquisition,
    *,
    embedder: Embedder,
    vector_index: VectorIndex,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    enricher: LLMClient | None = None,
    on_stage: OnStage | None = None,
) -> IncrementalResult:
    session_factory = session_factory or make_session_factory_from_settings()

    async with session_factory() as session:
        repo_row = await repo.get_repo_by_url(session, acquisition.url)
        if repo_row is None:
            raise IndexingError(f"{acquisition.url} is not indexed; run a full index first.")
        prev = await repo.get_active_snapshot(session, repo_row.id)
        if prev is None:
            raise IndexingError(
                f"{acquisition.url} has no active snapshot; run a full index first."
            )
        repo_id = repo_row.id
        prev_snapshot_id = prev.id
        prev_commit = prev.commit_sha
        prev_hashes = await repo.file_hashes_for_snapshot(session, prev_snapshot_id)

    if acquisition.commit_sha == prev_commit:
        logger.info("incremental no-op: commit unchanged", commit=prev_commit)
        return IncrementalResult(
            repo_id=repo_id,
            snapshot_id=prev_snapshot_id,
            commit_sha=prev_commit,
            n_reprocessed=0,
            n_unchanged=len(prev_hashes),
            n_deleted=0,
            no_op=True,
        )

    await _notify(on_stage, "scanning")
    scan_result = await scan(acquisition)
    plan = plan_update(scan_result.files, prev_hashes)
    logger.info(
        "incremental plan",
        reprocess=len(plan.to_process),
        unchanged=len(plan.unchanged),
        deleted=len(plan.deleted),
    )

    # Reprocess only the changed/added files.
    await _notify(on_stage, "parsing", files=len(plan.to_process))
    chunks, symbol_rows, contexts = await _build_units(acquisition, plan.to_process)
    edges = extract_edges(contexts)
    if enricher is not None and chunks:
        await _notify(on_stage, "enriching", chunks=len(chunks))
        chunks = await enrich_chunks(enricher, chunks)

    await _notify(on_stage, "embedding", chunks=len(chunks))
    await vector_index.prepare(embedder.dimensions)
    vectors = (
        await embedder.embed([c.embed_text for c in chunks], input_type="document")
        if chunks
        else []
    )

    async with session_factory() as session:
        snapshot = await repo.create_snapshot(session, repo_id, acquisition.commit_sha)
        await session.commit()
        new_snapshot_id = snapshot.id

    await _notify(
        on_stage, "indexing", reprocess=len(plan.to_process), unchanged=len(plan.unchanged)
    )
    points, chunk_rows = _vector_points(
        chunks,
        vectors,
        snapshot_id=new_snapshot_id,
        repo_id=str(repo_id),
        commit=acquisition.commit_sha,
    )
    await vector_index.upsert(points)

    async with session_factory() as session:
        await repo.add_files(
            session,
            new_snapshot_id,
            [
                {
                    "path": f.path,
                    "language": f.language,
                    "size_bytes": f.size_bytes,
                    "content_hash": f.content_hash,
                }
                for f in plan.to_process
            ],
        )
        if symbol_rows:
            session.add_all([SymbolRow(snapshot_id=new_snapshot_id, **s) for s in symbol_rows])
        if edges:
            session.add_all(
                [
                    EdgeModel(
                        snapshot_id=new_snapshot_id,
                        src=e.src,
                        dst=e.dst,
                        kind=e.kind,
                        confidence=e.confidence,
                        src_file=e.src_file,
                    )
                    for e in edges
                ]
            )
        if chunk_rows:
            await repo.add_chunks(session, chunk_rows)
        await session.commit()

    # Carry unchanged files forward into the new snapshot.
    await _copy_unchanged(
        session_factory,
        vector_index,
        prev_snapshot_id=prev_snapshot_id,
        new_snapshot_id=new_snapshot_id,
        repo_id=str(repo_id),
        new_commit=acquisition.commit_sha,
        unchanged=set(plan.unchanged),
    )

    stats = {
        "files": len(plan.to_process) + len(plan.unchanged),
        "reprocessed": len(plan.to_process),
        "unchanged": len(plan.unchanged),
        "deleted": len(plan.deleted),
    }
    async with session_factory() as session:
        await repo.finalize_snapshot(session, repo_id, new_snapshot_id, stats)
        await session.commit()

    logger.info(
        "incremental update complete",
        repo=acquisition.url,
        commit=acquisition.commit_sha,
        **stats,
    )
    return IncrementalResult(
        repo_id=repo_id,
        snapshot_id=new_snapshot_id,
        commit_sha=acquisition.commit_sha,
        n_reprocessed=len(plan.to_process),
        n_unchanged=len(plan.unchanged),
        n_deleted=len(plan.deleted),
        no_op=False,
    )


async def _copy_unchanged(
    session_factory: async_sessionmaker[AsyncSession],
    vector_index: VectorIndex,
    *,
    prev_snapshot_id: uuid.UUID,
    new_snapshot_id: uuid.UUID,
    repo_id: str,
    new_commit: str,
    unchanged: set[str],
) -> None:
    """Copy unchanged files' rows and Qdrant points from the old snapshot to the new one."""
    if not unchanged:
        return
    async with session_factory() as session:
        files = [
            f
            for f in await repo.files_for_snapshot(session, prev_snapshot_id)
            if f.path in unchanged
        ]
        chunks = [
            c
            for c in await repo.chunks_for_snapshot(session, prev_snapshot_id)
            if c.file_path in unchanged
        ]
        symbols = [
            s
            for s in await repo.symbols_for_snapshot(session, prev_snapshot_id)
            if s.file_path in unchanged
        ]
        edges = [
            e
            for e in await repo.edges_for_snapshot(session, prev_snapshot_id)
            if e.src_file in unchanged
        ]

    id_pairs: list[tuple[str, str]] = []
    new_chunk_rows: list[dict] = []
    for c in chunks:
        new_id = _point_id(new_snapshot_id, c.file_path, c.chunk_index)
        id_pairs.append((str(c.id), new_id))
        new_chunk_rows.append(
            {
                "id": uuid.UUID(new_id),
                "snapshot_id": new_snapshot_id,
                "file_path": c.file_path,
                "language": c.language,
                "category": c.category,
                "symbol": c.symbol,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "content_hash": c.content_hash,
                "chunk_index": c.chunk_index,
            }
        )

    await vector_index.copy_points(
        repo_id=repo_id, pairs=id_pairs, payload_overrides={"commit": new_commit}
    )

    async with session_factory() as session:
        await repo.add_files(
            session,
            new_snapshot_id,
            [
                {
                    "path": f.path,
                    "language": f.language,
                    "size_bytes": f.size_bytes,
                    "content_hash": f.content_hash,
                }
                for f in files
            ],
        )
        if symbols:
            session.add_all(
                [
                    SymbolRow(
                        snapshot_id=new_snapshot_id,
                        file_path=s.file_path,
                        name=s.name,
                        qualified_name=s.qualified_name,
                        kind=s.kind,
                        start_line=s.start_line,
                        end_line=s.end_line,
                        signature=s.signature,
                        docstring=s.docstring,
                        parent=s.parent,
                    )
                    for s in symbols
                ]
            )
        if edges:
            session.add_all(
                [
                    EdgeModel(
                        snapshot_id=new_snapshot_id,
                        src=e.src,
                        dst=e.dst,
                        kind=e.kind,
                        confidence=e.confidence,
                        src_file=e.src_file,
                    )
                    for e in edges
                ]
            )
        if new_chunk_rows:
            await repo.add_chunks(session, new_chunk_rows)
        await session.commit()
