"""Language detection and file classification."""

import pytest

from repo_assistant.ingestion.languages import classify, detect_language
from repo_assistant.ingestion.models import FileCategory


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/app.py", "python"),
        ("lib/types.pyi", "python"),
        ("web/index.js", "javascript"),
        ("web/component.jsx", "javascript"),
        ("web/module.mjs", "javascript"),
        ("api/server.ts", "typescript"),
        ("ui/App.tsx", "tsx"),
        ("cmd/main.go", "go"),
        ("src/Main.java", "java"),
        ("src/lib.rs", "rust"),
        ("README.md", None),
        ("config.yaml", None),
        ("Makefile", None),
    ],
)
def test_detect_language(path: str, expected: str | None) -> None:
    assert detect_language(path) == expected


@pytest.mark.parametrize(
    ("path", "language", "category"),
    [
        ("src/app.py", "python", FileCategory.CODE),
        ("ui/App.tsx", "tsx", FileCategory.CODE),
        ("docs/guide.md", None, FileCategory.DOC),
        ("README", None, FileCategory.DOC),
        ("LICENSE", None, FileCategory.DOC),
        ("pyproject.toml", None, FileCategory.CONFIG),
        ("settings.json", None, FileCategory.CONFIG),
        ("Makefile", None, FileCategory.TEXT),
        ("scripts/deploy.sh", None, FileCategory.TEXT),
    ],
)
def test_classify(path: str, language: str | None, category: FileCategory) -> None:
    assert classify(path) == (language, category)
