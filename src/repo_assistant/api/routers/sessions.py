"""Chat sessions: create a conversation bound to a repo's active snapshot, and
list/inspect sessions with their message history.
"""

import uuid

from fastapi import APIRouter, status

from repo_assistant.api.auth import CurrentUser
from repo_assistant.api.deps import RuntimeDep
from repo_assistant.api.schemas import (
    MessageOut,
    SessionCreate,
    SessionDetailOut,
    SessionOut,
)
from repo_assistant.cli.runtime import resolve_indexed_repo
from repo_assistant.core.errors import NotFoundError
from repo_assistant.storage import repositories as repo

router = APIRouter(prefix="/repos", tags=["sessions"])


@router.post("/{repo_id}/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    repo_id: uuid.UUID, body: SessionCreate, runtime: RuntimeDep, user: CurrentUser
) -> SessionOut:
    """Open a session (owned by the caller) pinned to the repo's active snapshot."""
    async with runtime.session_factory() as session:
        if not await repo.is_repo_member(session, user.id, repo_id):
            raise NotFoundError(f"No repository {repo_id}")
    resolved = await resolve_indexed_repo(runtime, str(repo_id))  # NotFoundError -> 404
    async with runtime.session_factory() as session:
        chat = await repo.create_session(
            session,
            repo_id=resolved.repo_id,
            snapshot_id=resolved.snapshot_id,
            commit_sha=resolved.commit_sha,
            title=body.title,
            user_id=user.id,
        )
        await session.commit()
        return SessionOut.model_validate(chat)


@router.get("/{repo_id}/sessions")
async def list_sessions(
    repo_id: uuid.UUID, runtime: RuntimeDep, user: CurrentUser
) -> list[SessionOut]:
    async with runtime.session_factory() as session:
        rows = await repo.list_sessions_for_user_repo(session, user.id, repo_id)
        return [SessionOut.model_validate(r) for r in rows]


@router.get("/{repo_id}/sessions/{session_id}")
async def get_session(
    repo_id: uuid.UUID, session_id: uuid.UUID, runtime: RuntimeDep, user: CurrentUser
) -> SessionDetailOut:
    async with runtime.session_factory() as session:
        chat = await repo.get_session(session, session_id)
        if chat is None or chat.repo_id != repo_id or chat.user_id != user.id:
            raise NotFoundError(f"No session {session_id} for repository {repo_id}")
        messages = await repo.get_messages(session, session_id)
        return SessionDetailOut(
            **SessionOut.model_validate(chat).model_dump(),
            messages=[MessageOut.model_validate(m) for m in messages],
        )
