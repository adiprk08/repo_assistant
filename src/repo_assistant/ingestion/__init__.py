"""Repository ingestion: acquire a working tree and scan it into an indexable file set."""

from repo_assistant.ingestion.git import clone, list_tracked_files, normalize_github_url
from repo_assistant.ingestion.languages import classify, detect_language
from repo_assistant.ingestion.models import (
    Acquisition,
    FileCategory,
    ScannedFile,
    ScanResult,
    SkippedFile,
    SkipReason,
)
from repo_assistant.ingestion.scanner import scan

__all__ = [
    "Acquisition",
    "FileCategory",
    "ScanResult",
    "ScannedFile",
    "SkipReason",
    "SkippedFile",
    "classify",
    "clone",
    "detect_language",
    "list_tracked_files",
    "normalize_github_url",
    "scan",
]
