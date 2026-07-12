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
from repo_assistant.cli.runtime import Runtime
from repo_assistant.core.config import get_settings
from repo_assistant.core.fakes import FakeEmbedder, FakeLLMClient, FakeVectorIndex
from repo_assistant.core.interfaces import Embedder, LLMClient, Reranker
from repo_assistant.ingestion import models as ingestion_models
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


@pytest_asyncio.fixture
async def client(runtime: _FakeRuntime) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeQueue]]:
    queue = _FakeQueue()
    app = create_app(runtime=runtime, queue=queue)  # type: ignore[arg-type]
    # ASGITransport does not run the lifespan, so wire app.state directly.
    app.state.settings = runtime.settings
    app.state.runtime = runtime
    app.state.queue = queue
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, queue


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
