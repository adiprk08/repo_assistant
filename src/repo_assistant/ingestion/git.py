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

# A full git object name: 40 hex (SHA-1) or 64 hex (SHA-256). Such a ref can't be
# a `git clone --branch` target, so it needs a fetch + checkout instead.
_COMMIT_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")

# Wall-clock ceiling for a network git operation (clone/fetch). A hostile or merely
# huge remote must not be able to pin a worker forever (docs/adr/0024).
CLONE_TIMEOUT_SECONDS = 300.0
# Local, non-network git calls (rev-parse, ls-files) are fast; bound them tightly.
_LOCAL_TIMEOUT_SECONDS = 60.0


def _looks_like_sha(ref: str) -> bool:
    return _COMMIT_SHA.fullmatch(ref) is not None


def normalize_github_url(url: str) -> str:
    """Validate a GitHub URL and return its canonical https clone form.

    Rejecting anything that isn't a well-formed GitHub repo URL is a security
    boundary: the value is passed to ``git`` as untrusted input.
    """
    match = _GITHUB_URL.match(url.strip())
    if not match:
        raise IngestionError(f"Not a valid GitHub repository URL: {url!r}")
    return f"https://github.com/{match['owner']}/{match['repo']}.git"


# Hardening flags applied to every git invocation (docs/adr/0024):
#   core.symlinks=false  - never materialize a tracked symlink as a real link, so
#                          nothing downstream can be tricked into following one out
#                          of the clone. Git writes the link target as file text.
#   protocol.file.allow / submodule ... - a repo cannot drag in content from an
#                          arbitrary or local transport during clone/checkout.
_HARDENING = (
    "-c",
    "core.symlinks=false",
    "-c",
    "protocol.file.allow=never",
    "-c",
    "submodule.recurse=false",
)


async def _run_git(
    *args: str,
    cwd: str | None = None,
    timeout: float | None = None,  # noqa: ASYNC109 - deadline must reach the subprocess kill
) -> str:
    """Run git with the hardening flags, bounded by ``timeout`` seconds.

    A hostile remote can otherwise stall a clone indefinitely and pin a worker
    (docs/adr/0024); the process is killed and reported as an IngestionError.

    ASYNC109 suggests the caller wrap this in ``asyncio.timeout()`` instead, but a
    cancelled task would leave the git child process alive — the timeout has to be
    handled here so the process is actually reaped.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *_HARDENING,
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise IngestionError(
            f"git {args[0] if args else ''} timed out after {timeout:.0f}s"
        ) from None
    if proc.returncode != 0:
        raise IngestionError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{stderr.decode('utf-8', 'replace').strip()}"
        )
    return stdout.decode("utf-8", "replace")


def _authenticated_url(clone_url: str, token: str | None) -> str:
    """Embed a GitHub App installation token for a private clone (docs/adr/0020).

    Uses GitHub's documented ``x-access-token:<token>@`` form. The result contains a
    secret, so it is never logged — only the sanitized ``clone_url`` is.
    """
    if not token:
        return clone_url
    return clone_url.replace("https://", f"https://x-access-token:{token}@", 1)


async def clone(
    url: str,
    dest: str,
    ref: str | None = None,
    *,
    token: str | None = None,
    timeout: float = CLONE_TIMEOUT_SECONDS,  # noqa: ASYNC109 - forwarded to _run_git
) -> Acquisition:
    """Blobless-clone ``url`` into ``dest`` and check out ``ref`` (or the default branch).

    ``ref`` may be a branch name, a tag, or a full commit SHA. A branch/tag is a
    ``--branch`` target on the clone; a SHA cannot be (``--branch`` only accepts
    named refs), so it is fetched explicitly and checked out — the object may not
    be present after a blobless partial clone if it is off the default branch.

    ``token`` is a GitHub App installation token for private repos; it is injected
    into the remote URL and never logged.
    """
    clone_url = normalize_github_url(url)
    logger.info("cloning repository", url=clone_url, ref=ref, dest=dest, private=bool(token))
    remote = _authenticated_url(clone_url, token)

    if ref and _looks_like_sha(ref):
        await _run_git(
            "clone",
            "--filter=blob:none",
            "--quiet",
            "--no-checkout",
            remote,
            dest,
            timeout=timeout,
        )
        await _run_git(
            "fetch", "--filter=blob:none", "--quiet", "origin", ref, cwd=dest, timeout=timeout
        )
        await _run_git("checkout", "--quiet", ref, cwd=dest, timeout=timeout)
    else:
        args = ["clone", "--filter=blob:none", "--quiet"]
        if ref:
            args += ["--branch", ref]
        args += [remote, dest]
        await _run_git(*args, timeout=timeout)

    commit_sha = (
        await _run_git("rev-parse", "HEAD", cwd=dest, timeout=_LOCAL_TIMEOUT_SECONDS)
    ).strip()
    resolved_ref = (
        ref
        or (
            await _run_git(
                "rev-parse", "--abbrev-ref", "HEAD", cwd=dest, timeout=_LOCAL_TIMEOUT_SECONDS
            )
        ).strip()
    )
    logger.info("clone complete", commit_sha=commit_sha, ref=resolved_ref)

    return Acquisition(url=clone_url, ref=resolved_ref, commit_sha=commit_sha, root_path=dest)


async def list_tracked_files(root: str) -> list[str]:
    """Return repo-relative POSIX paths of all tracked files.

    Using ``git ls-files`` means .gitignore is honored for free (ignored files are
    simply never tracked), and we get stable forward-slash paths on every OS.
    """
    out = await _run_git("ls-files", "-z", cwd=root, timeout=_LOCAL_TIMEOUT_SECONDS)
    return [str(PurePosixPath(p)) for p in out.split("\0") if p]
