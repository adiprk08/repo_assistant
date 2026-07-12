"""Unit tests for the incremental-update plan and webhook signature check."""

import hashlib
import hmac

from repo_assistant.api.routers.webhooks import _valid_signature
from repo_assistant.indexing.incremental import plan_update
from repo_assistant.ingestion.models import FileCategory, ScannedFile


def _sf(path: str, content_hash: str) -> ScannedFile:
    return ScannedFile(
        path=path,
        language=None,
        category=FileCategory.TEXT,
        size_bytes=1,
        content_hash=content_hash,
    )


def test_plan_partitions_by_content_hash() -> None:
    prev = {"a.py": "h1", "b.py": "h2", "gone.py": "h3"}
    scanned = [_sf("a.py", "h1"), _sf("b.py", "CHANGED"), _sf("new.py", "h4")]

    plan = plan_update(scanned, prev)

    assert plan.unchanged == ["a.py"]
    assert {f.path for f in plan.to_process} == {"b.py", "new.py"}  # changed + added
    assert plan.deleted == ["gone.py"]


def test_plan_all_unchanged() -> None:
    prev = {"a.py": "h1", "b.py": "h2"}
    plan = plan_update([_sf("a.py", "h1"), _sf("b.py", "h2")], prev)
    assert set(plan.unchanged) == {"a.py", "b.py"}
    assert plan.to_process == []
    assert plan.deleted == []


def test_valid_signature() -> None:
    secret, body = "s3cr3t", b'{"ref":"refs/heads/main"}'
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert _valid_signature(secret, body, good)
    assert not _valid_signature(secret, body, "sha256=deadbeef")
    assert not _valid_signature(secret, body, None)
    assert not _valid_signature(secret, body, "not-prefixed")
    assert not _valid_signature(secret, b"tampered", good)  # body mismatch
