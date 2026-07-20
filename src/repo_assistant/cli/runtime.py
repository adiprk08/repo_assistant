"""Dependency wiring for the CLI.

Composes the real providers, vector index, and database session factory from
settings, so the command handlers stay thin. Kept out of ``main`` so it can be
reused by the API service and workers in later phases.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.config import Settings, get_settings
from repo_assistant.core.errors import NotFoundError
from repo_assistant.core.interfaces import Embedder, LLMClient, Reranker, VectorIndex
from repo_assistant.indexing.cache import CachingEmbedder, EmbeddingCacheStore
from repo_assistant.indexing.qdrant_index import QdrantVectorIndex
from repo_assistant.ingestion.git import normalize_github_url
from repo_assistant.providers import get_embedder, get_llm_client, get_reranker
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.db import make_engine, make_session_factory
from repo_assistant.storage.models import Repo


@dataclass(slots=True)
class Runtime:
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    vector_index: VectorIndex

    def embedder(self) -> Embedder:
        """Voyage embedder wrapped in the content-addressed cache."""
        inner = get_embedder(self.settings)
        return CachingEmbedder(inner, EmbeddingCacheStore(self.session_factory))

    def llm(self, *, model: str | None = None) -> LLMClient:
        """The generation LLM (Opus by default); pass ``model`` for the router (Haiku)."""
        return get_llm_client(self.settings, model=model)

    def reranker(self) -> Reranker | None:
        return get_reranker(self.settings)

    async def aclose(self) -> None:
        await self.vector_index.aclose()


def build_runtime(settings: Settings | None = None) -> Runtime:
    settings = settings or get_settings()
    return Runtime(
        settings=settings,
        session_factory=make_session_factory(make_engine(settings)),
        vector_index=QdrantVectorIndex.from_url(
            settings.qdrant_url, api_key=settings.qdrant_api_key
        ),
    )


@dataclass(frozen=True, slots=True)
class ResolvedRepo:
    repo_id: uuid.UUID
    snapshot_id: uuid.UUID
    url: str
    commit_sha: str


async def resolve_indexed_repo(runtime: Runtime, identifier: str) -> ResolvedRepo:
    """Resolve a URL or repo-id to its active (indexed) snapshot, or raise."""
    async with runtime.session_factory() as session:
        repo_row = await _find_repo(session, identifier)
        if repo_row is None:
            raise NotFoundError(f"No repository matches {identifier!r}. Run `ra index` first.")
        snapshot = await repo.get_active_snapshot(session, repo_row.id)
        if snapshot is None:
            raise NotFoundError(f"{repo_row.url} has no indexed snapshot yet. Run `ra index`.")
        return ResolvedRepo(
            repo_id=repo_row.id,
            snapshot_id=snapshot.id,
            url=repo_row.url,
            commit_sha=snapshot.commit_sha,
        )


async def _find_repo(session: AsyncSession, identifier: str) -> Repo | None:
    # Try as a UUID first, then as a (normalizable) GitHub URL.
    try:
        by_id = await session.get(Repo, uuid.UUID(identifier))
        if by_id is not None:
            return by_id
    except ValueError:
        pass
    try:
        url = normalize_github_url(identifier)
    except Exception:  # noqa: BLE001 - not a URL; nothing more to try
        return await repo.get_repo_by_url(session, identifier)
    return await repo.get_repo_by_url(session, url)


__all__ = ["ResolvedRepo", "Runtime", "build_runtime", "resolve_indexed_repo"]
