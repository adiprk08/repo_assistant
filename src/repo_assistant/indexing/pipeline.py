"""End-to-end indexing pipeline: URL -> READY snapshot.

Orchestrates acquire -> scan -> parse -> chunk -> embed -> index for one commit,
writing vectors to Qdrant and metadata to Postgres, then atomically promoting the
new snapshot to active (docs/ARCHITECTURE.md §4). Providers are injected so the
whole flow runs against fakes with zero API/infra cost in tests.
"""

import hashlib
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.chunking import chunk_code
from repo_assistant.chunking.models import Chunk
from repo_assistant.chunking.text import chunk_fallback, chunk_markdown
from repo_assistant.core.interfaces import Embedder, LLMClient, VectorIndex, VectorPoint
from repo_assistant.core.logging import get_logger
from repo_assistant.core.sparse import text_to_sparse
from repo_assistant.graph.extract import SymbolContext, extract_edges
from repo_assistant.indexing.enrichment import enrich_chunks
from repo_assistant.ingestion import clone, scan
from repo_assistant.ingestion.models import Acquisition, FileCategory, ScannedFile
from repo_assistant.parsing import parse_file
from repo_assistant.parsing.models import ParsedFile
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.db import make_session_factory
from repo_assistant.storage.models import Edge as EdgeModel
from repo_assistant.storage.models import Symbol as SymbolRow

logger = get_logger(__name__)

_POINT_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00cf4fc964ff")


@dataclass(frozen=True, slots=True)
class IndexResult:
    repo_id: uuid.UUID
    snapshot_id: uuid.UUID
    commit_sha: str
    n_files: int
    n_chunks: int
    n_symbols: int


def _point_id(snapshot_id: uuid.UUID, path: str, index: int) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{snapshot_id}:{path}:{index}"))


def _chunks_for(scanned: ScannedFile, source: bytes) -> tuple[list[Chunk], ParsedFile | None]:
    if scanned.category is FileCategory.CODE and scanned.language is not None:
        parsed = parse_file(scanned.path, scanned.language, source)
        return chunk_code(parsed), parsed
    text = source.decode("utf-8", "replace")
    ext = scanned.path.rsplit(".", 1)[-1].lower()
    if scanned.category is FileCategory.DOC and ext in {"md", "markdown", "mdx"}:
        return chunk_markdown(scanned.path, text), None
    return chunk_fallback(scanned.path, text, scanned.category), None


async def _build_units(
    acquisition: Acquisition, scanned_files: list[ScannedFile]
) -> tuple[list[Chunk], list[dict], list[SymbolContext]]:
    """Chunk every file; collect symbol rows and symbol contexts (for edges)."""
    root = Path(acquisition.root_path)
    all_chunks: list[Chunk] = []
    symbol_rows: list[dict] = []
    contexts: list[SymbolContext] = []
    for scanned in scanned_files:
        try:
            source = (root / scanned.path).read_bytes()
        except OSError:
            continue
        chunks, parsed = _chunks_for(scanned, source)
        all_chunks.extend(chunks)
        if parsed is not None:
            for s in parsed.symbols:
                symbol_rows.append(
                    {
                        "file_path": scanned.path,
                        "name": s.name,
                        "qualified_name": s.qualified_name,
                        "kind": str(s.kind),
                        "start_line": s.start_line,
                        "end_line": s.end_line,
                        "signature": s.signature,
                        "docstring": s.docstring,
                        "parent": s.parent,
                    }
                )
                contexts.append(
                    SymbolContext(
                        qualified_name=s.qualified_name,
                        name=s.name,
                        file_path=scanned.path,
                        parent=s.parent,
                        body=parsed.source[s.start_byte : s.end_byte].decode("utf-8", "replace"),
                    )
                )
    return all_chunks, symbol_rows, contexts


def _vector_points(
    chunks: list[Chunk],
    vectors: list[list[float]],
    *,
    snapshot_id: uuid.UUID,
    repo_id: str,
    commit: str,
) -> tuple[list[VectorPoint], list[dict]]:
    points: list[VectorPoint] = []
    chunk_rows: list[dict] = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        point_id = _point_id(snapshot_id, chunk.path, chunk.index)
        payload = {
            "repo_id": repo_id,
            "path": chunk.path,
            "language": chunk.language,
            "category": str(chunk.category),
            "symbol": chunk.symbol,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "commit": commit,
            "text": chunk.text,
        }
        points.append(
            VectorPoint(
                id=point_id,
                dense_vector=vector,
                # BM25 sparse over the embed text (breadcrumb + code) for lexical
                # matching on identifiers (docs/adr/0004).
                sparse_vector=text_to_sparse(chunk.embed_text),
                payload=payload,
            )
        )
        chunk_rows.append(
            {
                "id": uuid.UUID(point_id),
                "snapshot_id": snapshot_id,
                "file_path": chunk.path,
                "language": chunk.language,
                "category": str(chunk.category),
                "symbol": chunk.symbol,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content_hash": hashlib.sha256(chunk.text.encode("utf-8")).hexdigest(),
                "chunk_index": chunk.index,
            }
        )
    return points, chunk_rows


async def index_repository(
    url: str,
    *,
    embedder: Embedder,
    vector_index: VectorIndex,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    ref: str | None = None,
    workdir: str | None = None,
    enricher: LLMClient | None = None,
) -> IndexResult:
    """Clone ``url`` and index it. The clone lives only for the duration of indexing."""
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        acquisition = await clone(url, tmp, ref=ref)
        return await index_working_tree(
            acquisition,
            embedder=embedder,
            vector_index=vector_index,
            session_factory=session_factory,
            enricher=enricher,
        )


async def index_working_tree(
    acquisition: Acquisition,
    *,
    embedder: Embedder,
    vector_index: VectorIndex,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    enricher: LLMClient | None = None,
) -> IndexResult:
    """Index an already-acquired working tree (scan -> chunk -> [enrich] -> embed -> persist).

    When ``enricher`` is set, code chunks get an LLM contextual description folded
    into their embedded text before embedding (ADR-0002); citation spans are
    unchanged.
    """
    session_factory = session_factory or make_session_factory_from_settings()

    scan_result = await scan(acquisition)
    chunks, symbol_rows, contexts = await _build_units(acquisition, scan_result.files)
    edges = extract_edges(contexts)

    if enricher is not None:
        chunks = await enrich_chunks(enricher, chunks)

    await vector_index.prepare(embedder.dimensions)
    vectors = await embedder.embed([c.embed_text for c in chunks], input_type="document")

    async with session_factory() as session:
        repo_row = await repo.create_or_get_repo(session, acquisition.url, acquisition.ref)
        snapshot = await repo.create_snapshot(session, repo_row.id, acquisition.commit_sha)
        await session.commit()
        repo_id, snapshot_id = repo_row.id, snapshot.id

    points, chunk_rows = _vector_points(
        chunks,
        vectors,
        snapshot_id=snapshot_id,
        repo_id=str(repo_id),
        commit=acquisition.commit_sha,
    )
    await vector_index.upsert(points)

    stats = {
        "files": len(scan_result.files),
        "chunks": len(chunks),
        "symbols": len(symbol_rows),
        "edges": len(edges),
    }
    async with session_factory() as session:
        await repo.add_files(
            session,
            snapshot_id,
            [
                {
                    "path": f.path,
                    "language": f.language,
                    "size_bytes": f.size_bytes,
                    "content_hash": f.content_hash,
                }
                for f in scan_result.files
            ],
        )
        if symbol_rows:
            session.add_all([SymbolRow(snapshot_id=snapshot_id, **s) for s in symbol_rows])
        if edges:
            session.add_all(
                [
                    EdgeModel(
                        snapshot_id=snapshot_id,
                        src=e.src,
                        dst=e.dst,
                        kind=e.kind,
                        confidence=e.confidence,
                        src_file=e.src_file,
                    )
                    for e in edges
                ]
            )
        await repo.add_chunks(session, chunk_rows)
        await repo.finalize_snapshot(session, repo_id, snapshot_id, stats)
        await session.commit()

    logger.info("index complete", repo=acquisition.url, commit=acquisition.commit_sha, **stats)
    return IndexResult(
        repo_id=repo_id,
        snapshot_id=snapshot_id,
        commit_sha=acquisition.commit_sha,
        n_files=len(scan_result.files),
        n_chunks=len(chunks),
        n_symbols=len(symbol_rows),
    )


def make_session_factory_from_settings() -> async_sessionmaker[AsyncSession]:
    from repo_assistant.storage.db import make_engine

    return make_session_factory(make_engine())
