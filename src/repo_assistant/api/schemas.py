"""Request/response schemas for the API service.

Pydantic models at the HTTP boundary only — pipeline code keeps its own
dataclasses; routers translate between the two (CLAUDE.md: api/ is a thin shell).
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserOut(BaseModel):
    """The signed-in account (docs/adr/0023)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    login: str
    name: str | None
    avatar_url: str | None


class RepoCreate(BaseModel):
    url: str = Field(description="GitHub repository URL.")
    ref: str | None = Field(default=None, description="Branch, tag, or full commit SHA.")
    enrich: bool = Field(
        default=False,
        description="Add LLM contextual descriptions to chunks before embedding (ADR-0013).",
    )
    installation_id: int | None = Field(
        default=None,
        description="GitHub App installation id for a private repo (ADR-0020); omit for public.",
    )


class SnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    commit_sha: str
    status: str
    stats: dict
    indexed_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repo_id: uuid.UUID
    job_type: str
    stage: str
    state: str
    progress: dict
    error: str | None
    created_at: datetime
    updated_at: datetime


class RepoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    default_ref: str
    status: str
    created_at: datetime


class RepoDetailOut(RepoOut):
    active_snapshot: SnapshotOut | None = None
    latest_job: JobOut | None = None


class RepoRegistered(BaseModel):
    """POST /repos response: the repo plus the ingestion job to watch."""

    repo: RepoOut
    job: JobOut


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=12, ge=1, le=50)


class SearchHit(BaseModel):
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    score: float
    symbol: str | None
    language: str | None
    excerpt: str


class SearchResponse(BaseModel):
    commit: str
    results: list[SearchHit]


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, description="Optional human label for the session.")


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repo_id: uuid.UUID
    snapshot_id: uuid.UUID
    commit_sha: str
    title: str | None
    summary: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    role: str
    content: str
    citations: list
    usage: dict
    created_at: datetime


class SessionDetailOut(SessionOut):
    messages: list[MessageOut] = []


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    path: Literal["auto", "fast", "agent"] = Field(
        default="auto", description="Reasoning path: auto (router decides), fast, or agent."
    )
    session_id: uuid.UUID | None = Field(
        default=None,
        description="Bind the turn to a conversation. Uses the session's pinned "
        "snapshot and prior turns; persists this exchange. Omit for a stateless one-off.",
    )


class CitationOut(BaseModel):
    path: str
    start_line: int
    end_line: int
    commit: str
    cited_text: str
