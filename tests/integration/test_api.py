"""End-to-end API + worker tests over the real Postgres, with fake providers.

The vector store, embedder, and LLM are fakes (no infra/keys/cost); Postgres is
real so the routers exercise the actual persistence and the ingestion job's state
machine. Requires the docker-compose stack (skipped otherwise).
"""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.api.app import create_app
from repo_assistant.api.ratelimit import InMemoryRateLimiter, NoopRateLimiter, RateLimiter
from repo_assistant.api.security import generate_api_key
from repo_assistant.cli.runtime import Runtime
from repo_assistant.core.config import get_settings
from repo_assistant.core.fakes import FakeEmbedder, FakeLLMClient, FakeVectorIndex
from repo_assistant.core.interfaces import Embedder, LLMClient, Reranker
from repo_assistant.ingestion import models as ingestion_models
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.db import make_engine, make_session_factory
from repo_assistant.workers.ingestion import run_ingestion

from .conftest import requires_stack

pytestmark = requires_stack


class _FakeQueue:
    """Records enqueued jobs instead of talking to Redis."""

    def __init__(self) -> None:
        self.enqueued: list[uuid.UUID] = []

    async def enqueue(self, job_id: uuid.UUID) -> None:
        self.enqueued.append(job_id)

    async def aclose(self) -> None:
        return None


class _FakeRuntime(Runtime):
    """A Runtime whose providers are fakes; keeps the real Postgres session factory."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder,
        llm: LLMClient,
    ) -> None:
        super().__init__(
            settings=get_settings(),
            session_factory=session_factory,
            vector_index=FakeVectorIndex(),
        )
        self._embedder = embedder
        self._llm = llm

    def embedder(self) -> Embedder:
        return self._embedder

    def llm(self, *, model: str | None = None) -> LLMClient:
        return self._llm

    def reranker(self) -> Reranker | None:
        return None


@pytest.fixture
def runtime() -> _FakeRuntime:
    factory = make_session_factory(make_engine(get_settings()))
    return _FakeRuntime(session_factory=factory, embedder=FakeEmbedder(), llm=FakeLLMClient())


def _build_app(runtime: _FakeRuntime, queue: _FakeQueue, rate_limiter: RateLimiter):
    """Create the app and wire app.state directly (ASGITransport skips the lifespan)."""
    app = create_app(runtime=runtime, queue=queue)  # type: ignore[arg-type]
    app.state.settings = runtime.settings
    app.state.runtime = runtime
    app.state.queue = queue
    app.state.rate_limiter = rate_limiter
    return app


async def _mint_key(runtime: _FakeRuntime, *, revoked: bool = False) -> tuple[str, uuid.UUID]:
    g = generate_api_key()
    async with runtime.session_factory() as session:
        row = await repo.create_api_key(
            session, name="test", key_prefix=g.prefix, key_hash=g.key_hash
        )
        if revoked:
            await repo.revoke_api_key(session, row.id)
        await session.commit()
        return g.plaintext, row.id


async def _drop_key(runtime: _FakeRuntime, key_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    from repo_assistant.storage.models import ApiKey

    async with runtime.session_factory() as session:
        await session.execute(delete(ApiKey).where(ApiKey.id == key_id))
        await session.commit()


@pytest_asyncio.fixture
async def client(runtime: _FakeRuntime) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeQueue]]:
    """Authenticated client with rate limiting disabled — the default for endpoint tests."""
    queue = _FakeQueue()
    app = _build_app(runtime, queue, NoopRateLimiter())
    plaintext, key_id = await _mint_key(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {plaintext}"},
    ) as c:
        try:
            yield c, queue
        finally:
            await _drop_key(runtime, key_id)


async def _drive_worker(runtime: _FakeRuntime, monkeypatch, local_repo, job_id: uuid.UUID) -> None:
    """Run the ingestion job with the real pipeline, but clone -> the local repo."""

    async def fake_clone(url: str, dest: str, ref: str | None = None):
        # Preserve the registered URL so create_or_get_repo matches the existing row.
        return ingestion_models.Acquisition(
            url=url,
            ref=local_repo.ref,
            commit_sha=local_repo.commit_sha,
            root_path=local_repo.root_path,
        )

    monkeypatch.setattr("repo_assistant.indexing.pipeline.clone", fake_clone)
    await run_ingestion({"runtime": runtime}, str(job_id))


async def test_health(client: tuple[httpx.AsyncClient, _FakeQueue]) -> None:
    c, _ = client
    resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_register_rejects_bad_url(client: tuple[httpx.AsyncClient, _FakeQueue]) -> None:
    c, _ = client
    resp = await c.post("/repos", json={"url": "not-a-github-url"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "IngestionError"


async def test_get_missing_repo_is_404(client: tuple[httpx.AsyncClient, _FakeQueue]) -> None:
    c, _ = client
    resp = await c.get(f"/repos/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_full_flow(
    client: tuple[httpx.AsyncClient, _FakeQueue],
    runtime: _FakeRuntime,
    local_repo,
    monkeypatch,
) -> None:
    c, queue = client

    # 1. Register -> repo pending, job queued.
    resp = await c.post("/repos", json={"url": local_repo.url})
    assert resp.status_code == 202
    body = resp.json()
    repo_id = body["repo"]["id"]
    job_id = uuid.UUID(body["job"]["id"])
    assert body["repo"]["status"] == "pending"
    assert queue.enqueued == [job_id]

    # 2. It shows up in the list and detail views.
    listed = await c.get("/repos")
    assert any(r["id"] == repo_id for r in listed.json())

    # 3. Drive the ingestion worker; job -> succeeded, repo -> ready.
    await _drive_worker(runtime, monkeypatch, local_repo, job_id)
    detail = (await c.get(f"/repos/{repo_id}")).json()
    assert detail["status"] == "ready"
    assert detail["active_snapshot"] is not None
    assert detail["latest_job"]["state"] == "succeeded"
    assert detail["latest_job"]["stage"] == "ready"

    # 4. Job progress stream terminates on the succeeded job.
    stream = await c.get(f"/repos/{repo_id}/job/stream")
    assert stream.status_code == 200
    assert "event: progress" in stream.text
    assert "event: done" in stream.text

    # 5. Search returns hits from the indexed snapshot.
    search = await c.post(f"/repos/{repo_id}/search", json={"query": "SessionManager refresh"})
    assert search.status_code == 200
    sbody = search.json()
    assert sbody["commit"] == local_repo.commit_sha
    assert len(sbody["results"]) > 0
    assert all("path" in hit for hit in sbody["results"])

    # 6. Chat streams tokens then a terminal done event with routing metadata.
    chat = await c.post(
        f"/repos/{repo_id}/chat", json={"question": "What is refresh?", "path": "fast"}
    )
    assert chat.status_code == 200
    assert chat.headers["content-type"].startswith("text/event-stream")
    assert "event: token" in chat.text
    assert "event: done" in chat.text
    assert '"path": "fast"' in chat.text

    # 7. Delete removes the repo; it is then gone.
    deleted = await c.delete(f"/repos/{repo_id}")
    assert deleted.status_code == 204
    assert (await c.get(f"/repos/{repo_id}")).status_code == 404


async def test_search_on_unindexed_repo_is_404(
    client: tuple[httpx.AsyncClient, _FakeQueue],
) -> None:
    c, _ = client
    resp = await c.post(f"/repos/{uuid.uuid4()}/search", json={"query": "anything"})
    assert resp.status_code == 404


async def _register_and_index(
    c: httpx.AsyncClient, runtime: _FakeRuntime, monkeypatch, local_repo
) -> str:
    """Register + drive ingestion; return the ready repo id."""
    resp = await c.post("/repos", json={"url": local_repo.url})
    body = resp.json()
    repo_id = body["repo"]["id"]
    await _drive_worker(runtime, monkeypatch, local_repo, uuid.UUID(body["job"]["id"]))
    return repo_id


async def test_session_multi_turn_persists_and_pins_snapshot(
    client: tuple[httpx.AsyncClient, _FakeQueue],
    runtime: _FakeRuntime,
    local_repo,
    monkeypatch,
) -> None:
    from repo_assistant.storage import repositories as repo

    # Small window so the rolling summary actually fires within two turns.
    monkeypatch.setattr(runtime.settings, "history_window_messages", 2)
    c, _ = client
    repo_id = await _register_and_index(c, runtime, monkeypatch, local_repo)

    # Create a session; it pins the repo's active snapshot + commit.
    created = await c.post(f"/repos/{repo_id}/sessions", json={"title": "my session"})
    assert created.status_code == 201
    session = created.json()
    session_id = session["id"]
    assert session["commit_sha"] == local_repo.commit_sha
    assert session["title"] == "my session"

    # Two conversational turns bound to the session.
    for question in ("What does SessionManager do?", "and how does refresh work?"):
        chat = await c.post(
            f"/repos/{repo_id}/chat",
            json={"question": question, "path": "fast", "session_id": session_id},
        )
        assert chat.status_code == 200
        assert "event: done" in chat.text

    # History persisted: user/assistant turns in order, raw questions preserved.
    detail = (await c.get(f"/repos/{repo_id}/sessions/{session_id}")).json()
    msgs = detail["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "What does SessionManager do?"
    assert msgs[2]["content"] == "and how does refresh work?"

    # The older turn aged out of the window and rolled into the summary.
    async with runtime.session_factory() as db:
        row = await repo.get_session(db, uuid.UUID(session_id))
        assert row is not None
        assert row.summary is not None
        assert row.summary_covered_messages == 2

    # Session shows up in the list, and deleting the repo cascades it away.
    listed = await c.get(f"/repos/{repo_id}/sessions")
    assert any(s["id"] == session_id for s in listed.json())
    assert (await c.delete(f"/repos/{repo_id}")).status_code == 204
    assert (await c.get(f"/repos/{repo_id}/sessions/{session_id}")).status_code == 404


async def test_create_session_on_unindexed_repo_is_404(
    client: tuple[httpx.AsyncClient, _FakeQueue],
) -> None:
    c, _ = client
    resp = await c.post(f"/repos/{uuid.uuid4()}/sessions", json={})
    assert resp.status_code == 404


async def test_chat_with_unknown_session_is_404(
    client: tuple[httpx.AsyncClient, _FakeQueue],
    runtime: _FakeRuntime,
    local_repo,
    monkeypatch,
) -> None:
    c, _ = client
    repo_id = await _register_and_index(c, runtime, monkeypatch, local_repo)
    resp = await c.post(
        f"/repos/{repo_id}/chat",
        json={"question": "hi", "path": "fast", "session_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    await c.delete(f"/repos/{repo_id}")


async def test_worker_marks_job_failed_on_pipeline_error(
    runtime: _FakeRuntime, local_repo, monkeypatch
) -> None:
    """A pipeline exception flips the job and repo to failed and re-raises."""
    from repo_assistant.storage import repositories as repo

    # Register a repo + job directly.
    async with runtime.session_factory() as session:
        repo_row = await repo.create_or_get_repo(session, local_repo.url, "main")
        job = await repo.create_job(session, repo_row.id, params={"url": local_repo.url})
        await session.commit()
        repo_id, job_id = repo_row.id, job.id

    async def boom(*args, **kwargs):
        raise RuntimeError("clone exploded")

    monkeypatch.setattr("repo_assistant.workers.ingestion.index_repository", boom)
    with pytest.raises(RuntimeError):
        await run_ingestion({"runtime": runtime}, str(job_id))

    async with runtime.session_factory() as session:
        failed_job = await repo.get_job(session, job_id)
        assert failed_job is not None
        assert failed_job.state == "failed"
        assert "clone exploded" in (failed_job.error or "")
        # Clean up so the shared DB doesn't accumulate this throwaway repo.
        await repo.delete_repo_rows(session, repo_id)
        await session.commit()


# --- auth + rate limiting ---------------------------------------------------


@pytest_asyncio.fixture
async def unauth_client(runtime: _FakeRuntime) -> AsyncIterator[httpx.AsyncClient]:
    """A client with no credentials and rate limiting off."""
    app = _build_app(runtime, _FakeQueue(), NoopRateLimiter())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_is_open_without_auth(unauth_client: httpx.AsyncClient) -> None:
    assert (await unauth_client.get("/health")).status_code == 200


async def test_cors_allows_the_ui_origin(unauth_client: httpx.AsyncClient) -> None:
    resp = await unauth_client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_missing_key_returns_401(unauth_client: httpx.AsyncClient) -> None:
    resp = await unauth_client.get("/repos")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert resp.json()["error"] == "AuthenticationError"


async def test_invalid_key_returns_401(unauth_client: httpx.AsyncClient) -> None:
    resp = await unauth_client.get("/repos", headers={"Authorization": "Bearer ra_bogus"})
    assert resp.status_code == 401


async def test_revoked_key_returns_401(
    unauth_client: httpx.AsyncClient, runtime: _FakeRuntime
) -> None:
    plaintext, key_id = await _mint_key(runtime, revoked=True)
    try:
        resp = await unauth_client.get("/repos", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 401
    finally:
        await _drop_key(runtime, key_id)


async def test_rate_limit_returns_429(runtime: _FakeRuntime) -> None:
    app = _build_app(runtime, _FakeQueue(), InMemoryRateLimiter(limit=2, window_seconds=60))
    plaintext, key_id = await _mint_key(runtime)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {plaintext}"},
        ) as c:
            assert (await c.get("/repos")).status_code == 200
            assert (await c.get("/repos")).status_code == 200
            limited = await c.get("/repos")  # third call is over budget
            assert limited.status_code == 429
            assert int(limited.headers["retry-after"]) >= 1
    finally:
        await _drop_key(runtime, key_id)
