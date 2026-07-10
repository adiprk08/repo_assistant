"""Exhaustive checks on the pure exclusion-policy predicates."""

import pytest

from repo_assistant.ingestion import filters


@pytest.mark.parametrize(
    "path",
    [
        "node_modules/react/index.js",
        "src/vendor/lib.py",
        "a/b/__pycache__/mod.cpython-312.pyc",
        "dist/bundle.js",
        ".venv/lib/site-packages/x.py",
    ],
)
def test_in_excluded_dir_true(path: str) -> None:
    assert filters.in_excluded_dir(path)


@pytest.mark.parametrize(
    "path",
    ["src/app.py", "README.md", "pkg/module/file.ts", "deep/nested/real/code.go"],
)
def test_in_excluded_dir_false(path: str) -> None:
    assert not filters.in_excluded_dir(path)


@pytest.mark.parametrize(
    "path",
    ["uv.lock", "package-lock.json", "app.min.js", "styles.min.css", "types.d.ts", "a/b/go.sum"],
)
def test_is_generated_file_true(path: str) -> None:
    assert filters.is_generated_file(path)


@pytest.mark.parametrize("path", ["src/app.py", "main.js", "index.ts"])
def test_is_generated_file_false(path: str) -> None:
    assert not filters.is_generated_file(path)


@pytest.mark.parametrize(
    "path",
    [".env", ".env.production", "config/.env.local", "secrets/id_rsa", "certs/server.pem", "a.key"],
)
def test_looks_like_secret_file_true(path: str) -> None:
    assert filters.looks_like_secret_file(path)


@pytest.mark.parametrize(
    "path",
    [".env.example", ".env.sample", ".env.template", "src/app.py", "keys.py"],
)
def test_looks_like_secret_file_false(path: str) -> None:
    # .env.example and friends are safe config documentation and must be kept.
    assert not filters.looks_like_secret_file(path)


def test_looks_binary_detects_nul_byte() -> None:
    assert filters.looks_binary(b"\x89PNG\r\n\x00\x1a")


def test_looks_binary_allows_utf8_text() -> None:
    assert not filters.looks_binary("def foo():\n    return 'héllo'\n".encode())


def test_looks_binary_empty_is_not_binary() -> None:
    assert not filters.looks_binary(b"")
