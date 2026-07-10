"""Scanner behavior over a real (temporary) git repository.

Uses a throwaway `git init` tree rather than mocks so we exercise the actual
`git ls-files` path — including .gitignore honoring — the way production will.
"""

import subprocess
from pathlib import Path

from repo_assistant.ingestion.models import Acquisition, FileCategory, SkipReason
from repo_assistant.ingestion.scanner import scan


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(root: Path, files: dict[str, bytes], gitignore: str | None = None) -> Acquisition:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    if gitignore is not None:
        (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "test")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    return Acquisition(
        url="https://github.com/x/y.git", ref="main", commit_sha=sha, root_path=str(root)
    )


async def test_scan_keeps_source_and_docs(tmp_path: Path) -> None:
    acq = _init_repo(
        tmp_path,
        {
            "src/app.py": b"def main():\n    return 1\n",
            "web/index.ts": b"export const x = 1;\n",
            "README.md": b"# Title\n",
        },
    )
    result = await scan(acq)

    kept = {f.path: f for f in result.files}
    assert set(kept) == {"src/app.py", "web/index.ts", "README.md"}
    assert kept["src/app.py"].language == "python"
    assert kept["src/app.py"].category is FileCategory.CODE
    assert kept["README.md"].category is FileCategory.DOC
    assert all(f.content_hash for f in result.files)


async def test_scan_excludes_by_policy(tmp_path: Path) -> None:
    acq = _init_repo(
        tmp_path,
        {
            "src/app.py": b"x = 1\n",
            "node_modules/dep/index.js": b"module.exports = {}\n",
            "app.min.js": b"var a=1;\n",
            ".env": b"SECRET=hunter2\n",
            ".env.example": b"SECRET=\n",
            "logo.png": b"\x89PNG\r\n\x00\x1a\n",
            "empty.py": b"",
            "uv.lock": b"# lock\n",
        },
    )
    result = await scan(acq)

    kept = {f.path for f in result.files}
    assert kept == {"src/app.py", ".env.example"}

    reasons = {s.path: s.reason for s in result.skipped}
    assert reasons["node_modules/dep/index.js"] is SkipReason.VENDORED
    assert reasons["app.min.js"] is SkipReason.GENERATED
    assert reasons[".env"] is SkipReason.SECRET
    assert reasons["logo.png"] is SkipReason.BINARY
    assert reasons["empty.py"] is SkipReason.EMPTY
    assert reasons["uv.lock"] is SkipReason.GENERATED


async def test_scan_honors_gitignore(tmp_path: Path) -> None:
    acq = _init_repo(
        tmp_path,
        {"src/app.py": b"x = 1\n", "build_artifact.txt": b"generated\n"},
        gitignore="build_artifact.txt\n",
    )
    result = await scan(acq)

    paths = {f.path for f in result.files}
    assert "src/app.py" in paths
    # Ignored (untracked) files never reach the scanner because git ls-files omits them.
    assert "build_artifact.txt" not in paths
    assert all(s.path != "build_artifact.txt" for s in result.skipped)


async def test_scan_skips_oversized_files(tmp_path: Path) -> None:
    oversized = b"x = 1  # pad\n" * 100_000  # comfortably over MAX_FILE_BYTES
    acq = _init_repo(tmp_path, {"big.py": oversized, "small.py": b"x = 1\n"})
    result = await scan(acq)

    assert {f.path for f in result.files} == {"small.py"}
    assert any(s.path == "big.py" and s.reason is SkipReason.TOO_LARGE for s in result.skipped)
