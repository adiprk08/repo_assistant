"""Chat endpoint: routed, grounded answering streamed over SSE.

The reasoning pipeline exposes an ``on_text`` callback; here it feeds an
``asyncio.Queue`` that the SSE generator drains, so answer tokens reach the client
as they are produced. The final ``done`` event carries the routing metadata and
the verified citations (citations are known only once generation completes).

When ``session_id`` is set the turn is conversational: answered against the
session's *pinned* snapshot (not the repo's current active one), grounded in the
prior turns, and persisted (docs/adr/0015). Otherwise it is a stateless one-off.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import cast

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from repo_assistant.api.auth import CurrentUser
from repo_assistant.api.deps import RuntimeDep
from repo_assistant.api.schemas import ChatRequest, CitationOut
from repo_assistant.api.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_event
from repo_assistant.cli.runtime import resolve_indexed_repo
from repo_assistant.core.errors import NotFoundError, RepoAssistantError
from repo_assistant.core.interfaces import Message
from repo_assistant.reasoning import RoutedAnswer, answer_routed
from repo_assistant.reasoning.conversation import prepare_turn, record_turn
from repo_assistant.reasoning.router import Path
from repo_assistant.storage import repositories as repo

router = APIRouter(prefix="/repos", tags=["chat"])


@router.post("/{repo_id}/chat")
async def chat_repo(
    repo_id: uuid.UUID, body: ChatRequest, runtime: RuntimeDep, user: CurrentUser
) -> StreamingResponse:
    force_path: Path | None = None if body.path == "auto" else cast(Path, body.path)

    # Resolve the target snapshot before streaming so a missing/unindexed repo or
    # session is a real HTTP error, not an SSE event after headers are sent. Access
    # is guarded here too: the session must belong to the caller, and a stateless
    # turn requires library membership on the repo (docs/adr/0023).
    history: list[Message] | None = None
    retrieval_query: str | None = None
    if body.session_id is not None:
        async with runtime.session_factory() as session:
            chat = await repo.get_session(session, body.session_id)
            if chat is None or chat.repo_id != repo_id or chat.user_id != user.id:
                raise NotFoundError(f"No session {body.session_id} for repository {repo_id}")
            repo_id_str = str(chat.repo_id)
            snapshot_id_str = str(chat.snapshot_id)
            commit = chat.commit_sha
        prepared = await prepare_turn(
            runtime.session_factory,
            body.session_id,
            body.question,
            router_llm=runtime.llm(model=runtime.settings.router_model),
            settings=runtime.settings,
        )
        history, retrieval_query = prepared.history, prepared.retrieval_query
    else:
        async with runtime.session_factory() as session:
            if not await repo.is_repo_member(session, user.id, repo_id):
                raise NotFoundError(f"No repository {repo_id}")
        resolved = await resolve_indexed_repo(runtime, str(repo_id))
        repo_id_str = str(resolved.repo_id)
        snapshot_id_str = str(resolved.snapshot_id)
        commit = resolved.commit_sha

    embedder = runtime.embedder()
    llm = runtime.llm()
    router_llm = runtime.llm(model=runtime.settings.router_model)

    async def events() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        async def on_text(delta: str) -> None:
            await queue.put(("token", delta))

        async def run() -> None:
            try:
                routed = await answer_routed(
                    repo_id=repo_id_str,
                    snapshot_id=snapshot_id_str,
                    commit=commit,
                    question=body.question,
                    embedder=embedder,
                    vector_index=runtime.vector_index,
                    session_factory=runtime.session_factory,
                    llm=llm,
                    router_llm=router_llm,
                    force_path=force_path,
                    budget=runtime.settings.agent_tool_call_budget,
                    on_text=on_text,
                    history=history,
                    retrieval_query=retrieval_query,
                )
                if body.session_id is not None and routed.answer is not None:
                    await record_turn(
                        runtime.session_factory,
                        body.session_id,
                        question=body.question,
                        answer=routed.answer,
                        summarizer_llm=router_llm,
                        settings=runtime.settings,
                    )
                await queue.put(("result", routed))
            except RepoAssistantError as exc:
                await queue.put(("error", exc))
            finally:
                await queue.put(("end", None))

        task = asyncio.create_task(run())
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "token":
                    yield sse_event("token", {"text": payload})
                elif kind == "result":
                    yield _result_events(cast(RoutedAnswer, payload))
                elif kind == "error":
                    yield sse_event("error", {"detail": str(payload)})
                elif kind == "end":
                    break
        finally:
            await task

    return StreamingResponse(events(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


def _result_events(routed: RoutedAnswer) -> str:
    """Serialize the terminal ``done`` event: citations + routing metadata."""
    answer = routed.answer
    citations = (
        [
            CitationOut(
                path=c.path,
                start_line=c.start_line,
                end_line=c.end_line,
                commit=c.commit,
                cited_text=c.cited_text,
            ).model_dump()
            for c in answer.citations
        ]
        if answer is not None
        else []
    )
    return sse_event(
        "done",
        {
            "path": routed.path,
            "intent": routed.intent,
            "n_tool_calls": routed.n_tool_calls,
            "forced_stop": routed.forced_stop,
            "refused": answer.refused if answer is not None else None,
            "citations": citations,
        },
    )
