"""Search endpoint: hybrid retrieval over a repo's active snapshot."""

import uuid

from fastapi import APIRouter

from repo_assistant.api.auth import CurrentUser
from repo_assistant.api.deps import RuntimeDep
from repo_assistant.api.schemas import SearchHit, SearchRequest, SearchResponse
from repo_assistant.cli.runtime import resolve_indexed_repo
from repo_assistant.core.errors import NotFoundError
from repo_assistant.retrieval import hybrid_retrieve
from repo_assistant.storage import repositories as repo

router = APIRouter(prefix="/repos", tags=["search"])

# Cap excerpts so a search response stays small even for large chunks.
_EXCERPT_CHARS = 600


@router.post("/{repo_id}/search")
async def search_repo(
    repo_id: uuid.UUID, body: SearchRequest, runtime: RuntimeDep, user: CurrentUser
) -> SearchResponse:
    async with runtime.session_factory() as session:
        if not await repo.is_repo_member(session, user.id, repo_id):
            raise NotFoundError(f"No repository {repo_id}")
    resolved = await resolve_indexed_repo(runtime, str(repo_id))  # NotFoundError -> 404
    chunks = await hybrid_retrieve(
        str(resolved.repo_id),
        str(resolved.snapshot_id),
        body.query,
        embedder=runtime.embedder(),
        vector_index=runtime.vector_index,
        session_factory=runtime.session_factory,
        commit=resolved.commit_sha,
        limit=body.limit,
        use_rerank=False,
    )
    return SearchResponse(
        commit=resolved.commit_sha,
        results=[
            SearchHit(
                chunk_id=c.chunk_id,
                path=c.path,
                start_line=c.start_line,
                end_line=c.end_line,
                score=c.score,
                symbol=c.symbol,
                language=c.language,
                excerpt=c.text[:_EXCERPT_CHARS],
            )
            for c in chunks
        ],
    )
