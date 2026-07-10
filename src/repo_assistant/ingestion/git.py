"""Git acquisition: clone or update a repository at a pinned commit.

We use a blobless partial clone (``--filter=blob:none``) so large histories don't
cost bandwidth we don't need — Phase 1 only reads the checked-out tree, not
history. The resolved commit SHA is recorded so every downstream artifact can be
pinned to it (docs/adr/0009-multitenancy-and-versioning.md).
"""

import asyncio
import re
from pathlib import PurePosixPath

from repo_assistant.core.errors import IngestionError
from repo_assistant.core.logging import get_logger
from repo_assistant.ingestion.models import Acquisition

logger = get_logger(__name__)

_GITHUB_URL = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


def normalize_github_url(url: str) -> str:
    """Validate a GitHub URL and return its canonical https clone form.

    Rejecting anything that isn't a well-formed GitHub repo URL is a security
    boundary: the value is passed to ``git`` as untrusted input.
    """
    match = _GITHUB_URL.match(url.strip())
    if not match:
        raise IngestionError(f"Not a valid GitHub repository URL: {url!r}")
    return f"https://github.com/{match['owner']}/{match['repo']}.git"


async def _run_git(*args: str, cwd: str | None = None) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise IngestionError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{stderr.decode('utf-8', 'replace').strip()}"
        )
    return stdout.decode("utf-8", "replace")


async def clone(url: str, dest: str, ref: str | None = None) -> Acquisition:
    """Blobless-clone ``url`` into ``dest`` and check out ``ref`` (or the default branch)."""
    clone_url = normalize_github_url(url)
    args = ["clone", "--filter=blob:none", "--quiet"]
    if ref:
        args += ["--branch", ref]
    args += [clone_url, dest]

    logger.info("cloning repository", url=clone_url, ref=ref, dest=dest)
    await _run_git(*args)

    commit_sha = (await _run_git("rev-parse", "HEAD", cwd=dest)).strip()
    resolved_ref = ref or (await _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=dest)).strip()
    logger.info("clone complete", commit_sha=commit_sha, ref=resolved_ref)

    return Acquisition(url=clone_url, ref=resolved_ref, commit_sha=commit_sha, root_path=dest)


async def list_tracked_files(root: str) -> list[str]:
    """Return repo-relative POSIX paths of all tracked files.

    Using ``git ls-files`` means .gitignore is honored for free (ignored files are
    simply never tracked), and we get stable forward-slash paths on every OS.
    """
    out = await _run_git("ls-files", "-z", cwd=root)
    return [str(PurePosixPath(p)) for p in out.split("\0") if p]
