import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Repo(Base):
    """A registered GitHub repository. Status: one of the ingestion state-machine values
    in docs/ARCHITECTURE.md §4 (PENDING/CLONING/.../READY/FAILED)."""

    __tablename__ = "repos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    url: Mapped[str] = mapped_column(String, unique=True)
    provider: Mapped[str] = mapped_column(String, default="github")
    default_ref: Mapped[str] = mapped_column(String, default="main")
    visibility: Mapped[str] = mapped_column(String, default="public")
    status: Mapped[str] = mapped_column(String, default="pending")
    active_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshots.id", use_alter=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Snapshot(Base):
    """A single indexed commit of a repo. Every chunk/symbol/edge/summary row is
    scoped to one snapshot (see docs/adr/0009-multitenancy-and-versioning.md)."""

    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id"))
    commit_sha: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    indexed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class File(Base):
    """A single file within a snapshot."""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = _uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id"))
    path: Mapped[str] = mapped_column(String)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int] = mapped_column(default=0)
    content_hash: Mapped[str] = mapped_column(String)


class Symbol(Base):
    """A named definition extracted from a file (the symbol-retrieval channel and,
    later, code-graph nodes). Scoped to a snapshot."""

    __tablename__ = "symbols"

    id: Mapped[uuid.UUID] = _uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id"))
    file_path: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    qualified_name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    signature: Mapped[str] = mapped_column(Text)
    docstring: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_symbols_snapshot", "snapshot_id"),
        Index("ix_symbols_snapshot_name", "snapshot_id", "name"),
    )


class Chunk(Base):
    """Bookkeeping for a retrieval unit. ``id`` is also the Qdrant point id, so the
    vector store and relational metadata stay joined (docs/ARCHITECTURE.md §7)."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id"))
    file_path: Mapped[str] = mapped_column(String)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String)
    symbol: Mapped[str | None] = mapped_column(String, nullable=True)
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String)
    chunk_index: Mapped[int] = mapped_column(Integer)

    __table_args__ = (Index("ix_chunks_snapshot", "snapshot_id"),)


class Edge(Base):
    """A code-graph edge (docs/adr/0005-code-graph.md). ``src``/``dst`` are symbol
    qualified names or module/file identifiers; kind is one of
    contains/imports/inherits/calls/references, each with a confidence in [0,1]."""

    __tablename__ = "edges"

    id: Mapped[uuid.UUID] = _uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id"))
    src: Mapped[str] = mapped_column(String)
    dst: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    src_file: Mapped[str] = mapped_column(String)

    __table_args__ = (
        Index("ix_edges_snapshot_src", "snapshot_id", "src"),
        Index("ix_edges_snapshot_dst", "snapshot_id", "dst"),
    )


class EmbeddingCache(Base):
    """Content-addressed embedding cache (docs/adr/0003-embedding-strategy.md).

    Keyed by (model, dimensions, content_hash) so re-indexing unchanged content
    never re-embeds — the primary defense against indexing-cost blowup (RISKS #1).
    """

    __tablename__ = "embedding_cache"

    content_hash: Mapped[str] = mapped_column(String, primary_key=True)
    model: Mapped[str] = mapped_column(String, primary_key=True)
    dimensions: Mapped[int] = mapped_column(Integer, primary_key=True)
    vector: Mapped[list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Job(Base):
    """A background pipeline job (ingestion, incremental update, enrichment...).
    Stage/state form the resumable checkpointed state machine (docs/adr/0008-job-queue.md)."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id"))
    job_type: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String, default="pending")
    state: Mapped[str] = mapped_column(String, default="queued")
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    progress: Mapped[dict] = mapped_column(JSONB, default=dict)
    checkpoints: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ChatSession(Base):
    """A multi-turn conversation, bound to one repo snapshot at creation.

    Pinning ``snapshot_id``/``commit_sha`` keeps the whole conversation answered
    against a single consistent commit even if the repo re-indexes mid-session
    (docs/adr/0006, docs/adr/0015). ``summary`` holds the rolling condensation of
    turns that have aged out of the verbatim window."""

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id"))
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("snapshots.id"))
    commit_sha: Mapped[str] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # How many of the oldest messages the summary already folds in — lets the
    # rolling summary update incrementally instead of re-reading the whole history.
    summary_covered_messages: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    __table_args__ = (Index("ix_chat_sessions_repo", "repo_id"),)


class ChatMessage(Base):
    """One turn within a session. Assistant turns carry the verified citations
    (JSONB) and token usage; user turns carry the raw question."""

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_sessions.id"))
    # Monotonic insertion order. created_at can't order turns within a session:
    # Postgres now() is transaction-constant, so both turns of one exchange share it.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    role: Mapped[str] = mapped_column(String)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index("ix_chat_messages_session", "session_id", "seq"),)


class EvalRun(Base):
    """One evaluation run, with the config snapshot so any two runs are diffable
    (docs/EVALUATION.md §4). ``overall`` holds the aggregated summary metrics."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    overall: Mapped[dict] = mapped_column(JSONB, default=dict)
    per_dataset: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class EvalResult(Base):
    """Per-question result within an eval run (metrics + verdict)."""

    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id"))
    dataset: Mapped[str] = mapped_column(String)
    question_id: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String)
    passed: Mapped[bool] = mapped_column(Boolean)
    ranking: Mapped[dict] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (Index("ix_eval_results_run", "run_id"),)
