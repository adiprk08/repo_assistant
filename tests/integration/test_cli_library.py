"""CLI ``ra library`` tests: claim an already-indexed repo into a user's library
over the shared index, with no re-index (docs/adr/0023).

Drives the real Typer app against Postgres with a seeded repo + snapshot, then
tears every seeded row down (FK-safe) so no residue is left in the dev DB.
"""

import asyncio
import uuid

from sqlalchemy import delete, update
from typer.testing import CliRunner

from repo_assistant.cli.main import app
from repo_assistant.core.config import get_settings
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.db import make_engine, make_session_factory
from repo_assistant.storage.models import Repo, Snapshot, User, UserRepo

from .conftest import requires_stack

pytestmark = requires_stack

runner = CliRunner()


def _session_factory():
    return make_session_factory(make_engine(get_settings()))


async def _seed_indexed_repo(url: str) -> uuid.UUID:
    """A repo with a READY, active snapshot — as if ``ra index`` had run."""
    async with _session_factory()() as session:
        row = await repo.create_or_get_repo(session, url, default_ref="main")
        snap = await repo.create_snapshot(session, row.id, commit_sha="0" * 40)
        await repo.finalize_snapshot(session, row.id, snap.id, stats={})
        await session.commit()
        return row.id


async def _make_user(login: str) -> uuid.UUID:
    async with _session_factory()() as session:
        user = User(login=login, name="Library Test", github_id=None)
        session.add(user)
        await session.flush()
        await session.commit()
        return user.id


async def _is_member(user_id: uuid.UUID, repo_id: uuid.UUID) -> bool:
    async with _session_factory()() as session:
        return await repo.is_repo_member(session, user_id, repo_id)


async def _repo_still_exists(repo_id: uuid.UUID) -> bool:
    async with _session_factory()() as session:
        return (await session.get(Repo, repo_id)) is not None


async def _drop(repo_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    async with _session_factory()() as session:
        await session.execute(delete(UserRepo).where(UserRepo.repo_id == repo_id))
        if user_id is not None:
            await session.execute(delete(User).where(User.id == user_id))
        # repos.active_snapshot_id <-> snapshots.repo_id is a cycle: drop the
        # active pointer, then snapshots, then the repo (FK-safe).
        await session.execute(
            update(Repo).where(Repo.id == repo_id).values(active_snapshot_id=None)
        )
        await session.execute(delete(Snapshot).where(Snapshot.repo_id == repo_id))
        await session.execute(delete(Repo).where(Repo.id == repo_id))
        await session.commit()


def test_library_add_list_remove_roundtrip() -> None:
    url = f"https://github.com/test/lib-{uuid.uuid4().hex[:8]}.git"
    login = f"lib-{uuid.uuid4().hex[:8]}"
    repo_id = asyncio.run(_seed_indexed_repo(url))
    user_id = asyncio.run(_make_user(login))
    try:
        # add -> membership exists, output confirms it.
        res = runner.invoke(app, ["library", "add", url, "--user", login])
        assert res.exit_code == 0, res.output
        assert "added to" in res.output
        assert asyncio.run(_is_member(user_id, repo_id)) is True

        # add again is idempotent and says so.
        res = runner.invoke(app, ["library", "add", url, "--user", login])
        assert res.exit_code == 0, res.output
        assert "already in" in res.output

        # list shows the repo for that user.
        res = runner.invoke(app, ["library", "list", "--user", login])
        assert res.exit_code == 0, res.output
        assert url in res.output

        # remove -> membership gone; the repo row (shared index) survives.
        res = runner.invoke(app, ["library", "remove", url, "--user", login])
        assert res.exit_code == 0, res.output
        assert "Removed" in res.output
        assert asyncio.run(_is_member(user_id, repo_id)) is False
        assert asyncio.run(_repo_still_exists(repo_id)) is True

        # remove again is a no-op, not an error.
        res = runner.invoke(app, ["library", "remove", url, "--user", login])
        assert res.exit_code == 0, res.output
        assert "was not in" in res.output
    finally:
        asyncio.run(_drop(repo_id, user_id))


def test_library_add_unknown_user_errors() -> None:
    url = f"https://github.com/test/lib-{uuid.uuid4().hex[:8]}.git"
    repo_id = asyncio.run(_seed_indexed_repo(url))
    try:
        res = runner.invoke(app, ["library", "add", url, "--user", "definitely-no-such-user"])
        assert res.exit_code == 1
        assert "sign in with GitHub first" in res.output
    finally:
        asyncio.run(_drop(repo_id, None))


def test_library_add_unindexed_repo_errors() -> None:
    res = runner.invoke(app, ["library", "add", "https://github.com/no/such-repo.git"])
    assert res.exit_code == 1
    assert "No repository matches" in res.output
