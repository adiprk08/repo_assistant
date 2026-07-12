"""Incremental re-index against the real Qdrant + Postgres stack (fake embedder).

Full-indexes a repo, mutates the working tree, then updates — asserting only the
changed files are reprocessed, unchanged files (rows + Qdrant points) are carried
forward, deletions drop, and the new snapshot is promoted. Skipped without the stack.
"""

import subprocess
from pathlib import Path

from repo_assistant.core.fakes import FakeEmbedder
from repo_assistant.indexing.incremental import update_working_tree
from repo_assistant.indexing.pipeline import index_working_tree
from repo_assistant.ingestion.models import Acquisition
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.models import Repo
from tests.integration.conftest import requires_stack

pytestmark = requires_stack


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _mutate(acq: Acquisition) -> Acquisition:
    """Edit one file, add one, delete one, and commit — returning the new acquisition."""
    root = acq.root_path
    (Path(root) / "src" / "util.py").write_text(
        "def slugify(text):\n    return text.strip().lower()\n", encoding="utf-8"
    )
    (Path(root) / "src" / "extra.py").write_text("def added():\n    return 42\n", encoding="utf-8")
    _git(root, "rm", "-q", "src/service.py")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "mutate")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return Acquisition(url=acq.url, ref=acq.ref, commit_sha=sha, root_path=root)


async def test_incremental_update_touches_only_changed_files(
    local_repo, qdrant_index, session_factory
) -> None:
    embedder = FakeEmbedder(dimensions=32)
    first = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )

    new_acq = _mutate(local_repo)
    result = await update_working_tree(
        new_acq, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )

    assert not result.no_op
    assert result.snapshot_id != first.snapshot_id
    assert result.n_reprocessed >= 2  # util.py edited + extra.py added
    assert result.n_deleted >= 1  # service.py removed
    assert result.n_unchanged >= 1  # README.md, util? -> at least README carried

    async with session_factory() as session:
        repo_row = await session.get(Repo, first.repo_id)
        assert repo_row is not None
        assert repo_row.active_snapshot_id == result.snapshot_id  # promoted

        files = await repo.file_hashes_for_snapshot(session, result.snapshot_id)
        assert "src/extra.py" in files  # added
        assert "src/service.py" not in files  # deleted
        assert "README.md" in files  # unchanged, carried forward

        chunks = await repo.chunks_for_snapshot(session, result.snapshot_id)
        paths = {c.file_path for c in chunks}
        assert "README.md" in paths
        assert "src/service.py" not in paths
        readme_ids = [str(c.id) for c in chunks if c.file_path == "README.md"]

    # The unchanged file's Qdrant points were copied to the new snapshot ids, with
    # the payload commit patched to the new commit (no re-embedding).
    assert readme_ids
    fetched = await qdrant_index.fetch(repo_id=str(first.repo_id), ids=readme_ids)
    assert len(fetched) == len(readme_ids)
    assert all(f.payload["commit"] == new_acq.commit_sha for f in fetched)
    assert all(f.payload["text"] for f in fetched)


async def test_incremental_no_op_when_commit_unchanged(
    local_repo, qdrant_index, session_factory
) -> None:
    embedder = FakeEmbedder(dimensions=32)
    first = await index_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )
    # Same commit sha -> nothing to do, no new snapshot.
    result = await update_working_tree(
        local_repo, embedder=embedder, vector_index=qdrant_index, session_factory=session_factory
    )
    assert result.no_op
    assert result.snapshot_id == first.snapshot_id
