"""Scale-validation harness (docs/SCALE.md, ROADMAP Phase 5).

Generates a synthetic repo of N source files, indexes it against the real
Postgres + Qdrant stack with the *fake* embedder (zero API cost, so this measures
the pipeline's structural throughput — scan/parse/chunk/DB/Qdrant — not embedding
latency), then makes an incremental update touching K files and confirms the work
scales with the diff, not the repo.

Usage:
    uv run python scripts/scale_bench.py --files 2000 --change 20
"""

import argparse
import asyncio
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from repo_assistant.core.config import get_settings
from repo_assistant.core.fakes import FakeEmbedder
from repo_assistant.indexing.incremental import update_working_tree
from repo_assistant.indexing.pipeline import index_working_tree
from repo_assistant.indexing.qdrant_index import QdrantVectorIndex
from repo_assistant.ingestion.models import Acquisition
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.db import make_engine, make_session_factory

_FILE_TEMPLATE = '''\
"""Module {i}."""


class Widget{i}:
    """A generated class for scale testing."""

    def __init__(self, value: int) -> None:
        self.value = value

    def scaled(self, factor: int) -> int:
        """Return the value scaled by ``factor``."""
        return self.value * factor + {i}


def helper_{i}(x: int) -> int:
    """Top-level helper number {i}."""
    return x + {i}
'''


def _git(cwd: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write_files(root: Path, indices: range | list[int]) -> None:
    for i in indices:
        path = root / f"pkg{i // 100}" / f"module_{i}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_FILE_TEMPLATE.format(i=i), encoding="utf-8")


def _commit(root: str, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


async def run(n_files: int, n_change: int) -> None:
    engine = make_engine()
    session_factory = make_session_factory(engine)
    embedder = FakeEmbedder(dimensions=32)
    collection = f"bench_{uuid.uuid4().hex[:8]}"
    index = QdrantVectorIndex.from_url(get_settings().qdrant_url, collection=collection)

    with tempfile.TemporaryDirectory() as tmp:
        _git(tmp, "init", "-q")
        _git(tmp, "config", "user.email", "b@e.com")
        _git(tmp, "config", "user.name", "bench")
        print(f"generating {n_files} files ...")
        _write_files(Path(tmp), range(n_files))
        sha = _commit(tmp, "init")
        url = f"https://github.com/bench/repo-{uuid.uuid4().hex[:6]}.git"
        acq = Acquisition(url=url, ref="main", commit_sha=sha, root_path=tmp)

        try:
            t0 = time.perf_counter()
            result = await index_working_tree(
                acq, embedder=embedder, vector_index=index, session_factory=session_factory
            )
            full = time.perf_counter() - t0
            print("\n=== FULL INDEX ===")
            print(f"  files:            {result.n_files}")
            print(f"  chunks:           {result.n_chunks}")
            print(f"  symbols:          {result.n_symbols}")
            print(f"  wall time:        {full:.1f}s")
            print(
                f"  throughput:       {result.n_files / full:.0f} files/s, "
                f"{result.n_chunks / full:.0f} chunks/s"
            )

            # Incremental: genuinely modify the first K files (append a unique line
            # so the content hash actually changes), then commit and update.
            for i in range(n_change):
                path = Path(tmp) / f"pkg{i // 100}" / f"module_{i}.py"
                path.write_text(
                    _FILE_TEMPLATE.format(i=i) + f"\n# edit {uuid.uuid4().hex}\n", encoding="utf-8"
                )
            new_sha = _commit(tmp, "change")
            acq2 = Acquisition(url=url, ref="main", commit_sha=new_sha, root_path=tmp)
            t1 = time.perf_counter()
            upd = await update_working_tree(
                acq2, embedder=embedder, vector_index=index, session_factory=session_factory
            )
            inc = time.perf_counter() - t1
            print("\n=== INCREMENTAL UPDATE ===")
            print(f"  reprocessed:      {upd.n_reprocessed}")
            print(f"  unchanged copied: {upd.n_unchanged}")
            print(f"  wall time:        {inc:.1f}s  ({full / inc:.1f}x faster than full)")
            print(f"  proportionality:  reprocessed {upd.n_reprocessed} of {result.n_files} files")
        finally:
            await index._client.delete_collection(collection)
            await index._client.close()
            async with session_factory() as session:
                await repo.delete_repo_rows(session, result.repo_id)
                await session.commit()
            await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=2000)
    parser.add_argument("--change", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(run(args.files, args.change))


if __name__ == "__main__":
    main()
