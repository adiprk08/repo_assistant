"""Clone ref handling: branch/tag vs commit SHA (git commands mocked, no network)."""

import pytest

from repo_assistant.core.errors import IngestionError
from repo_assistant.ingestion import git
from repo_assistant.ingestion.git import _looks_like_sha, clone, normalize_github_url

_SHA = "b67832c2167e5b0ff6764a8c04a0a9087e697b5a"


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (_SHA, True),
        (_SHA.upper(), True),
        ("a" * 64, True),  # SHA-256
        ("main", False),
        ("v8.1.0", False),
        (_SHA[:12], False),  # short SHA is not a full object name
        ("feature/abc123", False),
    ],
)
def test_looks_like_sha(ref: str, expected: bool) -> None:
    assert _looks_like_sha(ref) == expected


def test_normalize_github_url_rejects_non_github() -> None:
    with pytest.raises(IngestionError):
        normalize_github_url("https://evil.example.com/x/y")


@pytest.fixture
def git_calls(monkeypatch) -> list[list[str]]:
    """Record git argv lists and return canned output for rev-parse."""
    calls: list[list[str]] = []

    async def fake_run_git(*args: str, cwd: str | None = None) -> str:
        calls.append(list(args))
        if args[:1] == ("rev-parse",) and "HEAD" in args and "--abbrev-ref" not in args:
            return f"{_SHA}\n"
        if args[:1] == ("rev-parse",):
            return "main\n"
        return ""

    monkeypatch.setattr(git, "_run_git", fake_run_git)
    return calls


async def test_clone_default_branch_when_no_ref(git_calls) -> None:
    await clone("https://github.com/pallets/click", "/tmp/x")
    clone_cmd = git_calls[0]
    assert clone_cmd[0] == "clone"
    assert "--branch" not in clone_cmd
    assert "--no-checkout" not in clone_cmd


async def test_clone_uses_branch_for_named_ref(git_calls) -> None:
    await clone("https://github.com/pallets/click", "/tmp/x", ref="v8.1.0")
    clone_cmd = git_calls[0]
    assert "--branch" in clone_cmd
    assert clone_cmd[clone_cmd.index("--branch") + 1] == "v8.1.0"
    # No separate fetch/checkout for a named ref.
    assert not any(c[:1] == ["fetch"] for c in git_calls)


async def test_clone_fetches_and_checks_out_commit_sha(git_calls) -> None:
    acq = await clone("https://github.com/pallets/click", "/tmp/x", ref=_SHA)

    kinds = [c[0] for c in git_calls]
    assert kinds[0] == "clone"
    assert "--no-checkout" in git_calls[0]  # don't check out the default branch first
    assert "--branch" not in git_calls[0]  # a SHA can't be a --branch target
    assert "fetch" in kinds and "checkout" in kinds
    fetch = next(c for c in git_calls if c[0] == "fetch")
    assert fetch[-2:] == ["origin", _SHA]
    checkout = next(c for c in git_calls if c[0] == "checkout")
    assert checkout[-1] == _SHA
    # The resolved acquisition is pinned to the requested commit.
    assert acq.commit_sha == _SHA
    assert acq.ref == _SHA
