"""Integration-test fixtures. These require the docker-compose stack
(Postgres + Qdrant) to be running, and are skipped otherwise.
"""

import asyncio
import socket
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.config import get_settings
from repo_assistant.indexing.qdrant_index import QdrantVectorIndex
from repo_assistant.ingestion.models import Acquisition
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.db import make_engine, make_session_factory
from repo_assistant.storage.models import Repo, UserRepo


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


_STACK_UP = _port_open("localhost", 5432) and _port_open("localhost", 6333)
requires_stack = pytest.mark.skipif(
    not _STACK_UP, reason="requires docker-compose stack (Postgres:5432 + Qdrant:6333)"
)


@pytest.fixture
def session_factory() -> async_sessionmaker[AsyncSession]:
    return make_session_factory(make_engine(get_settings()))


@pytest.fixture(scope="session", autouse=True)
def _sweep_test_repo_residue() -> Iterator[None]:
    """Integration tests register repos under ``test/*`` URLs (the ``local_repo``
    fixture) and drive real ingestion, leaving repo/snapshot/chunk rows in the dev
    Postgres. Sweep them once at the end of the session so residue can't pile up
    across runs. Postgres-only: test vectors go to throwaway Qdrant collections
    (``qdrant_index``) or a FakeVectorIndex, never the shared collection.
    """
    yield
    if not _STACK_UP:
        return
    asyncio.run(_purge_test_repos())


async def _purge_test_repos() -> None:
    factory = make_session_factory(make_engine(get_settings()))
    async with factory() as session:
        repo_ids = (
            (await session.execute(select(Repo.id).where(Repo.url.like("%/test/%"))))
            .scalars()
            .all()
        )
        for repo_id in repo_ids:
            # delete_repo_rows leaves user_repos to the caller; strip it first.
            await session.execute(delete(UserRepo).where(UserRepo.repo_id == repo_id))
            await repo.delete_repo_rows(session, repo_id)
        await session.commit()


@pytest_asyncio.fixture
async def qdrant_index() -> AsyncIterator[QdrantVectorIndex]:
    """A Qdrant index backed by a throwaway collection, dropped after the test."""
    collection = f"test_chunks_{uuid.uuid4().hex[:8]}"
    index = QdrantVectorIndex.from_url(get_settings().qdrant_url, collection=collection)
    try:
        yield index
    finally:
        await index._client.delete_collection(collection)
        await index._client.close()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def local_repo(tmp_path: Path) -> Iterator[Acquisition]:
    """A small real git repo on disk, wrapped as an Acquisition (no network)."""
    files = {
        "src/service.py": (
            "import os\n\n\n"
            "class SessionManager:\n"
            '    """Manages user sessions."""\n\n'
            "    def refresh(self, token: str) -> str:\n"
            '        """Refresh an access token before it expires."""\n'
            "        return token + '-refreshed'\n\n"
            "    def revoke(self, token: str) -> None:\n"
            '        """Revoke a token immediately."""\n'
            "        return None\n"
        ),
        "src/util.py": "def slugify(text):\n    return text.lower().replace(' ', '-')\n",
        "README.md": "# Demo\n\n## Sessions\n\nThe SessionManager handles refresh and revoke.\n",
    }
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@e.com")
    _git(tmp_path, "config", "user.name", "T")
    for rel, content in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    yield Acquisition(
        url=f"https://github.com/test/demo-{uuid.uuid4().hex[:6]}.git",
        ref="main",
        commit_sha=sha,
        root_path=str(tmp_path),
    )
