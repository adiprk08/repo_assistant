"""Scan an acquired working tree into an indexable file set.

Applies the exclusion policy (filters.py) and classification (languages.py) to the
set of tracked files, reading each once to compute size, a binary check, and a
content hash. The content hash is what lets re-indexing skip unchanged files and
lets embeddings be cached (docs/ARCHITECTURE.md §4).
"""

import hashlib
from pathlib import Path

from repo_assistant.core.logging import get_logger
from repo_assistant.ingestion import filters
from repo_assistant.ingestion.git import list_tracked_files
from repo_assistant.ingestion.languages import classify
from repo_assistant.ingestion.models import (
    Acquisition,
    ScannedFile,
    ScanResult,
    SkippedFile,
    SkipReason,
)

logger = get_logger(__name__)

# Bytes read from the head of each file for the binary heuristic.
_BINARY_SNIFF_BYTES = 8192


def _classify_skip(rel_path: str) -> SkipReason | None:
    """Path-only exclusion checks (no file read required). None means 'keep so far'."""
    if filters.in_excluded_dir(rel_path):
        return SkipReason.VENDORED
    if filters.looks_like_secret_file(rel_path):
        return SkipReason.SECRET
    if filters.is_generated_file(rel_path):
        return SkipReason.GENERATED
    return None


def _escapes_root(abs_path: Path, root: Path) -> bool:
    """True if ``abs_path`` is a symlink or otherwise resolves outside ``root``.

    Repository content is untrusted: a tracked symlink with an innocuous name
    (``notes.md -> /etc/passwd``) would otherwise be *followed* by the read below,
    pulling host files into the index. Only regular files inside the clone are
    indexable, so the link is refused rather than resolved.

    Sync (not async) so the filesystem probes stay off the event loop's hot path
    and out of the async-lint surface; callers are I/O-bound already.
    """
    if abs_path.is_symlink():
        return True
    try:
        return not abs_path.resolve().is_relative_to(root.resolve())
    except OSError:  # unresolvable path (broken link chain, permission, cycle)
        return True


async def scan(acquisition: Acquisition) -> ScanResult:
    """Produce the set of files to index (and a reasoned list of what was skipped)."""
    root = Path(acquisition.root_path)
    result = ScanResult()
    total_bytes = 0

    for rel_path in await list_tracked_files(acquisition.root_path):
        skip_reason = _classify_skip(rel_path)
        if skip_reason is not None:
            result.skipped.append(SkippedFile(path=rel_path, reason=skip_reason))
            continue

        abs_path = root / rel_path
        # Refuse anything that would read outside the clone (docs/adr/0024).
        if _escapes_root(abs_path, root):
            result.skipped.append(SkippedFile(path=rel_path, reason=SkipReason.SYMLINK))
            continue
        try:
            raw = abs_path.read_bytes()
        except (OSError, ValueError):
            # Broken symlink, unreadable mode, or path we cannot open: skip, don't fail.
            result.skipped.append(SkippedFile(path=rel_path, reason=SkipReason.IGNORED))
            continue

        if not raw:
            result.skipped.append(SkippedFile(path=rel_path, reason=SkipReason.EMPTY))
            continue
        if len(raw) > filters.MAX_FILE_BYTES:
            result.skipped.append(SkippedFile(path=rel_path, reason=SkipReason.TOO_LARGE))
            continue
        if filters.looks_binary(raw[:_BINARY_SNIFF_BYTES]):
            result.skipped.append(SkippedFile(path=rel_path, reason=SkipReason.BINARY))
            continue
        # Content-level secret scan: keep a file with an inlined credential out of
        # the index even if its name looks innocuous (docs/adr/0021).
        if filters.contains_secret(raw.decode("utf-8", "replace")):
            result.skipped.append(SkippedFile(path=rel_path, reason=SkipReason.SECRET))
            continue

        # Whole-repo ceilings: past either one, keep recording *why* files were
        # dropped but stop growing the indexable set (docs/adr/0024).
        if (
            len(result.files) >= filters.MAX_REPO_FILES
            or total_bytes + len(raw) > filters.MAX_REPO_BYTES
        ):
            result.skipped.append(SkippedFile(path=rel_path, reason=SkipReason.REPO_LIMIT))
            continue
        total_bytes += len(raw)

        language, category = classify(rel_path)
        result.files.append(
            ScannedFile(
                path=rel_path,
                language=language,
                category=category,
                size_bytes=len(raw),
                content_hash=hashlib.sha256(raw).hexdigest(),
            )
        )

    logger.info(
        "scan complete",
        commit_sha=acquisition.commit_sha,
        kept=len(result.files),
        bytes=total_bytes,
        skipped=len(result.skipped),
    )
    return result
